"""One scene entity per learned snapshot, so each can be triggered by hand."""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import DOMAIN as SCENE_DOMAIN, Scene
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import learner
from .const import CONF_CREATE_SCENES, WEEKDAY_NAMES
from .coordinator import PresenceSimConfigEntry, PresenceSimulator
from .entity import PresenceSimEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PresenceSimConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    sim = entry.runtime_data
    added: set[str] = set()

    @callback
    def _sync() -> None:
        wanted: dict[str, tuple[str, int, int]] = {}
        if sim.options.get(CONF_CREATE_SCENES, True):
            for day_key, day in sim.days.items():
                for scene in day["scenes"]:
                    uid = f"{entry.entry_id}_scene_{day_key}_{scene['t']}"
                    wanted[uid] = (day_key, day["weekday"], scene["t"])

        # Drop scenes that no longer exist after a relearn (or from a previous run).
        ent_reg = er.async_get(hass)
        for reg in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if reg.domain == SCENE_DOMAIN and reg.unique_id not in wanted:
                ent_reg.async_remove(reg.entity_id)
                added.discard(reg.unique_id)

        new = [SimScene(sim, *wanted[uid]) for uid in wanted if uid not in added]
        added.update(uid for uid in wanted)
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(sim.async_add_learn_listener(_sync))


class SimScene(PresenceSimEntity, Scene):
    _attr_icon = "mdi:palette-outline"

    def __init__(self, sim: PresenceSimulator, day_key: str, weekday: int, offset: int) -> None:
        super().__init__(sim, f"scene_{day_key}_{offset}")
        self._day_key = day_key
        self._offset = offset
        self._attr_name = f"{WEEKDAY_NAMES[weekday]} {day_key} {learner.format_offset(offset)[:5]}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self.sim.days.get(self._day_key, {})
        scene = next((s for s in day.get("scenes", []) if s["t"] == self._offset), None)
        return {
            "source_day": self._day_key,
            "time_of_day": learner.format_offset(self._offset),
            "entity_id": sorted(scene["states"]) if scene else [],
        }

    async def async_activate(self, **kwargs: Any) -> None:
        await self.sim.async_apply_scene(self._day_key, self._offset)
