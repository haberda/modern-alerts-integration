"""Read-only delivery explanations and authenticated overview transport."""

from copy import deepcopy

from homeassistant.auth.permissions import PolicyPermissions
from homeassistant.auth.permissions.const import CAT_ENTITIES
from test_integration import setup_alert
from test_runtime import set_state

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.preview import explain


async def test_preview_has_no_incident_or_delivery_side_effects(
    hass, make_runtime, notifications
):
    runtime = make_runtime(
        message="Value {{ states('sensor.value') }}",
        delivery={"presence_entities": ["person.test"]},
    )
    hass.states.async_set("sensor.value", "42")
    await set_state(hass, "on")
    before = deepcopy(runtime.snapshot())
    errors = deepcopy(runtime.policy_errors)
    result = explain(runtime, render=True)
    assert result["preview"]["message"] == "Value 42"
    assert result["notification_blockers"] == ["presence"]
    assert runtime.snapshot() == before and runtime.policy_errors == errors
    assert not notifications
    runtime.acknowledge(True)
    result = explain(runtime)
    assert "acknowledged" in result["notification_blockers"]
    assert result["next_repeat_opportunity"] is None


async def test_overview_and_preview_websocket(hass, hass_ws_client, notifications):
    entry = await setup_alert(hass, message="Example preview")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "modern_alerts/overview"})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"][0]["entry_id"] == entry.entry_id
    assert response["result"][0]["notification_blockers"][0] == "condition_inactive"
    await client.send_json(
        {"id": 2, "type": "modern_alerts/preview", "entry_id": entry.entry_id}
    )
    response = await client.receive_json()
    assert (
        response["success"]
        and response["result"]["preview"]["message"] == "Example preview"
    )
    assert not notifications
    await client.send_json(
        {"id": 3, "type": "modern_alerts/preview", "entry_id": "missing"}
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_panel_registered_removed_and_reloaded(hass, hass_client):
    entry = await setup_alert(hass)
    assert DOMAIN in hass.data["frontend_panels"]
    client = await hass_client()
    response = await client.get("/modern_alerts_static/overview.js")
    assert response.status == 200
    assert "modern-alerts-overview" in await response.text()
    await hass.config_entries.async_unload(entry.entry_id)
    assert DOMAIN not in hass.data["frontend_panels"]
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data["frontend_panels"]


async def test_non_admin_preview_denied_and_overview_filtered(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    await setup_alert(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json(
        {"id": 1, "type": "modern_alerts/preview", "entry_id": "anything"}
    )
    assert (await client.receive_json())["error"]["code"] == "unauthorized"
    # Entity permissions are applied before exposing any alert metadata.
    hass_read_only_user.permissions = PolicyPermissions(
        {CAT_ENTITIES: False}, hass_read_only_user.perm_lookup
    )
    await client.send_json({"id": 2, "type": "modern_alerts/overview"})
    response = await client.receive_json()
    assert response["success"] and response["result"] == []


async def test_preview_respects_literal_name_and_output_stage(hass, make_runtime):
    from test_outputs import output

    runtime = make_runtime(
        name="Literal {{ not_a_template }}",
        outputs=[output("siren", entities=["siren.hall"])],
        stages=[{"name": "Loud", "after": 5, "output_ids": ["siren"]}],
    )
    result = explain(runtime, render=True)
    assert result["preview"]["message"] == "Literal {{ not_a_template }}"
    assert "waiting_for_escalation" in result["outputs"][0]["blockers"]
