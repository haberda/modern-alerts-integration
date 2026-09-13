"""Render at dispatch time and isolate notifier failures."""

import pytest
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.modern_alerts.models import AlertConfig
from custom_components.modern_alerts.notifications import async_notify


async def test_templates_and_literal_payload(hass, notifications):
    data = {"tag": "garage", "value": "{{ 1 + 1 }}"}
    config = AlertConfig.from_dict(
        {
            "name": "Literal {{ name }}",
            "entity_id": "sensor.test",
            "message": "{{ states('sensor.test') }}",
            "title": "{{ 2 + 2 }}",
            "done_message": "clear_notification",
            "notifiers": ["phone"],
            "data": data,
        },
        hass,
    )
    context = Context()
    hass.states.async_set("sensor.test", "one")
    assert await async_notify(hass, config, context=context) == {}
    hass.states.async_set("sensor.test", "two")
    await async_notify(hass, config)
    await async_notify(hass, config, done=True)
    assert [c.data["message"] for c in notifications] == [
        "one",
        "two",
        "clear_notification",
    ]
    assert all(c.data["title"] == "4" and c.data["data"] == data for c in notifications)
    assert notifications[0].context == context


@pytest.mark.parametrize("broken", ["missing", "raises"])
async def test_failure_isolation(hass, notifications, broken):
    async def raises(call):
        raise RuntimeError("secret provider payload")

    hass.services.async_register("notify", "raises", raises)
    config = AlertConfig(
        name="Name", entity_id="sensor.test", notifiers=(broken, "phone")
    )
    errors = await async_notify(hass, config)
    assert errors[f"notify.{broken}"] in ("ServiceNotFound", "RuntimeError")
    assert notifications[0].data == {"message": "Name"}


async def test_entity_adapter(hass):
    calls = async_mock_service(hass, "notify", "send_message")
    config = AlertConfig(
        name="Name", entity_id="sensor.test", notify_entities=("notify.target",)
    )
    assert await async_notify(hass, config) == {}
    assert calls[0].data == {"entity_id": "notify.target", "message": "Name"}


async def test_template_failure_is_reported(hass, notifications):
    config = AlertConfig(
        name="Name",
        entity_id="sensor.test",
        message="{{ 1 / 0 }}",
        notifiers=("phone",),
    )
    assert await async_notify(hass, config) == {"template": "render_failed"}
    assert notifications == []


async def test_empty_destinations(hass):
    assert (
        await async_notify(hass, AlertConfig(name="State", entity_id="sensor.test"))
        == {}
    )
