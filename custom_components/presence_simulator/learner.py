"""Pure learning and planning logic for Presence Simulator.

No Home Assistant imports live here, so this module can be unit tested with
plain pytest (see tests/test_learner.py).

Terminology
-----------
* change   - one recorded state row for one entity.
* scene    - a full snapshot of every tracked entity at a point in the day,
             stored as {"t": <seconds since local midnight>, "states": {...}}.
* day      - the ordered list of scenes learned from one calendar date.
* plan     - a day's scenes with jitter applied, ready to schedule.
"""
from __future__ import annotations

import copy
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

SECONDS_PER_DAY = 86_400
LAST_SECOND = SECONDS_PER_DAY - 1

UNUSABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})
OFF_STATES = frozenset({"off", "closed"})

# Attributes worth replaying, per domain. Only kept while the entity is "on".
# Home Assistant's reproduce_state picks the right colour attribute from color_mode.
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, str):
        return str(value)  # strips StrEnum subclasses
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


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


def learn(
    changes: Iterable[Change],
    windows: list[tuple[str, int, datetime, datetime]],
    merge_seconds: int,
    tz: tzinfo,
) -> dict[str, dict]:
    """Turn recorded changes into a list of scenes per day.

    windows: ordered (day_key, weekday, start, end) tuples, contiguous or not.
    Changes that start within ``merge_seconds`` of the first change in a
    cluster are merged into one scene, timed at the start of the cluster.
    Each day begins with a baseline scene at 00:00:00 describing the state
    carried in from the previous day.
    """
    ordered = sorted(changes, key=lambda c: c.when)
    snapshot: dict[str, dict] = {}
    idx = 0
    result: dict[str, dict] = {}

    def apply(change: Change) -> None:
        norm = normalise(change.entity_id, change.state, change.attributes)
        if norm is not None:  # keep last known good state through "unavailable"
            snapshot[change.entity_id] = norm

    for day_key, weekday, start, end in windows:
        while idx < len(ordered) and ordered[idx].when <= start:
            apply(ordered[idx])
            idx += 1
        # Skip anything between the previous window's end and this start.
        scenes: list[dict] = []
        if snapshot:
            scenes.append({"t": 0, "states": copy.deepcopy(snapshot)})
        last_emitted = copy.deepcopy(snapshot)
        cluster_start: datetime | None = None

        def emit() -> None:
            nonlocal last_emitted
            if cluster_start is None or snapshot == last_emitted:
                return
            offset = wall_offset(cluster_start, tz)
            if scenes and offset <= scenes[-1]["t"]:
                offset = scenes[-1]["t"] + 1
            if offset > LAST_SECOND:
                return
            scenes.append({"t": offset, "states": copy.deepcopy(snapshot)})
            last_emitted = copy.deepcopy(snapshot)

        while idx < len(ordered) and ordered[idx].when < end:
            change = ordered[idx]
            if cluster_start is None:
                cluster_start = change.when
            elif (change.when - cluster_start).total_seconds() > merge_seconds:
                emit()
                cluster_start = change.when
            apply(change)
            idx += 1
        emit()

        result[day_key] = {"weekday": weekday, "scenes": scenes}
    return result


def pick_day(days: Mapping[str, dict], target: date, mode: str, rng: random.Random) -> str | None:
    """Choose which learned day to replay on ``target``."""
    keys = sorted(k for k, v in days.items() if v.get("scenes"))
    if not keys:
        return None
    if mode == "weekday":
        same = [k for k in keys if days[k]["weekday"] == target.weekday()]
        if same:
            return same[(target.toordinal() // 7) % len(same)]
        mode = "sequential"
    if mode == "random":
        return rng.choice(keys)
    return keys[target.toordinal() % len(keys)]


def build_plan(scenes: list[dict], jitter_seconds: int, rng: random.Random) -> list[tuple[int, dict]]:
    """Apply random jitter while preserving scene order.

    Returns [(offset, scene), ...] strictly increasing by offset. The 00:00:00
    baseline is never jittered.
    """
    plan: list[tuple[int, dict]] = []
    prev = -1
    for scene in scenes:
        offset = scene["t"]
        if offset > 0:
            if jitter_seconds > 0:
                offset += rng.randint(-jitter_seconds, jitter_seconds)
            offset = min(max(offset, 1), LAST_SECOND)
        offset = max(offset, prev + 1)
        if offset > LAST_SECOND:
            break
        plan.append((offset, scene))
        prev = offset
    return plan
