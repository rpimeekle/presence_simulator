"""Unit tests for YAML item generation and edit-preserving merge."""
from _load import generator

ENTRY = {"day_key": "2026-09-14", "t": 64800, "shift": 0, "weekdays": [0, 2],
         "max_delay": 0, "states": {"light.a": {"state": "on", "attributes": {"brightness": 50}}}}


def test_ids_are_stable_and_unique():
    assert generator.scene_id("p", ENTRY) == "p_20260914_64800"
    assert generator.scene_id("p", {**ENTRY, "shift": 1}) == "p_20260914_151200"
    assert generator.automation_id("p", ENTRY) == "p_20260914_64800_auto"


def test_scene_and_automation_shape():
    scene = generator.build_scene("s1", "Lounge", ENTRY["states"])
    assert scene["entities"] == {"light.a": {"state": "on", "brightness": 50}}
    auto = generator.build_automation("a1", "s1", "Lounge", ENTRY, "presence_simulator")
    assert auto["triggers"] == [{"trigger": "time", "at": "18:00:00"}]
    assert auto["conditions"] == [{"condition": "time", "weekday": ["mon", "wed"]}]
    assert auto["actions"] == [{"action": "presence_simulator.activate_scene", "data": {"scene_id": "s1"}}]
    every_day = generator.build_automation("a1", "s1", "L", {**ENTRY, "weekdays": list(range(7)), "max_delay": 120}, "d")
    assert every_day["conditions"] == []
    assert "range(0, 121)" in every_day["actions"][0]["delay"]["seconds"]


def test_merge_replaces_untouched_keeps_edited_and_foreign():
    old = {"id": "g1", "name": "old"}
    edited = {"id": "g2", "name": "user changed this"}
    foreign = {"id": "mine", "name": "not generated"}
    previous = {"g1": generator.item_hash(old), "g2": generator.item_hash({"id": "g2", "name": "orig"})}
    new = [{"id": "g1", "name": "new"}, {"id": "g2", "name": "regenerated"}, {"id": "g3", "name": "fresh"}]

    merged, remember, kept = generator.merge([foreign, old, edited], previous, new)
    assert {i["id"]: i["name"] for i in merged} == {
        "mine": "not generated", "g1": "new", "g2": "user changed this", "g3": "fresh"}
    assert kept == {"g2"}
    assert remember["g1"] == generator.item_hash({"id": "g1", "name": "new"})
    assert remember["g2"] == previous["g2"]

    merged, _, kept = generator.merge([foreign, old, edited], previous, new, discard_edits=True)
    assert {i["id"]: i["name"] for i in merged}["g2"] == "regenerated" and not kept


def test_merge_drops_stale_generated_items():
    stale = {"id": "g9", "name": "x"}
    merged, remember, _ = generator.merge([stale], {"g9": generator.item_hash(stale)}, [])
    assert merged == [] and remember == {}


def test_merge_never_clobbers_user_item_with_same_id():
    user = {"id": "g1", "name": "user made"}
    merged, remember, kept = generator.merge([user], {}, [{"id": "g1", "name": "ours"}])
    assert merged == [user] and "g1" not in remember and kept == {"g1"}
