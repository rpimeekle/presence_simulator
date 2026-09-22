"""Read/write scenes.yaml and automations.yaml the same way HA's config editor does."""
from __future__ import annotations

import os
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml


def read_list(path: str) -> list[Any]:
    """Must run in the executor."""
    if not os.path.isfile(path):
        return []
    data = load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, list):
        raise HomeAssistantError(f"{path} does not contain a list; refusing to modify it")
    return list(data)


def write_list(path: str, items: list[Any]) -> None:
    """Must run in the executor. Dump first so an error never truncates the file."""
    contents = dump(items) if items else "[]\n"
    write_utf8_file_atomic(path, contents)
