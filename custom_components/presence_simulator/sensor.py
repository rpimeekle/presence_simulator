"""Status sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import STATUSES
from .coordinator import PresenceSimConfigEntry, PresenceSimulator
from .entity import PresenceSimEntity


@dataclass(frozen=True, kw_only=True)
class SimSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PresenceSimulator], Any]
    attrs_fn: Callable[[PresenceSimulator], dict[str, Any]] | None = None


SENSORS: tuple[SimSensorDescription, ...] = (
    SimSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda s: s.status,
        attrs_fn=lambda s: {"last_error": s.last_error},
    ),
    SimSensorDescription(
        key="next_scene",
        translation_key="next_scene",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.next_run,
        attrs_fn=lambda s: {"scene": s.next_label},
    ),
    SimSensorDescription(
        key="last_scene",
        translation_key="last_scene",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.last_applied,
        attrs_fn=lambda s: {"scene": s.last_label},
    ),
    SimSensorDescription(
        key="learned_scenes",
        translation_key="learned_scenes",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.scene_count,
        attrs_fn=lambda s: {
            "learned_at": s.data.get("learned_at"),
            "source_days": sorted(s.days),
            "entities": s.entities,
            "last_error": s.last_error,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PresenceSimConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    sim = entry.runtime_data
    async_add_entities(SimSensor(sim, desc) for desc in SENSORS)


class SimSensor(PresenceSimEntity, SensorEntity):
    entity_description: SimSensorDescription

    def __init__(self, sim: PresenceSimulator, description: SimSensorDescription) -> None:
        super().__init__(sim, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.sim)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.sim) if fn else None
