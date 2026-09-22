"""Presence Simulator: learn an area's activity from history and replay it
with editable scenes and automations."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_SCENE_ID,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_OPTIONS,
    DOMAIN,
    SERVICE_ACTIVATE_SCENE,
    STORAGE_VERSION,
)
from .coordinator import PresenceSimConfigEntry, PresenceSimulator, async_remove_generated

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
ACTIVATE_SCHEMA = vol.Schema({vol.Required(ATTR_SCENE_ID): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service the generated automations call."""

    async def _activate(call: ServiceCall) -> None:
        sid = call.data[ATTR_SCENE_ID]
        owners = [
            entry.runtime_data
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED and entry.runtime_data.owns_scene(sid)
        ]
        if owners:
            await owners[0].async_activate(sid, call.context)
            return
        # Not one of ours (e.g. user pointed it at another YAML scene): still honour it.
        entity_id = er.async_get(hass).async_get_entity_id("scene", "homeassistant", sid)
        if entity_id is None:
            raise ServiceValidationError(f"No scene with id {sid} is loaded")
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": entity_id}, blocking=True, context=call.context
        )

    hass.services.async_register(DOMAIN, SERVICE_ACTIVATE_SCENE, _activate, schema=ACTIVATE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> bool:
    sim = PresenceSimulator(hass, entry)
    await sim.async_initialize()
    entry.runtime_data = sim

    # v0.1 created integration-owned scene entities; those are replaced by YAML scenes.
    ent_reg = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg.domain == "scene":
            ent_reg.async_remove(reg.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    sim.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleting the integration also deletes the scenes/automations it generated."""
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    if data := await store.async_load():
        await async_remove_generated(hass, data)
    await store.async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        options = {
            k: v
            for k, v in entry.options.items()
            if k not in ("replay_mode", "create_scene_entities")
        }
        options.setdefault(CONF_WINDOW_START, DEFAULT_OPTIONS[CONF_WINDOW_START])
        options.setdefault(CONF_WINDOW_END, DEFAULT_OPTIONS[CONF_WINDOW_END])
        hass.config_entries.async_update_entry(entry, options={**DEFAULT_OPTIONS, **options}, version=2)
        _LOGGER.info("Migrated %s to config version 2", entry.title)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
