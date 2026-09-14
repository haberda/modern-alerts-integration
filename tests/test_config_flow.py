"""Exercise the actual config/options managers and their serialized UI forms."""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import config_validation as cv
from probatio import to_field_list
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.models import AlertConfig


async def begin(hass):
    hass.states.async_set("binary_sensor.garage", "off")
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def configure(manager, result, data):
    return await manager.async_configure(result["flow_id"], data)


def assert_form(result, step):
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    # The frontend consumes this serialization, not the Python schema directly.
    to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)


async def test_full_creation(hass, notifications):
    manager = hass.config_entries.flow
    result = await begin(hass)
    assert_form(result, "user")
    result = await configure(
        manager,
        result,
        {"name": "Garage", "entity_id": "binary_sensor.garage", "state": "on"},
    )
    assert_form(result, "timing")
    result = await configure(
        manager,
        result,
        {
            "intervals": [{"minutes": 15}, {"minutes": 30}, {"minutes": 60}],
            "skip_first": True,
            "can_acknowledge": True,
            "evaluate_on_start": False,
        },
    )
    assert_form(result, "notifications")
    result = await configure(manager, result, {"notifiers": ["phone"]})
    assert_form(result, "messages")
    result = await configure(
        manager,
        result,
        {
            "message": "Open",
            "title": "Garage",
            "done_message": "Closed",
            "data": {"tag": "garage"},
        },
    )
    assert_form(result, "review")
    assert result["description_placeholders"]["schedule"] == "15, 30, 60"
    assert notifications == []
    with patch("custom_components.modern_alerts.async_setup_entry", return_value=True):
        result = await configure(manager, result, {})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    assert result["data"]["repeat"] == [15, 30, 60]
    assert result["data"]["notify_entities"] == []


async def test_validation_and_retry(hass):
    manager = hass.config_entries.flow
    result = await begin(hass)
    result = await configure(
        manager,
        result,
        {"name": "", "entity_id": "binary_sensor.garage", "state": "on"},
    )
    assert result["errors"] == {"name": "required"}
    result = await configure(
        manager,
        result,
        {"name": "Garage", "entity_id": "binary_sensor.garage", "state": "on"},
    )
    result = await configure(manager, result, {"intervals": []})
    assert result["errors"] == {"intervals": "invalid_repeat"}
    result = await configure(manager, result, {"intervals": [{"minutes": 1}]})
    result = await configure(manager, result, {"notifiers": ["notify"]})
    assert result["errors"] == {"notifiers": "invalid_destination"}
    result = await configure(manager, result, {})
    # TemplateSelector rejects syntax before the flow step is invoked. The HTTP
    # flow API turns this into a field error for the frontend.
    with pytest.raises(InvalidData):
        await configure(manager, result, {"message": "{{ broken"})
    result = await configure(manager, result, {"data": []})
    assert result["errors"] == {"data": "invalid_data"}
    result = await configure(manager, result, {})
    assert_form(result, "review")
    assert "Status only" in result["description_placeholders"]["destinations"]


async def test_entity_data_error(hass):
    manager = hass.config_entries.flow
    result = await begin(hass)
    result = await configure(
        manager,
        result,
        {"name": "Garage", "entity_id": "binary_sensor.garage", "state": "on"},
    )
    result = await configure(manager, result, {"intervals": [{"minutes": 1}]})
    result = await configure(manager, result, {"notify_entities": ["notify.test"]})
    result = await configure(manager, result, {"data": {"tag": "test"}})
    assert result["errors"] == {"data": "entity_data_unsupported"}
    result = await configure(manager, result, {})
    assert_form(result, "review")


