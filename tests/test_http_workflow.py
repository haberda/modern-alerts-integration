"""Create, edit, and delete using the HTTP endpoints consumed by the HA UI."""

from homeassistant.components.config import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.modern_alerts.const import DOMAIN


async def test_frontend_workflow(hass, hass_client, notifications):
    assert await async_setup_component(hass, "http", {})
    config_entries.async_setup(hass)
    client = await hass_client()
    hass.states.async_set("binary_sensor.garage", "off")

    response = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}
    )
    assert response.status == 200
    result = await response.json()
    assert result["step_id"] == "user"

    async def submit(result, values, *, options=False):
        endpoint = "options/flow" if options else "flow"
        response = await client.post(
            f"/api/config/config_entries/{endpoint}/{result['flow_id']}", json=values
        )
        assert response.status == 200, await response.text()
        return await response.json()

    result = await submit(
        result, {"name": "Garage", "entity_id": "binary_sensor.garage", "state": "on"}
    )
    assert result["data_schema"][0]["selector"]["object"]["multiple"]
    result = await submit(
        result,
        {
            "intervals": [{"minutes": 5}],
            "skip_first": False,
            "can_acknowledge": True,
            "evaluate_on_start": False,
        },
    )
    result = await submit(result, {"notifiers": ["phone"]})
    result = await submit(result, {"done_message": "Closed"})
    result = await submit(result, {})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    status = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_status"
    )
    assert hass.states.get(status).state == "idle"
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN, "acknowledge", {"entity_id": status}, blocking=True
    )

    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert response.status == 200
    result = await response.json()
    assert result["step_id"] == "init"
    result = await submit(
        result,
        {"name": "Renamed", "entity_id": "binary_sensor.garage", "state": "on"},
        options=True,
    )
    result = await submit(
        result,
        {
            "intervals": [{"minutes": 10}],
            "skip_first": False,
            "can_acknowledge": True,
            "evaluate_on_start": False,
        },
        options=True,
    )
    result = await submit(result, {"notifiers": ["phone"]}, options=True)
    result = await submit(result, {"done_message": "Closed"}, options=True)
    result = await submit(result, {}, options=True)
    await hass.async_block_till_done()
    assert entry.title == "Renamed"
    assert hass.states.get(status).state == "off"
    assert len(notifications) == 1

    hass.states.async_set("binary_sensor.garage", "off")
    await hass.async_block_till_done()
    assert hass.states.get(status).state == "idle"
    assert notifications[-1].data["message"] == "Closed"
    response = await client.delete(f"/api/config/config_entries/entry/{entry.entry_id}")
    assert response.status == 200
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_numeric_reliability_http_workflow(
    hass, hass_client, notifications, freezer
):
    from test_runtime import advance

    assert await async_setup_component(hass, "http", {})
    config_entries.async_setup(hass)
    client = await hass_client()
    hass.states.async_set("sensor.battery", "30", {"unit_of_measurement": "%"})
    response = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}
    )
    result = await response.json()
    assert {field["name"] for field in result["data_schema"]} >= {
        "numeric_below",
        "numeric_unit",
    }
    for values in [
        {
            "name": "Battery",
            "entity_id": "sensor.battery",
            "numeric_below": 15,
            "numeric_recover_above": 20,
            "numeric_unit": "%",
        },
        {
            "intervals": [{"minutes": 1}],
            "activation_delay": 60,
            "recovery_delay": 60,
            "restore_state": True,
            "unavailable_policy": "suspend",
            "enable_snooze": True,
            "snooze_minutes": 5,
            "action_buttons": True,
        },
        {"notifiers": ["phone"]},
        {"done_message": "Recovered"},
        {},
    ]:
        response = await client.post(
            f"/api/config/config_entries/flow/{result['flow_id']}", json=values
        )
        assert response.status == 200, await response.text()
        result = await response.json()
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data["restore_state"] and entry.data["numeric_below"] == 15
    hass.states.async_set("sensor.battery", "14", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert not entry.runtime_data.firing
    await advance(hass, freezer, 1)
    assert entry.runtime_data.firing and len(notifications) == 1
    # Generic notify actions never receive Companion-only generated data.
    assert "data" not in notifications[-1].data
    hass.states.async_set("sensor.battery", "21", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    await advance(hass, freezer, 1)
    assert (
        not entry.runtime_data.firing
        and notifications[-1].data["message"] == "Recovered"
    )
    response = await client.delete(f"/api/config/config_entries/entry/{entry.entry_id}")
    assert response.status == 200


async def test_output_http_create_edit_test_and_remove(
    hass, hass_client, notifications
):
    from pytest_homeassistant_custom_component.common import async_mock_service
    from test_outputs import settle

    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "api", {})
    config_entries.async_setup(hass)
    client = await hass_client()
    hass.states.async_set("siren.hall", "off")
    on = async_mock_service(hass, "siren", "turn_on")
    off = async_mock_service(hass, "siren", "turn_off")

    async def submit(result, values, options=False):
        path = "options/flow" if options else "flow"
        response = await client.post(
            f"/api/config/config_entries/{path}/{result['flow_id']}", json=values
        )
        assert response.status == 200, await response.text()
        result = await response.json()
        assert not result.get("errors"), result
        return result

    response = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}
    )
    result = await response.json()
    for values in [
        {"name": "Garage", "entity_id": "binary_sensor.garage"},
        {"intervals": [{"minutes": 1}]},
        {"configure_outputs": True},
        {"next_step_id": "output_add"},
        {"type": "siren"},
        {"name": "Hall siren", "entities": ["siren.hall"], "duration": 5},
        {"next_step_id": "messages"},
        {},
        {},
    ]:
        result = await submit(result, values)
    assert result["type"] == "create_entry"
    await settle(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    output_id = entry.data["outputs"][0]["id"]
    status = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_status"
    )
    response = await client.post(
        "/api/services/modern_alerts/test_output",
        json={"entity_id": status, "output_id": output_id},
    )
    assert response.status == 200
    await settle(hass)
    assert len(on) == 1 and not entry.runtime_data.attempted and not notifications
    response = await client.post(
        "/api/services/modern_alerts/stop_outputs", json={"entity_id": status}
    )
    assert response.status == 200
    await settle(hass)
    assert len(off) == 1
    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    result = await response.json()
    for values in [
        {"name": "Garage", "entity_id": "binary_sensor.garage"},
        {"intervals": [{"minutes": 1}]},
        {"configure_outputs": True},
        {"next_step_id": "output_edit"},
        {"output_id": output_id},
        {"name": "Renamed siren", "entities": ["siren.hall"], "duration": 2},
        {"next_step_id": "messages"},
        {},
        {},
    ]:
        result = await submit(result, values, options=True)
    await settle(hass)
    assert entry.runtime_data.config.outputs[0]["id"] == output_id
    assert entry.runtime_data.config.outputs[0]["duration"] == 2
    runtime = entry.runtime_data
    response = await client.delete(f"/api/config/config_entries/entry/{entry.entry_id}")
    assert response.status == 200
    await settle(hass)
    assert not runtime.outputs._tasks


async def test_profiles_and_delivery_policy_http_workflow(
    hass, hass_client, notifications
):
    from test_outputs import settle

    assert await async_setup_component(hass, "http", {})
    config_entries.async_setup(hass)
    client = await hass_client()

    async def start():
        response = await client.post(
            "/api/config/config_entries/flow", json={"handler": DOMAIN}
        )
        assert response.status == 200
        return await response.json()

    async def submit(result, values):
        response = await client.post(
            f"/api/config/config_entries/flow/{result['flow_id']}", json=values
        )
        assert response.status == 200, await response.text()
        result = await response.json()
        assert not result.get("errors"), result
        return result

    result = await start()
    result = await submit(result, {"name": "Household", "kind": "profile"})
    assert result["step_id"] == "notifications"
    result = await submit(result, {"notifiers": ["phone"]})
    assert result["step_id"] == "profile_review"
    result = await submit(result, {})
    await settle(hass)
    profile = next(
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data["kind"] == "profile"
    )
    assert profile.runtime_data._stopped

    result = await start()
    result = await submit(result, {"name": "Door", "entity_id": "binary_sensor.garage"})
    result = await submit(
        result, {"intervals": [{"minutes": 30}], "configure_delivery": True}
    )
    profile_field = next(
        field for field in result["data_schema"] if field["name"] == "profile_ids"
    )
    assert (
        profile_field["selector"]["select"]["options"][0]["value"] == profile.entry_id
    )
    result = await submit(result, {"profile_ids": [profile.entry_id]})
    result = await submit(result, {"done_message": "Closed"})
    assert result["step_id"] == "delivery"
    result = await submit(
        result,
        {
            "quiet_start": "22:00:00",
            "quiet_end": "07:00:00",
            "presence_entities": ["person.dan"],
            "group": "doors",
            "group_window": 15,
            "rate_limit": 5,
            "history_limit": 25,
            "stages": [
                {"name": "Urgent", "after": 5, "interval": 2, "notifiers": ["phone"]}
            ],
        },
    )
    assert result["step_id"] == "review"
    result = await submit(result, {})
    await settle(hass)
    entry = next(
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data["kind"] == "alert"
    )
    assert entry.runtime_data.config.stages[0]["after"] == 5
    assert entry.runtime_data.config.delivery["group"] == "doors"
    assert entry.runtime_data.config.history_limit == 25
    assert entry.runtime_data.effective_config.notifiers == ("phone",)
    for current in (entry, profile):
        response = await client.delete(
            f"/api/config/config_entries/entry/{current.entry_id}"
        )
        assert response.status == 200
