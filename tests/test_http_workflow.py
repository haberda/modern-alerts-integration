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
