"""Config flow tests."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.presence_simulator.const import DOMAIN


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer
) -> None:
    freezer.move_to("2026-09-15 10:00:00+10:00")
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"lamp": {}}})
    area = ar.async_get(hass).async_create("Study")
    er.async_get(hass).async_update_entity("input_boolean.lamp", area_id=area.id)

    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    user_input = {
        "name": "Study sim",
        "areas": [area.id],
        "domains": ["light"],
        "source_date": "2026-09-14",
        "source_days": 1,
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"base": "no_entities"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**user_input, "source_date": "2026-09-30"}
    )
    assert result["errors"] == {"source_date": "future_date"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**user_input, "domains": ["input_boolean"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Study sim"
    assert result["options"]["areas"] == [area.id]
    assert result["options"]["source_days"] == 1
    assert result["options"]["jitter_minutes"] == 10
    await hass.async_block_till_done(wait_background_tasks=True)
