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
CONF_REPLAY_MODE = "replay_mode"
CONF_RESTORE_ON_STOP = "restore_on_stop"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
CONF_CREATE_SCENES = "create_scene_entities"

SUPPORTED_DOMAINS = ["light", "switch", "fan", "cover", "media_player", "input_boolean"]
DEFAULT_DOMAINS = ["light", "switch"]

REPLAY_WEEKDAY = "weekday"
REPLAY_SEQUENTIAL = "sequential"
REPLAY_RANDOM = "random"
REPLAY_MODES = [REPLAY_WEEKDAY, REPLAY_SEQUENTIAL, REPLAY_RANDOM]

DEFAULT_OPTIONS: dict = {
    CONF_AREAS: [],
    CONF_DOMAINS: DEFAULT_DOMAINS,
    CONF_SOURCE_DAYS: 1,
    CONF_MERGE_SECONDS: 60,
    CONF_JITTER_MINUTES: 10,
    CONF_REPLAY_MODE: REPLAY_WEEKDAY,
    CONF_RESTORE_ON_STOP: True,
    CONF_EXCLUDE_ENTITIES: [],
    CONF_CREATE_SCENES: True,
}

# Options that change what gets learned. Changing any of these triggers a relearn.
LEARN_KEYS = (
    CONF_AREAS,
    CONF_DOMAINS,
    CONF_SOURCE_DATE,
    CONF_SOURCE_DAYS,
    CONF_MERGE_SECONDS,
    CONF_EXCLUDE_ENTITIES,
)

STATUS_IDLE = "idle"
STATUS_LEARNING = "learning"
STATUS_RUNNING = "running"
STATUS_NO_DATA = "no_data"
STATUSES = [STATUS_IDLE, STATUS_LEARNING, STATUS_RUNNING, STATUS_NO_DATA]

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
