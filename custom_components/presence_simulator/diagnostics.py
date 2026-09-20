"""Diagnostics support."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import learner
from .coordinator import PresenceSimConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PresenceSimConfigEntry
) -> dict[str, Any]:
    sim = entry.runtime_data
    return {
        "options": dict(entry.options),
        "status": sim.status,
        "enabled": sim.enabled,
        "last_error": sim.last_error,
        "learned_at": sim.data.get("learned_at"),
        "entities": sim.entities,
        "next_run": sim.next_run.isoformat() if sim.next_run else None,
        "next_label": sim.next_label,
        "days": {
            key: {
                "weekday": day["weekday"],
                "scene_times": [learner.format_offset(s["t"]) for s in day["scenes"]],
            }
            for key, day in sim.days.items()
        },
    }
