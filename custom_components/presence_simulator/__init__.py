"""Presence Simulator: learn an area's activity from history and replay it."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .coordinator import PresenceSimConfigEntry, PresenceSimulator

PLATFORMS = [Platform.SWITCH, Platform.BUTTON, Platform.SENSOR, Platform.SCENE]


async def async_setup_entry(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> bool:
    sim = PresenceSimulator(hass, entry)
    await sim.async_initialize()
    entry.runtime_data = sim
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    sim.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: PresenceSimConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
