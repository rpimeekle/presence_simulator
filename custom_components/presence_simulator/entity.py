"""Base entity for Presence Simulator."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import PresenceSimulator


class PresenceSimEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, sim: PresenceSimulator, key: str) -> None:
        self.sim = sim
        self._attr_unique_id = f"{sim.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sim.entry.entry_id)},
            name=sim.entry.title,
            manufacturer="bannon52",
            model="Presence Simulator",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.sim.async_add_listener(self.async_write_ha_state))
