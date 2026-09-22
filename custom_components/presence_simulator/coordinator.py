"""Core engine: learns from recorder history, writes editable scenes and
automations to scenes.yaml / automations.yaml, and switches them on and off."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.state import async_reproduce_state
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import generator, learner, yaml_io
from .const import (
    AUTOMATIONS_FILE,
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_JITTER_MINUTES,
    CONF_MERGE_SECONDS,
    CONF_OFF_AT_END,
    CONF_RESTORE_ON_STOP,
    CONF_SOURCE_DATE,
    CONF_SOURCE_DAYS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_DOMAINS,
    DEFAULT_OPTIONS,
    DOMAIN,
    LEARN_KEYS,
    SCENES_FILE,
    STATUS_IDLE,
    STATUS_LEARNING,
    STATUS_NO_DATA,
    STATUS_RUNNING,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

type PresenceSimConfigEntry = ConfigEntry[PresenceSimulator]

# Entity registry platforms for YAML scenes / automations (unique_id == YAML id)
SCENE_PLATFORM = "homeassistant"
AUTOMATION_PLATFORM = "automation"


class LearnError(Exception):
    """Raised when learning cannot proceed."""


def id_prefix(entry_id: str) -> str:
    return f"presence_sim_{entry_id[-8:].lower()}"


def window_bounds(options: dict[str, Any]) -> tuple[time, int]:
    """(start time, duration in seconds). End <= start means it crosses midnight."""
    start = time.fromisoformat(str(options[CONF_WINDOW_START]))
    end = time.fromisoformat(str(options[CONF_WINDOW_END]))
    start_s = start.hour * 3600 + start.minute * 60 + start.second
    end_s = end.hour * 3600 + end.minute * 60 + end.second
    duration = (end_s - start_s) % learner.SECONDS_PER_DAY
    return start, duration or learner.SECONDS_PER_DAY


@callback
def async_resolve_entities(hass: HomeAssistant, options: dict[str, Any]) -> list[str]:
    """Entities in the chosen areas (directly or via their device) and domains."""
    areas = set(options.get(CONF_AREAS) or [])
    domains = set(options.get(CONF_DOMAINS) or DEFAULT_DOMAINS)
    exclude = set(options.get(CONF_EXCLUDE_ENTITIES) or [])
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    found: list[str] = []
    for ent in ent_reg.entities.values():
        if (
            ent.domain not in domains
            or ent.disabled_by is not None
            or ent.entity_category is not None  # skip config/diagnostic switches
            or ent.platform == DOMAIN
            or ent.entity_id in exclude
        ):
            continue
        area_id = ent.area_id
        if area_id is None and ent.device_id and (device := dev_reg.async_get(ent.device_id)):
            area_id = device.area_id
        if area_id in areas:
            found.append(ent.entity_id)
    return sorted(found)


async def async_remove_generated(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Delete every scene/automation this entry generated (used on entry removal)."""
    generated = data.get("generated", {})
    scene_ids = set(generated.get("scenes", {}))
    auto_ids = set(generated.get("automations", {}))
    if not scene_ids and not auto_ids:
        return

    def _work() -> None:
        for filename, ids in ((SCENES_FILE, scene_ids), (AUTOMATIONS_FILE, auto_ids)):
            path = hass.config.path(filename)
            items = yaml_io.read_list(path)
            remaining = generator.remove(items, ids)
            if len(remaining) != len(items):
                yaml_io.write_list(path, remaining)

    await hass.async_add_executor_job(_work)
    await _async_reload(hass)


async def _async_reload(hass: HomeAssistant) -> None:
    for domain in ("scene", "automation"):
        if hass.services.has_service(domain, "reload"):
            await hass.services.async_call(domain, "reload", blocking=True)


class PresenceSimulator:
    """Owns learned data, the generated YAML and the on/off state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.data: dict[str, Any] = {}
        self.prefix = id_prefix(entry.entry_id)

        self.learning = False
        self.last_error: str | None = None
        self.next_run: datetime | None = None
        self.next_label: str | None = None
        self.last_applied: datetime | None = None
        self.last_label: str | None = None

        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ props
    @property
    def options(self) -> dict[str, Any]:
        return {**DEFAULT_OPTIONS, **self.entry.options}

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled"))

    @property
    def days(self) -> dict[str, dict]:
        return self.data.get("days", {})

    @property
    def entities(self) -> list[str]:
        return list(self.data.get("entities", []))

    @property
    def schedule(self) -> list[dict]:
        return self.data.get("schedule", [])

    @property
    def scene_count(self) -> int:
        return len(self.schedule)

    @property
    def scene_ids(self) -> list[str]:
        return sorted(self.data.get("generated", {}).get("scenes", {}))

    @property
    def automation_ids(self) -> list[str]:
        return sorted(self.data.get("generated", {}).get("automations", {}))

    @property
    def edited_ids(self) -> list[str]:
        return sorted(self.data.get("edited", []))

    @property
    def status(self) -> str:
        if self.learning:
            return STATUS_LEARNING
        if not self.scene_count:
            return STATUS_NO_DATA
        return STATUS_RUNNING if self.enabled else STATUS_IDLE

    def _fingerprint(self) -> str:
        opts = self.options
        relevant = {
            k: sorted(opts.get(k) or []) if isinstance(opts.get(k), list) else opts.get(k)
            for k in LEARN_KEYS
        }
        raw = json.dumps(relevant, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    @property
    def needs_learning(self) -> bool:
        return self.data.get("fingerprint") != self._fingerprint()

    def _entry_label(self, entry: dict) -> str:
        return generator.label(self.entry.title, entry, self.days[entry["day_key"]]["weekday"])

    # -------------------------------------------------------------- listeners
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # -------------------------------------------------------------- lifecycle
    async def async_initialize(self) -> None:
        self.data = await self._store.async_load() or {}

    @callback
    def async_start(self) -> None:
        """Defer learning until Home Assistant has fully started."""
        self._unsub_started = async_at_started(self.hass, self._async_on_started)

    async def _async_on_started(self, _hass: HomeAssistant) -> None:
        self._unsub_started = None
        if self.needs_learning:
            self.entry.async_create_background_task(
                self.hass, self.async_learn(), f"{DOMAIN}_learn_{self.entry.entry_id}"
            )
            return
        await self._async_sync_automations()
        self._update_next()

    async def async_shutdown(self) -> None:
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None
        self._cancel_timer()

    # --------------------------------------------------------------- learning
    def _windows(self, now: datetime) -> list[learner.Window]:
        opts = self.options
        tz = dt_util.get_default_time_zone()
        start_time, duration = window_bounds(opts)
        first = date.fromisoformat(str(opts[CONF_SOURCE_DATE]))
        windows: list[learner.Window] = []
        for i in range(int(opts[CONF_SOURCE_DAYS])):
            day = first + timedelta(days=i)
            start = datetime.combine(day, start_time, tz)
            if start >= now:
                break
            end = start + timedelta(seconds=duration)
            complete = end <= now
            windows.append(
                learner.Window(
                    key=day.isoformat(),
                    weekday=day.weekday(),
                    start=start,
                    end=min(end, now),
                    # An "all off" at midnight of a full-day window would fight the next baseline.
                    end_scene=bool(opts[CONF_OFF_AT_END])
                    and complete
                    and duration < learner.SECONDS_PER_DAY,
                )
            )
        return windows

    async def async_learn(self, discard_edits: bool = False) -> None:
        """Read the recorder, build scenes, and (re)generate the YAML."""
        if self.learning:
            return
        self.learning = True
        self.last_error = None
        self._notify()
        try:
            opts = self.options
            entity_ids = async_resolve_entities(self.hass, opts)
            if not entity_ids:
                raise LearnError("No matching entities found in the selected areas")

            now = dt_util.now()
            windows = self._windows(now)
            if not windows:
                raise LearnError("The source date/time is in the future")

            raw = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    windows[0].start,
                    windows[-1].end,
                    entity_ids,
                    include_start_time_state=True,
                    significant_changes_only=False,
                    minimal_response=False,
                    no_attributes=False,
                )
            )
            changes = [
                learner.Change(st.last_updated, entity_id, st.state, dict(st.attributes))
                for entity_id, states in raw.items()
                for st in states
                if isinstance(st, State)
            ]
            days = learner.learn(
                changes, windows, int(opts[CONF_MERGE_SECONDS]), dt_util.get_default_time_zone()
            )
            _, duration = window_bounds(opts)
            self.data.update(
                {
                    "learned_at": now.isoformat(),
                    "entities": entity_ids,
                    "days": days,
                    "window_seconds": duration,
                    "schedule": learner.schedule(days, int(opts[CONF_JITTER_MINUTES]) * 60),
                }
            )
            await self._async_generate(discard_edits)
            self.data["fingerprint"] = self._fingerprint()
            await self._store.async_save(self.data)

            if not self.scene_count:
                self.last_error = (
                    "No recorded history for the selected period "
                    "(check the recorder's purge_keep_days and any recorder excludes)"
                )
            _LOGGER.info(
                "%s: generated %d scenes/automations over %d day(s) from %d entities",
                self.entry.title, self.scene_count, len(days), len(entity_ids),
            )
        except LearnError as err:
            self.last_error = str(err)
            _LOGGER.warning("%s: %s", self.entry.title, err)
        except Exception:  # noqa: BLE001 - surface anything to the user
            self.last_error = "Unexpected error while learning, see the log"
            _LOGGER.exception("%s: learning failed", self.entry.title)
        finally:
            self.learning = False

        await self._async_sync_automations()
        self._update_next()
        self._notify()

    async def _async_generate(self, discard_edits: bool) -> None:
        """Write scenes + automations, keeping anything edited in the frontend."""
        scenes: list[dict] = []
        automations: list[dict] = []
        for entry in self.schedule:
            sid = generator.scene_id(self.prefix, entry)
            name = self._entry_label(entry)
            scenes.append(generator.build_scene(sid, name, entry["states"]))
            automations.append(
                generator.build_automation(
                    generator.automation_id(self.prefix, entry), sid, name, entry, DOMAIN
                )
            )

        previous = self.data.get("generated", {})
        scenes_path = self.hass.config.path(SCENES_FILE)
        autos_path = self.hass.config.path(AUTOMATIONS_FILE)

        def _work() -> tuple[dict, dict, set[str]]:
            s_items, s_hashes, s_kept = generator.merge(
                yaml_io.read_list(scenes_path), previous.get("scenes", {}), scenes, discard_edits
            )
            a_items, a_hashes, a_kept = generator.merge(
                yaml_io.read_list(autos_path), previous.get("automations", {}), automations, discard_edits
            )
            yaml_io.write_list(scenes_path, s_items)
            yaml_io.write_list(autos_path, a_items)
            return s_hashes, a_hashes, s_kept | a_kept

        s_hashes, a_hashes, kept = await self.hass.async_add_executor_job(_work)
        self.data["generated"] = {"scenes": s_hashes, "automations": a_hashes}
        self.data["edited"] = sorted(kept)
        await self._store.async_save(self.data)
        if kept:
            _LOGGER.info("%s: kept %d item(s) edited in the frontend", self.entry.title, len(kept))

        await _async_reload(self.hass)

        ent_reg = er.async_get(self.hass)
        missing = []
        if s_hashes and not any(
            ent_reg.async_get_entity_id("scene", SCENE_PLATFORM, sid) for sid in s_hashes
        ):
            missing.append(f"scene: !include {SCENES_FILE}")
        if a_hashes and not any(
            ent_reg.async_get_entity_id("automation", AUTOMATION_PLATFORM, aid) for aid in a_hashes
        ):
            missing.append(f"automation: !include {AUTOMATIONS_FILE}")
        if missing:
            raise LearnError(
                "Generated items were written but did not load. Add "
                + " and ".join(f"`{m}`" for m in missing)
                + " to configuration.yaml"
            )

    # ------------------------------------------------------------- enable/off
    async def async_set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        if enabled:
            self.data["restore"] = self._snapshot_current()
            self.data["enabled"] = True
            await self._store.async_save(self.data)
            await self._async_sync_automations()
            await self._async_catch_up()
        else:
            self.data["enabled"] = False
            restore = self.data.pop("restore", None)
            await self._store.async_save(self.data)
            await self._async_sync_automations()
            if restore and self.options[CONF_RESTORE_ON_STOP]:
                await self._async_reproduce(restore)
        self._update_next()
        self._notify()

    def _snapshot_current(self) -> dict[str, dict]:
        snap: dict[str, dict] = {}
        for entity_id in self.entities:
            if (st := self.hass.states.get(entity_id)) and (
                norm := learner.normalise(entity_id, st.state, st.attributes)
            ):
                snap[entity_id] = norm
        return snap

    async def _async_sync_automations(self) -> None:
        """Generated automations are on exactly while the simulation is on."""
        ent_reg = er.async_get(self.hass)
        entity_ids = [
            eid
            for aid in self.automation_ids
            if (eid := ent_reg.async_get_entity_id("automation", AUTOMATION_PLATFORM, aid))
        ]
        if not entity_ids or not self.hass.services.has_service("automation", "turn_on"):
            return
        service = "turn_on" if self.enabled else "turn_off"
        wrong = [
            eid for eid in entity_ids
            if (st := self.hass.states.get(eid)) and st.state != ("on" if self.enabled else "off")
        ]
        if wrong:
            await self.hass.services.async_call(
                "automation", service, {"entity_id": wrong}, blocking=True
            )

    # ----------------------------------------------------------- time helpers
    def _at(self, day: date, offset: int) -> datetime:
        return datetime(
            day.year, day.month, day.day,
            offset // 3600, (offset % 3600) // 60, offset % 60,
            tzinfo=dt_util.get_default_time_zone(),
        )

    def _last_occurrence(self, entry: dict, now: datetime) -> datetime | None:
        for back in range(8):
            day = now.date() - timedelta(days=back)
            if day.weekday() in entry["weekdays"] and (when := self._at(day, entry["t"])) <= now:
                return when
        return None

    def _next_occurrence(self, entry: dict, now: datetime) -> datetime | None:
        for ahead in range(8):
            day = now.date() + timedelta(days=ahead)
            if day.weekday() in entry["weekdays"] and (when := self._at(day, entry["t"])) > now:
                return when
        return None

    async def _async_catch_up(self) -> None:
        """On enable, apply the scene that should be active right now (if inside a window)."""
        now = dt_util.now()
        best: tuple[datetime, dict] | None = None
        for entry in self.schedule:
            if (when := self._last_occurrence(entry, now)) and (best is None or when > best[0]):
                best = (when, entry)
        if best is None:
            return
        # Only if we're still inside the learned window that scene belongs to.
        first_of_day = self.days[best[1]["day_key"]]["scenes"][0]
        elapsed = learner.scene_abs(best[1]) - learner.scene_abs(first_of_day)
        window_left = self.data.get("window_seconds", learner.SECONDS_PER_DAY) - elapsed
        if (now - best[0]).total_seconds() < window_left:
            await self.async_activate(generator.scene_id(self.prefix, best[1]))

    @callback
    def _update_next(self) -> None:
        self._cancel_timer()
        self.next_run = None
        self.next_label = None
        if self.enabled:
            now = dt_util.now()
            for entry in self.schedule:
                when = self._next_occurrence(entry, now)
                if when and (self.next_run is None or when < self.next_run):
                    self.next_run = when
                    self.next_label = self._entry_label(entry)
            if self.next_run:
                # Refresh the sensor shortly after that automation should have fired.
                self._unsub_timer = async_track_point_in_time(
                    self.hass, self._handle_timer, self.next_run + timedelta(seconds=1)
                )
        self._notify()

    @callback
    def _handle_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._update_next()

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    # ---------------------------------------------------------------- applying
    def owns_scene(self, sid: str) -> bool:
        return sid in self.data.get("generated", {}).get("scenes", {})

    async def async_activate(self, sid: str, context: Context | None = None) -> None:
        """Turn on the (possibly frontend-edited) YAML scene with this id."""
        entity_id = er.async_get(self.hass).async_get_entity_id("scene", SCENE_PLATFORM, sid)
        if entity_id is None:
            _LOGGER.warning("%s: scene id %s is not loaded", self.entry.title, sid)
            return
        await self.hass.services.async_call(
            "scene", "turn_on", {"entity_id": entity_id}, blocking=True, context=context
        )
        self.async_note_activated(sid)

    @callback
    def async_note_activated(self, sid: str) -> None:
        self.last_applied = dt_util.now()
        state = self.hass.states.get(
            er.async_get(self.hass).async_get_entity_id("scene", SCENE_PLATFORM, sid) or ""
        )
        self.last_label = state.name if state else sid
        self._notify()

    async def _async_reproduce(self, states: dict[str, dict]) -> None:
        targets = [
            State(entity_id, value["state"], value.get("attributes") or {})
            for entity_id, value in states.items()
            if self.hass.states.get(entity_id) is not None
        ]
        if not targets:
            return
        try:
            await async_reproduce_state(self.hass, targets, context=Context())
        except Exception:  # noqa: BLE001
            _LOGGER.exception("%s: failed to restore states", self.entry.title)
