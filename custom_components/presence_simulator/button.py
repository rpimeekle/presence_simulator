"""Relearn button."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PresenceSimConfigEntry
from .entity import PresenceSimEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PresenceSimConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([RelearnButton(entry.runtime_data, "relearn")])


class RelearnButton(PresenceSimEntity, ButtonEntity):
    _attr_translation_key = "relearn"
    _attr_icon = "mdi:history"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return not self.sim.learning

    async def async_press(self) -> None:
        await self.sim.async_learn()
