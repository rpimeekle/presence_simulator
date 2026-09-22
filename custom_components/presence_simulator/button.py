"""Relearn buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PresenceSimConfigEntry, PresenceSimulator
from .entity import PresenceSimEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PresenceSimConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    sim = entry.runtime_data
    async_add_entities(
        [
            RelearnButton(sim, "relearn", discard_edits=False),
            RelearnButton(sim, "rebuild", discard_edits=True),
        ]
    )


class RelearnButton(PresenceSimEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, sim: PresenceSimulator, key: str, discard_edits: bool) -> None:
        super().__init__(sim, key)
        self._discard = discard_edits
        self._attr_translation_key = key
        self._attr_icon = "mdi:delete-restore" if discard_edits else "mdi:history"

    @property
    def available(self) -> bool:
        return not self.sim.learning

    async def async_press(self) -> None:
        await self.sim.async_learn(discard_edits=self._discard)
