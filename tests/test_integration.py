"""Real setup, registry entities, actions, edits, and removal."""

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.models import AlertConfig


async def setup_alert(hass, **overrides):
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
    entry = MockConfigEntry(domain=DOMAIN, title=config.name, data=config.as_dict())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def entity_id(hass, entry, domain, key):
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )


async def test_setup_entities_actions_and_removal(hass, notifications, freezer):
    entry = await setup_alert(hass)
    status = entity_id(hass, entry, "sensor", "status")
    problem = entity_id(hass, entry, "binary_sensor", "problem")
    ack = entity_id(hass, entry, "button", "acknowledge")
    resume = entity_id(hass, entry, "button", "unacknowledge")
    assert status and problem and ack and resume
    assert hass.states.get(status).state == "idle"
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "on"
    assert hass.states.get(problem).state == "on"
    await hass.services.async_call("button", "press", {"entity_id": ack}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "off"
    assert hass.states.get(problem).state == "on"
    await hass.services.async_call(
        "button", "press", {"entity_id": resume}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "on"
    for action, expected in [
        ("toggle", "off"),
        ("turn_on", "on"),
        ("turn_off", "off"),
        ("unacknowledge", "on"),
        ("acknowledge", "off"),
    ]:
        await hass.services.async_call(
            DOMAIN, action, {"entity_id": status}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(status).state == expected
    assert len(notifications) == 1
    hass.states.async_set("binary_sensor.garage", "off")
    await hass.async_block_till_done()
    assert notifications[-1].data["message"] == "Closed"
    assert await hass.config_entries.async_remove(entry.entry_id)
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    assert len(notifications) == 2
    assert er.async_get(hass).async_get(status) is None


async def test_live_options_preserve_identity_and_ack(hass, notifications):
    entry = await setup_alert(hass)
    status = entity_id(hass, entry, "sensor", "status")
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN, "acknowledge", {"entity_id": status}, blocking=True
    )
    options = {**entry.data, "name": "Renamed", "repeat": [5], "message": "New message"}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert entry.title == "Renamed"
    assert entity_id(hass, entry, "sensor", "status") == status
    assert hass.states.get(status).state == "off"
    assert len(notifications) == 1


async def test_two_alerts_are_independent(hass, notifications):
    first = await setup_alert(hass)
    second = await setup_alert(hass, name="Second")
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN,
        "acknowledge",
        {"entity_id": entity_id(hass, first, "sensor", "status")},
        blocking=True,
    )
    assert first.runtime_data.state == "off"
    assert second.runtime_data.state == "on"
    assert len(notifications) == 2


async def test_no_ack_and_explicit_test(hass, notifications):
    entry = await setup_alert(hass, can_acknowledge=False)
    status = entity_id(hass, entry, "sensor", "status")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "acknowledge", {"entity_id": status}, blocking=True
        )
    await hass.services.async_call(
        DOMAIN, "test_notification", {"entity_id": status}, blocking=True
    )
    assert entry.runtime_data.state == "idle"
    assert not entry.runtime_data.attempted
    assert len(notifications) == 1


async def test_invalid_saved_config_fails_clearly(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"name": "Bad", "entity_id": "sensor.test", "repeat": []}
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.parametrize("evaluate", [False, True])
async def test_reload_has_documented_startup_policy(hass, notifications, evaluate):
    entry = await setup_alert(hass, evaluate_on_start=evaluate)
    status = entity_id(hass, entry, "sensor", "status")
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    entry.runtime_data.acknowledge(True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_id(hass, entry, "sensor", "status") == status
    assert entry.runtime_data.state == ("on" if evaluate else "idle")
    assert len(notifications) == (2 if evaluate else 1)


async def test_test_button(hass, notifications):
    entry = await setup_alert(hass)
    button = entity_id(hass, entry, "button", "test_notification")
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert len(notifications) == 1
    assert not entry.runtime_data.attempted
