"""Record a real evening in the recorder, learn it, then replay it the next day."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.presence_simulator.const import (
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_JITTER_MINUTES,
    CONF_SOURCE_DATE,
    DEFAULT_OPTIONS,
    DOMAIN,
)

TZ = ZoneInfo("Australia/Brisbane")


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


def _eid(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"test_entry_{key}")


async def test_learn_and_replay(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    await hass.config.async_set_time_zone("Australia/Brisbane")
    freezer.move_to(local(2026, 9, 13, 23, 0))

    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"lamp": {}, "tv": {}, "garage": {}}}
    )
    lounge = ar.async_get(hass).async_create("Lounge")
    ent_reg = er.async_get(hass)
    for obj in ("lamp", "tv"):
        ent_reg.async_update_entity(f"input_boolean.{obj}", area_id=lounge.id)

    # History on Monday 14 Sep: lamp + TV on at 18:00 (40s apart), off at 22:30.
    await _set(hass, freezer, local(2026, 9, 13, 23, 0), lamp=False, tv=False, garage=False)
    await _set(hass, freezer, local(2026, 9, 14, 18, 0, 0), lamp=True)
    await _set(hass, freezer, local(2026, 9, 14, 18, 0, 40), tv=True, garage=True)
    await _set(hass, freezer, local(2026, 9, 14, 22, 30, 0), lamp=False, tv=False)

    # Next day at 10:00, set up the integration pointed at Monday.
    await _set(hass, freezer, local(2026, 9, 15, 10, 0))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lounge sim",
        entry_id="test_entry",
        options={
            **DEFAULT_OPTIONS,
            CONF_AREAS: [lounge.id],
            CONF_DOMAINS: ["input_boolean"],
            CONF_SOURCE_DATE: "2026-09-14",
            CONF_JITTER_MINUTES: 0,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    sim = entry.runtime_data
    assert sim.last_error is None
    assert sim.entities == ["input_boolean.lamp", "input_boolean.tv"]  # garage not in area
    assert hass.states.get(_eid(hass, "sensor", "learned_scenes")).state == "3"
    assert [s["t"] for s in sim.days["2026-09-14"]["scenes"]] == [0, 64800, 81000]
    scene_eid = _eid(hass, "scene", "scene_2026-09-14_64800")
    assert scene_eid is not None

    # Pretend the owner leaves with the TV on, then enable the simulation.
    await _set(hass, freezer, local(2026, 9, 15, 10, 0, 5), tv=True)
    switch = _eid(hass, "switch", "simulation")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    # Catch-up applied the 00:00 baseline (everything off)
    assert hass.states.get("input_boolean.tv").state == "off"
    assert hass.states.get(_eid(hass, "sensor", "status")).state == "running"
    assert sim.next_run == local(2026, 9, 15, 18, 0)

    # 18:00 Tuesday: lamp and TV come on.
    freezer.move_to(local(2026, 9, 15, 18, 0, 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("input_boolean.lamp").state == "on"
    assert hass.states.get("input_boolean.tv").state == "on"
    assert hass.states.get("input_boolean.garage").state == "on"  # outside the area: left alone
    assert sim.next_run == local(2026, 9, 15, 22, 30)

    # 22:30: off again, then schedule rolls over to tomorrow's baseline.
    freezer.move_to(local(2026, 9, 15, 22, 30, 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("input_boolean.lamp").state == "off"
    assert sim.next_run == local(2026, 9, 16, 0, 0)

    # Manually activate a learned scene.
    await hass.services.async_call("scene", "turn_on", {"entity_id": scene_eid}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.lamp").state == "on"

    # Switch off -> restore the state captured when it was switched on (TV on, lamp off).
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.lamp").state == "off"
    assert hass.states.get("input_boolean.tv").state == "on"
    assert sim.next_run is None

    # Relearn keeps the same scene entities.
    await hass.services.async_call(
        "button", "press", {"entity_id": _eid(hass, "button", "relearn")}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _eid(hass, "scene", "scene_2026-09-14_64800") == scene_eid

    assert await hass.config_entries.async_unload(entry.entry_id)