async def test_options_clear_optional_fields(hass):
    data = AlertConfig(
        name="Garage",
        entity_id="binary_sensor.garage",
        message="Old",
        title="Old",
        done_message="Old",
        notifiers=("phone",),
    ).as_dict()
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Garage")
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert_form(result, "init")
    manager = hass.config_entries.options
    result = await configure(
        manager,
        result,
        {"name": "Renamed", "entity_id": "binary_sensor.garage", "state": "on"},
    )
    result = await configure(manager, result, {"intervals": [{"minutes": 5}]})
    result = await configure(manager, result, {})
    result = await configure(manager, result, {})
    result = await configure(manager, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["name"] == "Renamed"
    assert entry.options["notifiers"] == []
    assert entry.options["message"] is None
    assert entry.options["done_message"] is None
    assert entry.options["title"] is None


@pytest.mark.parametrize("state", ["problem", "unknown", "1"])
async def test_arbitrary_state_and_duplicate_sources(hass, state):
    entry = MockConfigEntry(domain=DOMAIN, data={"entity_id": "binary_sensor.garage"})
    entry.add_to_hass(hass)
    result = await begin(hass)
    result = await configure(
        hass.config_entries.flow,
        result,
        {"name": "Another alert", "entity_id": "binary_sensor.garage", "state": state},
    )
    assert_form(result, "timing")


async def test_numeric_options_can_be_cleared(hass):
    data = AlertConfig(
        name="Battery",
        entity_id="sensor.battery",
        numeric_below=15,
        numeric_recover_above=20,
        numeric_unit="%",
        restore_state=True,
        snooze_minutes=12,
    ).as_dict()
    entry = MockConfigEntry(domain=DOMAIN, title="Battery", data=data)
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    assert_form(result, "init")
    result = await configure(
        manager,
        result,
        {"name": "Battery", "entity_id": "sensor.battery", "state": "low"},
    )
    result = await configure(
        manager,
        result,
        {"intervals": [{"minutes": 5}], "restore_state": True, "snooze_minutes": 12},
    )
    result = await configure(manager, result, {})
    result = await configure(manager, result, {})
    result = await configure(manager, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["numeric_below"] is None
    assert entry.options["numeric_recover_above"] is None
    assert entry.options["numeric_unit"] is None
    assert entry.options["restore_state"] is True


async def test_invalid_hysteresis_has_field_error(hass):
    manager = hass.config_entries.flow
    result = await begin(hass)
    result = await configure(
        manager,
        result,
        {
            "name": "Battery",
            "entity_id": "sensor.battery",
            "numeric_below": 15,
            "numeric_recover_above": 10,
        },
    )
    assert result["errors"] == {"numeric_recover_above": "incompatible_thresholds"}
    assert_form(result, "user")


async def test_output_editor_all_types_and_remove(hass):
    manager = hass.config_entries.flow
    result = await begin(hass)
    result = await configure(
        manager, result, {"name": "Garage", "entity_id": "binary_sensor.garage"}
    )
    result = await configure(manager, result, {"intervals": [{"minutes": 1}]})
    result = await configure(manager, result, {"configure_outputs": True})
    assert result["step_id"] == "outputs"
    settings = {
        "light": {"entities": ["light.hall"], "color": [255, 0, 0], "pattern": "blink"},
        "siren": {"entities": ["siren.hall"], "tone": "alarm"},
        "tts": {
            "entities": ["media_player.hall"],
            "tts_entity": "tts.local",
            "message": "{{ alert_name }}",
        },
        "audio": {
            "entities": ["media_player.hall"],
            "media_url": "media-source://media_source/local/chime.mp3",
        },
        "custom": {
            "actions": [{"event": "alert_test"}],
            "stop_actions": [{"event": "alert_stop"}],
        },
    }
    for kind, values in settings.items():
        result = await configure(manager, result, {"next_step_id": "output_add"})
        result = await configure(manager, result, {"type": kind})
        assert_form(result, "output_settings")
        result = await configure(
            manager, result, {"name": kind, "duration": 5, **values}
        )
        assert result["step_id"] == "outputs"
    result = await configure(manager, result, {"next_step_id": "output_edit"})
    assert_form(result, "output_edit")
    serialized = to_field_list(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    first_id = serialized[0]["selector"]["select"]["options"][0]["value"]
    result = await configure(manager, result, {"output_id": first_id})
    result = await configure(
        manager,
        result,
        {
            "name": "Renamed light",
            "entities": ["light.hall"],
            "pattern": "steady",
            "duration": 2,
        },
    )
    assert result["step_id"] == "outputs"
    result = await configure(manager, result, {"next_step_id": "output_remove"})
    result = await configure(manager, result, {"output_id": first_id})
    result = await configure(manager, result, {"next_step_id": "messages"})
    result = await configure(manager, result, {})
    assert_form(result, "review")
    with patch("custom_components.modern_alerts.async_setup_entry", return_value=True):
        result = await configure(manager, result, {})
        await hass.async_block_till_done()
    assert len(result["data"]["outputs"]) == 4
    assert {item["type"] for item in result["data"]["outputs"]} == {
        "siren",
        "tts",
        "audio",
        "custom",
    }


async def test_output_editor_validation(hass):
    manager = hass.config_entries.flow
    result = await begin(hass)
    result = await configure(
        manager, result, {"name": "Garage", "entity_id": "binary_sensor.garage"}
    )
    result = await configure(manager, result, {"intervals": [{"minutes": 1}]})
    result = await configure(manager, result, {"configure_outputs": True})
    result = await configure(manager, result, {"next_step_id": "output_add"})
    result = await configure(manager, result, {"type": "custom"})
    result = await configure(
        manager, result, {"name": "Custom", "actions": [{"nonsense": "bad"}]}
    )
    assert result["errors"] == {"actions": "invalid_actions"}
    assert_form(result, "output_settings")
