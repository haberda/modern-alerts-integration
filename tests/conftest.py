"""Fixtures using a real Home Assistant runtime, with network disabled."""

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.modern_alerts.models import AlertConfig
from custom_components.modern_alerts.runtime import AlertRuntime


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow loading the local integration."""


@pytest.fixture
def notifications(hass: HomeAssistant):
    return async_mock_service(hass, "notify", "phone")


@pytest.fixture
async def make_runtime(hass: HomeAssistant, notifications):
    runtimes = []

    def make(**overrides):
        config = AlertConfig.from_dict(
            {
                "name": "Garage",
                "entity_id": "binary_sensor.garage",
                "notifiers": ["phone"],
                "done_message": "Closed",
                **overrides,
            },
            hass,
        )
        runtime = AlertRuntime(hass, config)
        runtime.start()
        runtimes.append(runtime)
        return runtime

    yield make
    for runtime in runtimes:
        await runtime.async_stop()
