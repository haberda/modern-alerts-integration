"""Differential tests against the installed, pinned built-in Alert integration."""

from datetime import timedelta

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)


@pytest.mark.parametrize("repeat", [0.5, [1, 2, 3]])
@pytest.mark.parametrize("skip_first", [False, True])
async def test_same_notifications_and_states_as_builtin(
    hass, make_runtime, notifications, freezer, repeat, skip_first
):
    reference = async_mock_service(hass, "notify", "reference")
    settings = {
        "name": "Garage",
        "entity_id": "binary_sensor.garage",
        "state": "on",
        "repeat": repeat,
        "skip_first": skip_first,
        "message": "Current: {{ states('binary_sensor.garage') }}",
        "title": "{{ 1 + 1 }}",
        "done_message": "Closed",
        "data": {"tag": "garage"},
    }
    assert await async_setup_component(
        hass,
        "alert",
        {"alert": {"reference": {**settings, "notifiers": ["reference"]}}},
    )
    runtime = make_runtime(**settings)

    async def check():
        await hass.async_block_till_done()
        assert runtime.state == hass.states.get("alert.reference").state
        assert [call.data for call in notifications] == [
            call.data for call in reference
        ]

    async def tick(minutes):
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass, dt_util.utcnow())
        await check()

    hass.states.async_set("binary_sensor.garage", "on")
    await check()
    await tick(1)
    for acknowledged in (True, False):
        runtime.acknowledge(acknowledged)
        await hass.services.async_call(
            "alert",
            "turn_off" if acknowledged else "turn_on",
            {"entity_id": "alert.reference"},
            blocking=True,
        )
        await check()
        for delay in (2, 3, 3):
            await tick(delay)
    runtime.acknowledge(True)
    await hass.services.async_call(
        "alert", "turn_off", {"entity_id": "alert.reference"}, blocking=True
    )
    hass.states.async_set("binary_sensor.garage", "off")
    await check()
    hass.states.async_set("binary_sensor.garage", "on")
    await check()
    await tick(1)
    hass.states.async_set("binary_sensor.garage", "off")
    await check()
