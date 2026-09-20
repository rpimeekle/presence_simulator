"""Core engine: learns scenes from recorder history and replays them."""
from __future__ import annotations

import hashlib
import json
import logging
import random
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

from . import learner
from .const import (
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_JITTER_MINUTES,
    CONF_MERGE_SECONDS,
    CONF_REPLAY_MODE,
    CONF_RESTORE_ON_STOP,
    CONF_SOURCE_DATE,
    CONF_SOURCE_DAYS,
    DEFAULT_DOMAINS,
    DEFAULT_OPTIONS,
    DOMAIN,
    LEARN_KEYS,
    STATUS_IDLE,
    STATUS_LEARNING,
    STATUS_NO_DATA,
    STATUS_RUNNING,
    STORAGE_VERSION,
    WEEKDAY_NAMES,
)

_LOGGER = logging.getLogger(__name__)

type PresenceSimConfigEntry = ConfigEntry[PresenceSimulator]


class LearnError(Exception):
    """Raised when learning cannot proceed."""


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


class PresenceSimulator:
    """Owns learned data, the replay schedule and the on/off state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.data: dict[str, Any] = {}

        self.learning = False
        self.last_error: str | None = None
        self.next_run: datetime | None = None
        self.next_label: str | None = None
        self.last_applied: datetime | None = None
        self.last_label: str | None = None

        self._plans: dict[date, tuple[str | None, list[tuple[int, dict]]]] = {}
        self._pending: tuple[str, dict, datetime] | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._learn_listeners: list[Callable[[], None]] = []

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
    def scene_count(self) -> int:
        return sum(len(d["scenes"]) for d in self.days.values())

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

    def label(self, day_key: str, offset: int) -> str:
        weekday = self.days.get(day_key, {}).get("weekday")
        prefix = f"{WEEKDAY_NAMES[weekday]} " if weekday is not None else ""
        return f"{prefix}{day_key} {learner.format_offset(offset)}"

    # -------------------------------------------------------------- listeners
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    @callback
    def async_add_learn_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._learn_listeners.append(cb)

        def remove() -> None:
            if cb in self._learn_listeners:
                self._learn_listeners.remove(cb)

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
        """Defer learning / resuming until Home Assistant has fully started."""
        self._unsub_started = async_at_started(self.hass, self._async_on_started)

    async def _async_on_started(self, _hass: HomeAssistant) -> None:
        self._unsub_started = None
        if self.needs_learning:
            self.entry.async_create_background_task(
                self.hass, self.async_learn(), f"{DOMAIN}_learn_{self.entry.entry_id}"
            )
        elif self.enabled:
            await self._async_resume(catch_up=True)

    async def async_shutdown(self) -> None:
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None
        self._cancel_timer()

    # --------------------------------------------------------------- learning
    async def async_learn(self) -> None:
        """Read the recorder for the configured window and build scenes."""
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

            tz = dt_util.get_default_time_zone()
            first = date.fromisoformat(str(opts[CONF_SOURCE_DATE]))
            now = dt_util.now()
            windows: list[tuple[str, int, datetime, datetime]] = []
            for i in range(int(opts[CONF_SOURCE_DAYS])):
                day = first + timedelta(days=i)
                start = datetime.combine(day, time.min, tz)
                if start >= now:
                    break
                end = min(datetime.combine(day + timedelta(days=1), time.min, tz), now)
                windows.append((day.isoformat(), day.weekday(), start, end))
            if not windows:
                raise LearnError("The source date is in the future")

            raw = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    windows[0][2],
                    windows[-1][3],
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
            days = learner.learn(changes, windows, int(opts[CONF_MERGE_SECONDS]), tz)

            self.data.update(
                {
                    "fingerprint": self._fingerprint(),
                    "learned_at": now.isoformat(),
                    "entities": entity_ids,
                    "days": days,
                }
            )
            await self._store.async_save(self.data)
            if not self.scene_count:
                self.last_error = (
                    "No recorded history for the selected period "
                    "(check the recorder's purge_keep_days and any recorder excludes)"
                )
            _LOGGER.info(
                "%s: learned %d scenes over %d day(s) from %d entities",
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

        self._plans.clear()
        for cb in list(self._learn_listeners):
            cb()
        self._notify()
        if self.enabled:
            await self._async_resume(catch_up=True)

    # ------------------------------------------------------------- enable/off
    async def async_set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        if enabled:
            self.data["restore"] = self._snapshot_current()
            self.data["enabled"] = True
            await self._store.async_save(self.data)
            await self._async_resume(catch_up=True)
        else:
            self._cancel_timer()
            self.next_run = None
            self.next_label = None
            restore = self.data.pop("restore", None)
            self.data["enabled"] = False
            await self._store.async_save(self.data)
            if restore and self.options[CONF_RESTORE_ON_STOP]:
                await self._async_reproduce(restore)
        self._notify()

    def _snapshot_current(self) -> dict[str, dict]:
        snap: dict[str, dict] = {}
        for entity_id in self.entities:
            if (st := self.hass.states.get(entity_id)) and (
                norm := learner.normalise(entity_id, st.state, st.attributes)
            ):
                snap[entity_id] = norm
        return snap

    # ------------------------------------------------------------- scheduling
    def _at(self, day: date, offset: int) -> datetime:
        return datetime(
            day.year, day.month, day.day,
            offset // 3600, (offset % 3600) // 60, offset % 60,
            tzinfo=dt_util.get_default_time_zone(),
        )

    def _plan_for(self, day: date) -> tuple[str | None, list[tuple[int, dict]]]:
        if day not in self._plans:
            opts = self.options
            # Seeded per entry + date so a restart mid-day reproduces the same plan.
            rng = random.Random(f"{self.entry.entry_id}:{day.isoformat()}")
            source = learner.pick_day(self.days, day, opts[CONF_REPLAY_MODE], rng)
            plan = (
                learner.build_plan(
                    self.days[source]["scenes"], int(opts[CONF_JITTER_MINUTES]) * 60, rng
                )
                if source
                else []
            )
            self._plans[day] = (source, plan)
            for old in [d for d in self._plans if d < day - timedelta(days=1)]:
                del self._plans[old]
        return self._plans[day]

    def _current_scene(self, now: datetime) -> tuple[str, dict] | None:
        day = now.date()
        source, plan = self._plan_for(day)
        latest = None
        for offset, scene in plan:
            if self._at(day, offset) > now:
                break
            latest = scene
        return (source, latest) if source and latest else None

    async def _async_resume(self, catch_up: bool) -> None:
        self._cancel_timer()
        if not self.enabled:
            return
        now = dt_util.now()
        if catch_up and (current := self._current_scene(now)):
            source, scene = current
            await self._async_apply(scene["states"], self.label(source, scene["t"]))
        self._schedule_next(now)

    @callback
    def _schedule_next(self, now: datetime) -> None:
        self._cancel_timer()
        for delta in (0, 1):
            day = now.date() + timedelta(days=delta)
            source, plan = self._plan_for(day)
            for offset, scene in plan:
                when = self._at(day, offset)
                if when > now and source:
                    self._pending = (source, scene, when)
                    self.next_run = when
                    self.next_label = self.label(source, scene["t"])
                    self._unsub_timer = async_track_point_in_time(
                        self.hass, self._handle_timer, when
                    )
                    self._notify()
                    return
        self.next_run = None
        self.next_label = None
        self._notify()

    @callback
    def _handle_timer(self, _now: datetime) -> None:
        self._unsub_timer = None
        if not self._pending or not self.enabled:
            return
        source, scene, when = self._pending
        self._pending = None
        self.entry.async_create_background_task(
            self.hass, self._async_fire(source, scene, when), f"{DOMAIN}_fire"
        )

    async def _async_fire(self, source: str, scene: dict, when: datetime) -> None:
        await self._async_apply(scene["states"], self.label(source, scene["t"]))
        if self.enabled:
            self._schedule_next(max(dt_util.now(), when))

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        self._pending = None

    # ---------------------------------------------------------------- applying
    async def async_apply_scene(self, day_key: str, offset: int) -> None:
        """Apply one learned scene on demand (used by scene entities)."""
        day = self.days.get(day_key)
        scene = next((s for s in day["scenes"] if s["t"] == offset), None) if day else None
        if scene is None:
            _LOGGER.warning("%s: scene %s %s no longer exists", self.entry.title, day_key, offset)
            return
        await self._async_apply(scene["states"], self.label(day_key, offset))

    async def _async_apply(self, states: dict[str, dict], label: str) -> None:
        await self._async_reproduce(states)
        self.last_applied = dt_util.now()
        self.last_label = label
        _LOGGER.debug("%s: applied scene %s", self.entry.title, label)
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
        except Exception:  # noqa: BLE001 - one bad device must not stop the schedule
            _LOGGER.exception("%s: failed to reproduce scene", self.entry.title)
