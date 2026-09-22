"""Constants for Presence Simulator."""
from __future__ import annotations

DOMAIN = "presence_simulator"
STORAGE_VERSION = 1

CONF_AREAS = "areas"
CONF_DOMAINS = "domains"
CONF_SOURCE_DATE = "source_date"
CONF_SOURCE_DAYS = "source_days"
CONF_MERGE_SECONDS = "merge_seconds"
CONF_JITTER_MINUTES = "jitter_minutes"
CONF_RESTORE_ON_STOP = "restore_on_stop"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
CONF_WINDOW_START = "window_start"
CONF_WINDOW_END = "window_end"
CONF_OFF_AT_END = "off_at_end"

SUPPORTED_DOMAINS = ["light", "switch", "fan", "cover", "media_player", "input_boolean"]
DEFAULT_DOMAINS = ["light", "switch"]

DEFAULT_OPTIONS: dict = {
    CONF_AREAS: [],
    CONF_DOMAINS: DEFAULT_DOMAINS,
    CONF_SOURCE_DAYS: 1,
    CONF_WINDOW_START: "00:00:00",
    CONF_WINDOW_END: "00:00:00",
    CONF_OFF_AT_END: True,
    CONF_MERGE_SECONDS: 60,
    CONF_JITTER_MINUTES: 0,
    CONF_RESTORE_ON_STOP: True,
    CONF_EXCLUDE_ENTITIES: [],
}

# Options that change what gets learned or generated. Changing any triggers a relearn.
LEARN_KEYS = (
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_SOURCE_DATE,
    CONF_SOURCE_DAYS,
    CONF_WINDOW_START,
    CONF_WINDOW_END,
    CONF_OFF_AT_END,
    CONF_MERGE_SECONDS,
    CONF_JITTER_MINUTES,
    CONF_EXCLUDE_ENTITIES,
)

SCENES_FILE = "scenes.yaml"
AUTOMATIONS_FILE = "automations.yaml"
SERVICE_ACTIVATE_SCENE = "activate_scene"
ATTR_SCENE_ID = "scene_id"

STATUS_IDLE = "idle"
STATUS_LEARNING = "learning"
STATUS_RUNNING = "running"
STATUS_NO_DATA = "no_data"
STATUSES = [STATUS_IDLE, STATUS_LEARNING, STATUS_RUNNING, STATUS_NO_DATA]

