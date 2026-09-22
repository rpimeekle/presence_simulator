# Presence Simulator

A Home Assistant custom integration (HACS) that **learns** how an area of the house was
used on a real day in history, turns that activity into a **series of scenes**, and
**replays** them at the same time of day to make the house look occupied.

## How it works

1. **Learn** – Pick one or more areas, the entity types to watch (lights, switches, fans,
   covers, media players, input booleans), a date, how many consecutive days, and the
   **time window** of each day to learn (e.g. `16:00 → 23:30`; an end earlier than the start
   crosses midnight, `00:00 → 00:00` is the whole day). The recorder is read for every enabled
   entity in those areas (assigned directly or via its device).
2. **Build scenes** – Changes within the *merge window* (default 60 s) become one scene, timed
   at the first change. Each scene is a full snapshot of every tracked entity (brightness /
   colour / colour temp, fan speed, cover position). The window starts with a baseline scene
   and, optionally, ends with an "everything off" scene so nothing is left on overnight.
3. **Generate** – Every scene is written to **`scenes.yaml`** and gets a matching automation in
   **`automations.yaml`** with a `time` trigger at the **exact time of day the original event
   happened** (plus a weekday condition when more than one day is learned). Both have stable
   unique `id`s, so they appear in *Settings → Automations & scenes* and can be edited in the
   normal scene and automation editors.
4. **Replay** – The **Simulation** switch turns the generated automations on and off. Switching
   on inside the learned window immediately applies the scene that should currently be active.
   Switching off disables them and (optionally) restores everything to how it was when the
   simulation was switched on.

### Which day plays when

A learned day plays on its own weekday; weekdays with no learned counterpart cycle through the
learned days. So one learned day plays every day, and 7 learned days give a realistic week.
Scenes after midnight in a midnight-crossing window run on the following weekday.

### Editing from the frontend

Generated ids look like `presence_sim_<entry>_<yyyymmdd>_<seconds>` (scenes) and
`…_auto` (automations). They're stable, so the same learned moment keeps the same id.

* **Relearn from history** regenerates everything *except* scenes/automations you've edited in
  the frontend – those are detected and kept exactly as you left them.
* **Rebuild (discard frontend edits)** regenerates everything.
* Your own scenes/automations in those files are never touched.
* Deleting the integration removes only the items it generated.

Automations call `presence_simulator.activate_scene` with the scene's **id** rather than its
entity_id, so renaming a scene entity doesn't break anything, and your edits to a scene are
what gets applied.

**Requirement:** `configuration.yaml` must include the standard lines (present by default):

```yaml
automation: !include automations.yaml
scene: !include scenes.yaml
```

If they're missing, the status sensor reports which one to add.

## Entities (per configured instance)

| Entity | Purpose |
| --- | --- |
| `switch.<name>_simulation` | Enables / disables the generated automations |
| `button.<name>_relearn_from_history` | Re-read the recorder; keeps frontend edits |
| `button.<name>_rebuild_discard_frontend_edits` | Re-read and overwrite everything |
| `sensor.<name>_status` | `idle`, `learning`, `running` or `no_data` (with `last_error`) |
| `sensor.<name>_next_scene` / `_last_scene` | Timestamps, with the scene name as an attribute |
| `sensor.<name>_learned_scenes` | Count, plus scene/automation ids and which were edited |

## Installation

HACS → ⋮ → Custom repositories → add this repo as an *Integration* → install → restart.
Then Settings → Devices & services → Add integration → **Presence Simulator**.
Add one instance per area group if you want different source days per part of the house.

## Configuration

Initial setup asks for name, areas, entity types, source date, number of days and the time
window. Everything is editable later under **Configure**:

| Option | Default | Notes |
| --- | --- | --- |
| Learn from / until | 00:00 / 00:00 | Whole day. End < start crosses midnight |
| Turn everything off at end of window | On | Ignored for whole-day windows |
| Merge window | 60 s | Larger = fewer, chunkier scenes |
| Random delay | 0 min | 0 = exact original time. Otherwise up to N min late, never past the next scene |
| Restore on stop | On | |
| Exclude entities | – | e.g. a fridge plug in the kitchen |

Changing any learning option triggers a relearn (keeping frontend edits).

**Recorder retention:** the source date must still be in the recorder (`purge_keep_days`,
default 10) when you learn it. After that the scenes live in `scenes.yaml` and survive purges.

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
* If a device is renamed (entity_id changes) after learning, press Relearn (or fix the scene in the editor).
* The integration rewrites `scenes.yaml` / `automations.yaml` the same way HA's editors do, which
  normalises formatting and drops comments in those two files.

## Development

```bash
pip install -r requirements_test.txt
pytest -q
```

`tests/test_learner.py` and `tests/test_generator.py` cover the pure logic (no HA needed).
`tests/integration/` runs against a real Home Assistant with an in-memory recorder and a
temporary config dir: it records a day, learns a time window, writes the YAML, and lets HA's
automation engine replay it the next day with a frozen clock, including edit preservation,
rebuild, restore and removal.

## Attribution

### AI Usage

This integration was developed with help from Claude Opus and GitHub Copilot.

### Icon

[Simulation icons created by Eucalyp - Flaticon](https://www.flaticon.com/free-icons/simulation)
