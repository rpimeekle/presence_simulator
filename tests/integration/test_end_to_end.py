"""Record real history, learn a time window, generate YAML scenes + automations,
and let Home Assistant's own automation engine replay them the next day."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant import config as conf_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.presence_simulator.const import (
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_SOURCE_DATE,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_OPTIONS,
    DOMAIN,
)

TZ = ZoneInfo("Australia/Brisbane")
USER_SCENE = {"id": "my_own_scene", "name": "Movie night", "entities": {"input_boolean.lamp": "off"}}


def local(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=TZ)


async def _set(hass: HomeAssistant, freezer, when, **states):
    freezer.move_to(when)
    for obj, on in states.items():
        await hass.services.async_call(
            "input_boolean", "turn_on" if on else "turn_off",
            {"entity_id": f"input_boolean.{obj}"}, blocking=True,
        )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _tick(hass, freezer, when):
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


ENTRY_ID = "test_entry_abcd1234"


def _eid(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{ENTRY_ID}_{key}")


def _yaml(hass, name):
    return load_yaml(hass.config.path(name)) or []


def _state(hass, obj):
    return hass.states.get(f"input_boolean.{obj}").state


async def test_learn_generate_and_replay(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer, tmp_path
) -> None:
    # A real config dir with the default includes and a pre-existing user scene.
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "scene: !include scenes.yaml\nautomation: !include automations.yaml\n"
    )
    (tmp_path / "scenes.yaml").write_text(dump([USER_SCENE]))
    (tmp_path / "automations.yaml").write_text("[]\n")

    await hass.config.async_set_time_zone("Australia/Brisbane")
    freezer.move_to(local(2026, 9, 13, 23, 0))
    config = await conf_util.async_hass_config_yaml(hass)
    assert await async_setup_component(hass, "scene", config)
    assert await async_setup_component(hass, "automation", config)
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"lamp": {}, "tv": {}, "garage": {}}}
    )
    lounge = ar.async_get(hass).async_create("Lounge")
    ent_reg = er.async_get(hass)
    for obj in ("lamp", "tv"):
        ent_reg.async_update_entity(f"input_boolean.{obj}", area_id=lounge.id)

    # Monday 14 Sep history. Only 16:00-23:00 is learned.
    await _set(hass, freezer, local(2026, 9, 13, 23, 0), lamp=False, tv=False, garage=False)
    await _set(hass, freezer, local(2026, 9, 14, 7, 0), lamp=True)        # morning: outside window
    await _set(hass, freezer, local(2026, 9, 14, 8, 0), lamp=False)
    await _set(hass, freezer, local(2026, 9, 14, 18, 0, 0), lamp=True)
    await _set(hass, freezer, local(2026, 9, 14, 18, 0, 40), tv=True)     # merged into 18:00
    await _set(hass, freezer, local(2026, 9, 14, 21, 15, 0), tv=False)
    await _set(hass, freezer, local(2026, 9, 14, 23, 40, 0), lamp=False)  # after window

    await _set(hass, freezer, local(2026, 9, 15, 10, 0))
    entry = MockConfigEntry(
        domain=DOMAIN, title="Lounge sim", entry_id=ENTRY_ID, version=2,
        options={
            **DEFAULT_OPTIONS,
            CONF_AREAS: [lounge.id],
            CONF_DOMAINS: ["input_boolean"],
            CONF_SOURCE_DATE: "2026-09-14",
            CONF_WINDOW_START: "16:00:00",
            CONF_WINDOW_END: "23:00:00",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    sim = entry.runtime_data
    assert sim.last_error is None, sim.last_error

    # 16:00 baseline, 18:00 on, 21:15 tv off, 23:00 everything off (end of window)
    times = [(e["t"], e["weekdays"]) for e in sim.schedule]
    assert times == [(57600, list(range(7))), (64800, list(range(7))),
                     (76500, list(range(7))), (82800, list(range(7)))]

    scenes = {s["id"]: s for s in _yaml(hass, "scenes.yaml")}
    autos = {a["id"]: a for a in _yaml(hass, "automations.yaml")}
    assert "my_own_scene" in scenes  # untouched
    sid = "presence_sim_abcd1234_20260914_64800"
    aid = f"{sid}_auto"
    assert scenes[sid]["entities"] == {"input_boolean.lamp": {"state": "on"}, "input_boolean.tv": {"state": "on"}}
    assert autos[aid]["triggers"] == [{"trigger": "time", "at": "18:00:00"}]
    assert len(scenes) == 5 and len(autos) == 4

    # Loaded as real, editable entities with the YAML id as unique_id.
    scene_eid = ent_reg.async_get_entity_id("scene", "homeassistant", sid)
    auto_eid = ent_reg.async_get_entity_id("automation", "automation", aid)
    assert scene_eid and auto_eid
    assert hass.states.get(scene_eid).attributes["id"] == sid
    assert hass.states.get(auto_eid).state == "off"  # simulation is off

    # Leave at 17:00 with the TV on; enable. Catch-up applies the 16:00 baseline.
    await _set(hass, freezer, local(2026, 9, 15, 17, 0), tv=True)
    switch = _eid(hass, "switch", "simulation")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(auto_eid).state == "on"
    assert _state(hass, "tv") == "off"
    assert sim.next_run == local(2026, 9, 15, 18, 0)

    # 18:00:00 exactly: HA's automation engine fires the generated automation.
    await _tick(hass, freezer, local(2026, 9, 15, 18, 0, 0))
    assert _state(hass, "lamp") == "on" and _state(hass, "tv") == "on"
    assert _state(hass, "garage") == "off"
    await _tick(hass, freezer, local(2026, 9, 15, 18, 0, 2))
    assert sim.next_run == local(2026, 9, 15, 21, 15)
    assert hass.states.get(_eid(hass, "sensor", "last_scene")).attributes["scene"].startswith("Lounge sim Mon")

    await _tick(hass, freezer, local(2026, 9, 15, 21, 15, 0))
    assert _state(hass, "tv") == "off" and _state(hass, "lamp") == "on"
    await _tick(hass, freezer, local(2026, 9, 15, 23, 0, 0))
    assert _state(hass, "lamp") == "off"

    # "Frontend edit": change the 18:00 scene to keep the TV off, reload scenes.
    items = _yaml(hass, "scenes.yaml")
    for item in items:
        if item["id"] == sid:
            item["entities"]["input_boolean.tv"] = {"state": "off"}
    (tmp_path / "scenes.yaml").write_text(dump(items))
    await hass.services.async_call("scene", "reload", blocking=True)

    # Relearn keeps the edit, and the automation uses the edited scene.
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "button", "relearn")}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sim.edited_ids == [sid]
    assert {s["id"]: s for s in _yaml(hass, "scenes.yaml")}[sid]["entities"]["input_boolean.tv"] == {"state": "off"}
    await _tick(hass, freezer, local(2026, 9, 16, 18, 0, 0))
    assert _state(hass, "lamp") == "on" and _state(hass, "tv") == "off"

    # Rebuild discards it.
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "button", "rebuild")}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sim.edited_ids == []
    assert {s["id"]: s for s in _yaml(hass, "scenes.yaml")}[sid]["entities"]["input_boolean.tv"] == {"state": "on"}

    # Switch off: automations disabled, states restored to when it was switched on (TV on).
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(auto_eid).state == "off"
    assert _state(hass, "tv") == "on" and _state(hass, "lamp") == "off"

    # Deleting the integration removes only what it generated.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert [s["id"] for s in _yaml(hass, "scenes.yaml")] == ["my_own_scene"]
    assert _yaml(hass, "automations.yaml") == []


async def test_missing_include_is_reported(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    await hass.config.async_set_time_zone("Australia/Brisbane")
    freezer.move_to(local(2026, 9, 14, 17, 0))
    config = await conf_util.async_hass_config_yaml(hass)
    assert await async_setup_component(hass, "scene", config)
    assert await async_setup_component(hass, "automation", config)
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"lamp": {}}})
    area = ar.async_get(hass).async_create("Den")
    er.async_get(hass).async_update_entity("input_boolean.lamp", area_id=area.id)
    await _set(hass, freezer, local(2026, 9, 14, 18, 0), lamp=True)
    await _set(hass, freezer, local(2026, 9, 15, 9, 0))

    entry = MockConfigEntry(domain=DOMAIN, title="Den", entry_id=ENTRY_ID, version=2, options={
        **DEFAULT_OPTIONS, CONF_AREAS: [area.id], CONF_DOMAINS: ["input_boolean"],
        CONF_SOURCE_DATE: "2026-09-14"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "scene: !include scenes.yaml" in entry.runtime_data.last_error


async def test_migrates_v1_options(recorder_mock, enable_custom_integrations, hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=1, options={
        **{k: v for k, v in DEFAULT_OPTIONS.items() if k not in (CONF_WINDOW_START, CONF_WINDOW_END)},
        "replay_mode": "weekday", "create_scene_entities": True, CONF_SOURCE_DATE: "2026-09-14"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert "replay_mode" not in entry.options and entry.options[CONF_WINDOW_START] == "00:00:00"
