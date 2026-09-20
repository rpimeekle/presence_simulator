# Presence Simulator

A Home Assistant custom integration (HACS) that **learns** how an area of the house was
used on a real day in history, turns that activity into a **series of scenes**, and
**replays** them at the same time of day to make the house look occupied.

## How it works

1. **Learn** – You pick one or more areas, the entity types to watch (lights, switches,
   fans, covers, media players, input booleans) and a date. The integration reads the
   recorder for that day (or N consecutive days) for every enabled entity in those areas
   (assigned directly or via its device).
2. **Build scenes** – Changes that happen within the *merge window* (default 60 s) are
   grouped into one scene, timed at the first change. Each scene is a full snapshot of
   every tracked entity, including brightness / colour / colour temp for lights, speed for
   fans and position for covers. Each day starts with a 00:00 baseline scene. "Unavailable"
   blips and attribute noise are ignored.
3. **Replay** – When the **Simulation** switch is on, each scene is applied at its time of
   day using Home Assistant's own `reproduce_state` machinery (the same thing scenes use),
   shifted by a random jitter (default ±10 min, re-rolled daily, order always preserved).
   Switching on mid-day immediately applies whatever scene *should* be active right now.
4. **Stop** – Switching off cancels the schedule and (optionally) restores every tracked
   entity to how it was when the simulation was switched on.

If you learn several days, the replay mode chooses which one to use each day: the same
weekday when available (default), cycling through them, or random. Learn 7 days to get a
realistic week.

## Entities (per configured instance)

| Entity | Purpose |
| --- | --- |
| `switch.<name>_simulation` | Start / stop the simulation (state survives restarts) |
| `button.<name>_relearn_from_history` | Re-read the recorder and rebuild scenes |
| `sensor.<name>_status` | `idle`, `learning`, `running` or `no_data` (with `last_error`) |
| `sensor.<name>_next_scene` / `_last_scene` | Timestamps, with the scene label as an attribute |
| `sensor.<name>_learned_scenes` | Count, plus learned days and tracked entities |
| `scene.<name>_<day>_<hh_mm>` | One per learned scene, so you can inspect or fire them manually |

Scene entities can be turned off in options if you learn many days.

## Installation

HACS → ⋮ → Custom repositories → add this repo as an *Integration* → install → restart.
Then Settings → Devices & services → Add integration → **Presence Simulator**.
Add one instance per area group if you want different source days per part of the house.

## Configuration

Initial setup asks for name, areas, entity types, source date and number of days.
Everything, including tuning, is editable later under **Configure**:

| Option | Default | Notes |
| --- | --- | --- |
| Merge window | 60 s | Larger = fewer, chunkier scenes |
| Random time jitter | 10 min | 0 disables |
| Replay mode | Same weekday | Only matters with >1 learned day |
| Restore on stop | On | |
| Create scene entities | On | |
| Exclude entities | – | e.g. a fridge plug in the kitchen |

Changing areas, types, date, days, merge window or exclusions triggers a relearn automatically.

**Recorder retention:** the source date must still be in the recorder (`purge_keep_days`,
default 10). If you want to keep a "reference day" long-term, learn it while it's still in
history – the learned scenes are stored in `.storage/presence_simulator.<entry_id>` and are
**not** affected by later purges (until you change learning options or press Relearn).

## Example automation

```yaml
automation:
  - alias: Presence simulation while away
    triggers:
      - trigger: state
        entity_id: alarm_control_panel.house
    actions:
      - action: >
          switch.turn_{{ 'on' if trigger.to_state.state == 'armed_away' else 'off' }}
        target:
          entity_id: switch.lounge_sim_simulation
```

## Limitations

* Media players are replayed as on/off only – what was playing isn't reproducible.
* Entities with an entity category (config/diagnostic switches) are skipped on purpose.
* If a device is renamed (entity_id changes) after learning, press Relearn.

## Development

```bash
pip install -r requirements_test.txt
pytest -q
```

`tests/test_learner.py` covers the pure learning/planning logic (no HA needed).
`tests/integration/` runs against a real Home Assistant with an in-memory recorder: it
records an evening, learns it, and replays it the next day with a frozen clock.




This repository was generated with help from Claude Opus and GitHub Copilot.