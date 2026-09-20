"""Config and options flow for Presence Simulator."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREAS,
    CONF_CREATE_SCENES,
    CONF_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_JITTER_MINUTES,
    CONF_MERGE_SECONDS,
    CONF_REPLAY_MODE,
    CONF_RESTORE_ON_STOP,
    CONF_SOURCE_DATE,
    CONF_SOURCE_DAYS,
    DEFAULT_OPTIONS,
    DOMAIN,
    REPLAY_MODES,
    SUPPORTED_DOMAINS,
)
from .coordinator import async_resolve_entities

SOURCE_FIELDS = {
    vol.Required(CONF_AREAS): selector.AreaSelector(
        selector.AreaSelectorConfig(multiple=True)
    ),
    vol.Required(CONF_DOMAINS): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=SUPPORTED_DOMAINS,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="domains",
        )
    ),
    vol.Required(CONF_SOURCE_DATE): selector.DateSelector(),
    vol.Required(CONF_SOURCE_DAYS): selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=14, step=1, mode=selector.NumberSelectorMode.BOX)
    ),
}

TUNING_FIELDS = {
    vol.Required(CONF_MERGE_SECONDS): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=5, max=900, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
        )
    ),
    vol.Required(CONF_JITTER_MINUTES): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=60, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.SLIDER
        )
    ),
    vol.Required(CONF_REPLAY_MODE): selector.SelectSelector(
        selector.SelectSelectorConfig(options=REPLAY_MODES, translation_key="replay_mode")
    ),
    vol.Required(CONF_RESTORE_ON_STOP): selector.BooleanSelector(),
    vol.Required(CONF_CREATE_SCENES): selector.BooleanSelector(),
    vol.Optional(CONF_EXCLUDE_ENTITIES): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=SUPPORTED_DOMAINS, multiple=True)
    ),
}


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_SOURCE_DAYS, CONF_MERGE_SECONDS, CONF_JITTER_MINUTES):
        if key in data:
            data[key] = int(data[key])
    return data


def _validate(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get(CONF_AREAS):
        errors[CONF_AREAS] = "no_areas"
        return errors
    if not data.get(CONF_DOMAINS):
        errors[CONF_DOMAINS] = "no_domains"
        return errors

    source = date.fromisoformat(str(data[CONF_SOURCE_DATE]))
    today = dt_util.now().date()
    if source > today:
        errors[CONF_SOURCE_DATE] = "future_date"
    else:
        keep_days = getattr(get_instance(hass), "keep_days", None)
        if keep_days is not None and (today - source).days > keep_days:
            errors[CONF_SOURCE_DATE] = "beyond_retention"

    if not errors and not async_resolve_entities(hass, {**DEFAULT_OPTIONS, **data}):
        errors["base"] = "no_entities"
    return errors


class PresenceSimulatorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            errors = _validate(self.hass, data)
            if not errors:
                name = data.pop(CONF_NAME)
                return self.async_create_entry(
                    title=name, data={}, options={**DEFAULT_OPTIONS, **data}
                )

        schema = vol.Schema(
            {vol.Required(CONF_NAME): selector.TextSelector(), **SOURCE_FIELDS}
        )
        suggested = user_input or {
            CONF_NAME: "Presence Simulator",
            CONF_DOMAINS: DEFAULT_OPTIONS[CONF_DOMAINS],
            CONF_SOURCE_DATE: (dt_util.now().date() - timedelta(days=1)).isoformat(),
            CONF_SOURCE_DAYS: DEFAULT_OPTIONS[CONF_SOURCE_DAYS],
        }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PresenceSimulatorOptionsFlow()


class PresenceSimulatorOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            data.setdefault(CONF_EXCLUDE_ENTITIES, [])
            errors = _validate(self.hass, data)
            if not errors:
                return self.async_create_entry(data={**DEFAULT_OPTIONS, **data})

        schema = vol.Schema({**SOURCE_FIELDS, **TUNING_FIELDS})
        current = user_input or {**DEFAULT_OPTIONS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )
