"""Integration tests run against real Home Assistant via
pytest-homeassistant-custom-component (auto-registered as a pytest plugin).

Request `recorder_mock` BEFORE `hass` / `enable_custom_integrations` in each
test signature, otherwise the recorder fixture refuses to start.
"""
