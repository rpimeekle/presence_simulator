"""Unit tests for the HA-free learning / planning logic."""
from __future__ import annotations

import importlib.util
import pathlib
import random
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "presence_simulator" / "learner.py"
_spec = importlib.util.spec_from_file_location("ps_learner", _PATH)
learner = importlib.util.module_from_spec(_spec)
sys.modules["ps_learner"] = learner
_spec.loader.exec_module(learner)

TZ = ZoneInfo("Australia/Brisbane")
DAY = date(2026, 9, 14)  # a Monday
START = datetime(2026, 9, 14, tzinfo=TZ)
END = START + timedelta(days=1)
WINDOW = [(DAY.isoformat(), DAY.weekday(), START, END)]


def at(h, m, s=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ)


def ch(when, eid, state, **attrs):
    return learner.Change(when, eid, state, attrs)


def test_normalise_media_player_and_unavailable():
    assert learner.normalise("media_player.tv", "playing", {})["state"] == "on"
    assert learner.normalise("media_player.tv", "standby", {})["state"] == "off"
    assert learner.normalise("light.lamp", "unavailable", {}) is None


def test_normalise_light_keeps_attrs_only_when_on():
    on = learner.normalise("light.lamp", "on", {"brightness": 128, "hs_color": (30.0, 50.0), "friendly_name": "x"})
    assert on == {"state": "on", "attributes": {"brightness": 128, "hs_color": [30.0, 50.0]}}
    off = learner.normalise("light.lamp", "off", {"brightness": 128})
    assert off == {"state": "off", "attributes": {}}


def test_baseline_from_state_before_day_start():
    changes = [ch(START - timedelta(hours=3), "light.lamp", "on", brightness=10)]
    day = learner.learn(changes, WINDOW, 60, TZ)[DAY.isoformat()]
    assert day["weekday"] == 0
    assert day["scenes"] == [{"t": 0, "states": {"light.lamp": {"state": "on", "attributes": {"brightness": 10}}}}]


def test_changes_within_merge_window_become_one_scene():
    changes = [
        ch(START - timedelta(hours=1), "light.a", "off"),
        ch(START - timedelta(hours=1), "light.b", "off"),
        ch(at(18, 0, 0), "light.a", "on", brightness=200),
        ch(at(18, 0, 40), "light.b", "on", brightness=100),
        ch(at(22, 30, 0), "light.a", "off"),
        ch(at(22, 30, 5), "light.b", "off"),
    ]
    scenes = learner.learn(changes, WINDOW, 60, TZ)[DAY.isoformat()]["scenes"]
    assert [s["t"] for s in scenes] == [0, 18 * 3600, 22 * 3600 + 30 * 60]
    assert scenes[1]["states"]["light.a"]["state"] == "on"
    assert scenes[1]["states"]["light.b"]["state"] == "on"
    assert all(v["state"] == "off" for v in scenes[2]["states"].values())


def test_noop_changes_do_not_create_scenes():
    changes = [
        ch(START - timedelta(hours=1), "switch.fan", "off"),
        ch(at(9, 0), "switch.fan", "unavailable"),  # blip, ignored
        ch(at(9, 5), "switch.fan", "off", friendly_name="Fan"),  # attr noise
    ]
    scenes = learner.learn(changes, WINDOW, 60, TZ)[DAY.isoformat()]["scenes"]
    assert [s["t"] for s in scenes] == [0]


def test_multi_day_carries_state_across_midnight():
    day2 = DAY + timedelta(days=1)
    windows = WINDOW + [(day2.isoformat(), day2.weekday(), END, END + timedelta(days=1))]
    changes = [ch(at(23, 50), "light.a", "on", brightness=5)]
    days = learner.learn(changes, windows, 60, TZ)
    assert days[day2.isoformat()]["scenes"][0]["states"]["light.a"]["state"] == "on"
    assert days[day2.isoformat()]["weekday"] == 1


def test_build_plan_preserves_order_and_baseline():
    scenes = [{"t": 0, "states": {}}] + [{"t": 3600 + i, "states": {}} for i in range(5)]
    plan = learner.build_plan(scenes, 600, random.Random(1))
    offsets = [o for o, _ in plan]
    assert offsets[0] == 0
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)
    assert [s["t"] for _, s in plan] == [s["t"] for s in scenes]


def test_build_plan_clamps_to_day():
    plan = learner.build_plan([{"t": 86_390, "states": {}}], 600, random.Random(3))
    assert all(1 <= o <= 86_399 for o, _ in plan)


def test_pick_day_prefers_weekday_then_falls_back():
    days = {
        "2026-09-14": {"weekday": 0, "scenes": [{"t": 0}]},
        "2026-09-15": {"weekday": 1, "scenes": [{"t": 0}]},
        "2026-09-16": {"weekday": 2, "scenes": []},
    }
    rng = random.Random(0)
    assert learner.pick_day(days, date(2026, 9, 21), "weekday", rng) == "2026-09-14"  # Monday
    assert learner.pick_day(days, date(2026, 9, 23), "weekday", rng) in ("2026-09-14", "2026-09-15")
    assert learner.pick_day({}, date(2026, 9, 21), "weekday", rng) is None


def test_format_offset():
    assert learner.format_offset(0) == "00:00:00"
    assert learner.format_offset(18 * 3600 + 5 * 60 + 9) == "18:05:09"
