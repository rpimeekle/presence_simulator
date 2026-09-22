"""Pure learning and scheduling logic for Presence Simulator.

No Home Assistant imports live here, so this module can be unit tested with
plain pytest (see tests/test_learner.py).

Terminology
-----------
* window   - the part of a calendar day that is learned, e.g. 16:00 -> 23:30.
             A window may cross midnight (e.g. 18:00 -> 01:00).
* scene    - a full snapshot of every tracked entity at one point in the window:
             {"t": <wall-clock seconds since midnight>, "shift": 0|1, "states": {...}}
             ``shift`` is 1 when the event happened after midnight of the learned day.
* schedule - which scenes run on which weekdays, at their original time of day.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

SECONDS_PER_DAY = 86_400
LAST_SECOND = SECONDS_PER_DAY - 1

UNUSABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})
OFF_STATES = frozenset({"off", "closed"})

# Attributes worth replaying, per domain. Only kept while the entity is "on".
# Home Assistant picks the right colour attribute from color_mode when applying.
REPRODUCIBLE_ATTRS: dict[str, tuple[str, ...]] = {
    "light": (
        "brightness",
        "color_mode",
        "color_temp_kelvin",
        "hs_color",
        "xy_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
    ),
    "fan": ("percentage", "preset_mode", "oscillating", "direction"),
    "cover": ("current_position", "current_tilt_position"),
}


@dataclass(frozen=True, slots=True)
class Change:
    """A single recorded state for an entity."""

    when: datetime  # timezone aware
    entity_id: str
    state: str | None
    attributes: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Window:
    """The learned part of one calendar day."""

    key: str  # ISO date of the learned day
    weekday: int  # 0 = Monday
    start: datetime
    end: datetime
    end_scene: bool = False  # add an "everything off" scene at the end


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, str):
        return str(value)  # strips StrEnum subclasses
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def off_state(entity_id: str) -> str:
    return "closed" if entity_id.startswith("cover.") else "off"


def normalise(entity_id: str, state: str | None, attributes: Mapping[str, Any]) -> dict | None:
    """Reduce a raw state to something replayable, or None if it is unusable."""
    if state is None or state.lower() in UNUSABLE_STATES:
        return None
    domain = entity_id.split(".", 1)[0]

    if domain == "media_player":
        # We can't replay *what* was playing, only that the device was on.
        state = "off" if state in ("off", "standby") else "on"
    elif domain == "cover":
        state = {"opening": "open", "closing": "closed"}.get(state, state)

    attrs: dict[str, Any] = {}
    if state not in OFF_STATES:
        for key in REPRODUCIBLE_ATTRS.get(domain, ()):
            value = attributes.get(key)
            if value is not None:
                attrs[key] = _jsonable(value)
    return {"state": state, "attributes": attrs}


def wall_offset(when: datetime, tz: tzinfo) -> int:
    """Seconds since local midnight on the wall clock (DST safe)."""
    local = when.astimezone(tz)
    return min(local.hour * 3600 + local.minute * 60 + local.second, LAST_SECOND)


def format_offset(offset: int) -> str:
    return f"{offset // 3600:02d}:{(offset % 3600) // 60:02d}:{offset % 60:02d}"


def scene_abs(scene: Mapping[str, Any]) -> int:
    """Seconds from the learned day's midnight (can exceed one day)."""
    return scene.get("shift", 0) * SECONDS_PER_DAY + scene["t"]


def learn(
    changes: Iterable[Change],
    windows: list[Window],
    merge_seconds: int,
    tz: tzinfo,
) -> dict[str, dict]:
    """Turn recorded changes into a list of scenes per learned day.

    Changes that start within ``merge_seconds`` of the first change in a
    cluster are merged into one scene, timed at the start of the cluster.
    Each window begins with a baseline scene describing the state at the
    window's start time.
    """
    ordered = sorted(changes, key=lambda c: c.when)
    snapshot: dict[str, dict] = {}
    idx = 0
    result: dict[str, dict] = {}

    def apply(change: Change) -> None:
        norm = normalise(change.entity_id, change.state, change.attributes)
        if norm is not None:  # keep last known good state through "unavailable"
            snapshot[change.entity_id] = norm

    for win in windows:
        day = date.fromisoformat(win.key)
        # Anything before the window start only contributes to the baseline.
        while idx < len(ordered) and ordered[idx].when <= win.start:
            apply(ordered[idx])
            idx += 1

        scenes: list[dict] = []
        last_emitted: dict[str, dict] | None = None

        def emit(when: datetime, states: dict[str, dict]) -> None:
            nonlocal last_emitted
            if not states or states == last_emitted:
                return
            local = when.astimezone(tz)
            shift = min(max((local.date() - day).days, 0), 1)
            absolute = shift * SECONDS_PER_DAY + wall_offset(when, tz)
            if scenes and absolute <= scene_abs(scenes[-1]):
                absolute = scene_abs(scenes[-1]) + 1
            shift, t = divmod(absolute, SECONDS_PER_DAY)
            if shift > 1:
                return
            scenes.append({"t": t, "shift": shift, "states": copy.deepcopy(states)})
            last_emitted = copy.deepcopy(states)

        emit(win.start, snapshot)  # baseline

        cluster_start: datetime | None = None
        while idx < len(ordered) and ordered[idx].when < win.end:
            change = ordered[idx]
            if cluster_start is None:
                cluster_start = change.when
            elif (change.when - cluster_start).total_seconds() > merge_seconds:
                emit(cluster_start, snapshot)
                cluster_start = change.when
            apply(change)
            idx += 1
        if cluster_start is not None:
            emit(cluster_start, snapshot)

        if win.end_scene and snapshot:
            emit(win.end, {eid: {"state": off_state(eid), "attributes": {}} for eid in snapshot})

        result[win.key] = {"weekday": win.weekday, "scenes": scenes}
    return result


def weekday_sources(days: Mapping[str, dict]) -> dict[int, str]:
    """Map each weekday (0=Mon) to the learned day that should be replayed on it.

    A learned day plays on its own weekday (the most recent one wins if several
    share a weekday). Weekdays with no learned counterpart cycle through the
    learned days, so a single learned day plays every day.
    """
    keys = sorted(k for k, v in days.items() if v.get("scenes"))
    if not keys:
        return {}
    mapping: dict[int, str] = {}
    for weekday in range(7):
        same = [k for k in keys if days[k]["weekday"] == weekday]
        mapping[weekday] = same[-1] if same else keys[weekday % len(keys)]
    return mapping


def schedule(days: Mapping[str, dict], jitter_seconds: int = 0) -> list[dict]:
    """Build one schedule entry per scene that will actually be replayed.

    Each entry fires at the scene's original wall-clock time on ``weekdays``.
    ``max_delay`` is the optional random delay, capped so a scene can never be
    pushed past the next one (which would replay events out of order).
    """
    sources = weekday_sources(days)
    entries: list[dict] = []
    for key in sorted(days):
        weekdays = sorted(w for w, k in sources.items() if k == key)
        if not weekdays:
            continue
        scenes = days[key]["scenes"]
        for i, scene in enumerate(scenes):
            gap = scene_abs(scenes[i + 1]) - scene_abs(scene) - 1 if i + 1 < len(scenes) else jitter_seconds
            entries.append(
                {
                    "day_key": key,
                    "t": scene["t"],
                    "shift": scene.get("shift", 0),
                    "weekdays": sorted((w + scene.get("shift", 0)) % 7 for w in weekdays),
                    "max_delay": max(0, min(jitter_seconds, gap)),
                    "states": scene["states"],
                }
            )
    return entries
