"""Master on/off switch for the simulation."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PresenceSimConfigEntry
from .entity import PresenceSimEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PresenceSimConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SimulationSwitch(entry.runtime_data, "simulation")])


class SimulationSwitch(PresenceSimEntity, SwitchEntity):
    _attr_translation_key = "simulation"
    _attr_icon = "mdi:home-account"

    @property
    def is_on(self) -> bool:
        return self.sim.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.sim.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.sim.async_set_enabled(False)
