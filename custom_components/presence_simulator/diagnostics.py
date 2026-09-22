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
                "scene_times": [
                    f"{learner.format_offset(s['t'])}{' (+1d)' if s.get('shift') else ''}"
                    for s in day["scenes"]
                ],
            }
            for key, day in sim.days.items()
        },
        "schedule": [
            {k: v for k, v in e.items() if k != "states"} for e in sim.schedule
        ],
        "scene_ids": sim.scene_ids,
        "automation_ids": sim.automation_ids,
        "edited_in_frontend": sim.edited_ids,
    }
