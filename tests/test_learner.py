"""Unit tests for the HA-free learning / scheduling logic."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from _load import learner

TZ = ZoneInfo("Australia/Brisbane")
DAY = date(2026, 9, 14)  # a Monday


def at(h, m, s=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ)


def win(start, end, day=DAY, end_scene=False):
    return learner.Window(day.isoformat(), day.weekday(), start, end, end_scene)


FULL = [win(at(0, 0), at(0, 0) + timedelta(days=1))]


def ch(when, eid, state, **attrs):
    return learner.Change(when, eid, state, attrs)


def scenes_of(result, day=DAY):
    return result[day.isoformat()]["scenes"]


def test_normalise_media_player_and_unavailable():
    assert learner.normalise("media_player.tv", "playing", {})["state"] == "on"
    assert learner.normalise("media_player.tv", "standby", {})["state"] == "off"
    assert learner.normalise("light.lamp", "unavailable", {}) is None


def test_normalise_light_keeps_attrs_only_when_on():
    on = learner.normalise("light.lamp", "on", {"brightness": 128, "hs_color": (30.0, 50.0), "friendly_name": "x"})
    assert on == {"state": "on", "attributes": {"brightness": 128, "hs_color": [30.0, 50.0]}}
    assert learner.normalise("light.lamp", "off", {"brightness": 128}) == {"state": "off", "attributes": {}}


def test_full_day_baseline_and_merge_window():
    changes = [
        ch(at(0, 0) - timedelta(hours=1), "light.a", "off"),
        ch(at(0, 0) - timedelta(hours=1), "light.b", "off"),
        ch(at(18, 0, 0), "light.a", "on", brightness=200),
        ch(at(18, 0, 40), "light.b", "on", brightness=100),
        ch(at(22, 30, 0), "light.a", "off"),
        ch(at(22, 30, 5), "light.b", "off"),
    ]
    scenes = scenes_of(learner.learn(changes, FULL, 60, TZ))
    assert [(s["t"], s["shift"]) for s in scenes] == [(0, 0), (64800, 0), (81000, 0)]
    assert scenes[1]["states"]["light.b"]["state"] == "on"


def test_window_limits_learning_and_sets_baseline_at_start():
    changes = [
        ch(at(7, 0), "light.a", "on"),  # before window -> only affects baseline
        ch(at(12, 0), "light.a", "off"),  # before window
        ch(at(17, 30), "light.a", "on"),
        ch(at(23, 45), "light.a", "off"),  # after window -> ignored
    ]
    scenes = scenes_of(learner.learn(changes, [win(at(16, 0), at(23, 0))], 60, TZ))
    assert [s["t"] for s in scenes] == [16 * 3600, 17 * 3600 + 1800]
    assert scenes[0]["states"]["light.a"]["state"] == "off"


def test_window_crossing_midnight_marks_shift():
    changes = [
        ch(at(17, 0), "light.a", "off"),
        ch(at(19, 0), "light.a", "on"),
        ch(at(0, 30, day=DAY + timedelta(days=1)), "light.a", "off"),
    ]
    scenes = scenes_of(learner.learn(changes, [win(at(18, 0), at(18, 0) + timedelta(hours=7))], 60, TZ))
    assert [(s["t"], s["shift"]) for s in scenes] == [(64800, 0), (68400, 0), (1800, 1)]


def test_end_scene_turns_everything_off():
    changes = [ch(at(15, 0), "light.a", "off"), ch(at(15, 0), "cover.blind", "closed"),
               ch(at(19, 0), "light.a", "on", brightness=9), ch(at(19, 0), "cover.blind", "open", current_position=80)]
    scenes = scenes_of(learner.learn(changes, [win(at(16, 0), at(23, 0), end_scene=True)], 60, TZ))
    assert scenes[-1]["t"] == 23 * 3600
    assert scenes[-1]["states"] == {"light.a": {"state": "off", "attributes": {}},
                                    "cover.blind": {"state": "closed", "attributes": {}}}


def test_noop_changes_do_not_create_scenes():
    changes = [
        ch(at(0, 0) - timedelta(hours=1), "switch.fan", "off"),
        ch(at(9, 0), "switch.fan", "unavailable"),
        ch(at(9, 5), "switch.fan", "off", friendly_name="Fan"),
    ]
    assert [s["t"] for s in scenes_of(learner.learn(changes, FULL, 60, TZ))] == [0]


def test_weekday_sources_single_day_covers_week():
    days = {"2026-09-14": {"weekday": 0, "scenes": [{"t": 0}]}}
    assert learner.weekday_sources(days) == {w: "2026-09-14" for w in range(7)}


def test_weekday_sources_prefers_matching_weekday():
    days = {
        "2026-09-14": {"weekday": 0, "scenes": [{"t": 0}]},
        "2026-09-15": {"weekday": 1, "scenes": [{"t": 0}]},
        "2026-09-21": {"weekday": 0, "scenes": [{"t": 0}]},  # newer Monday wins
    }
    src = learner.weekday_sources(days)
    assert src[0] == "2026-09-21" and src[1] == "2026-09-15"


def test_schedule_shifts_weekday_after_midnight_and_caps_delay():
    days = {"2026-09-14": {"weekday": 0, "scenes": [
        {"t": 64800, "shift": 0, "states": {}},
        {"t": 64900, "shift": 0, "states": {}},
        {"t": 1800, "shift": 1, "states": {}},
    ]}, "2026-09-15": {"weekday": 1, "scenes": [{"t": 64800, "shift": 0, "states": {}}]}}
    entries = learner.schedule(days, jitter_seconds=600)
    mon = [e for e in entries if e["day_key"] == "2026-09-14"]
    # Monday's learned day plays on Mon + the 5 uncovered weekdays alternating... check its own weekday
    assert 0 in mon[0]["weekdays"]
    assert mon[2]["weekdays"] == sorted((w + 1) % 7 for w in mon[0]["weekdays"])
    assert mon[0]["max_delay"] == 99  # next scene is 100 s later
    assert mon[2]["max_delay"] == 600


def test_format_offset():
    assert learner.format_offset(18 * 3600 + 5 * 60 + 9) == "18:05:09"
