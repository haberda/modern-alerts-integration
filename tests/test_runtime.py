"""Behavioral parity with homeassistant.components.alert in HA 2026.9.2."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.modern_alerts.models import AlertConfig


async def set_state(hass, value, **kwargs):
    hass.states.async_set("binary_sensor.garage", value, **kwargs)
    await hass.async_block_till_done()


async def advance(hass, freezer, minutes):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


@pytest.mark.parametrize("skip_first", [False, True])
async def test_interval_sequence(
    hass, make_runtime, notifications, freezer, skip_first
):
    runtime = make_runtime(repeat=[15, 30, 60], skip_first=skip_first)
    await set_state(hass, "on")
    assert len(notifications) == (0 if skip_first else 1)
    expected = len(notifications)
    for interval in (15, 30, 60, 60):
        await advance(hass, freezer, interval - 0.1)
        assert len(notifications) == expected
        await advance(hass, freezer, 0.1)
        expected += 1
        assert len(notifications) == expected
    assert runtime.state == "on"
    await set_state(hass, "off")
    assert runtime.state == "idle"
    assert notifications[-1].data["message"] == "Closed"
    await advance(hass, freezer, 120)
    assert len(notifications) == expected + 1


async def test_acknowledgement_advances_schedule(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(repeat=[1, 2, 3])
    await set_state(hass, "on")
    runtime.acknowledge(True)
    assert runtime.state == "off"
    await advance(hass, freezer, 1)
    await advance(hass, freezer, 2)
    assert len(notifications) == 1
    runtime.acknowledge(False)
    assert len(notifications) == 1  # No immediate send on resume.
    await advance(hass, freezer, 3)
    assert len(notifications) == 2
    runtime.acknowledge(True)
    await set_state(hass, "off")
    assert notifications[-1].data["message"] == "Closed"
    await set_state(hass, "on")
    assert runtime.state == "on"
    assert not runtime.acknowledged
    assert runtime.next_notification == dt_util.utcnow() + timedelta(minutes=1)


@pytest.mark.parametrize("acknowledge", [False, True])
async def test_no_done_before_first_attempt(
    hass, make_runtime, notifications, freezer, acknowledge
):
    runtime = make_runtime(skip_first=True, repeat=[1])
    await set_state(hass, "on")
    if acknowledge:
        runtime.acknowledge(True)
        await advance(hass, freezer, 3)
    await set_state(hass, "off")
    await advance(hass, freezer, 4)
    assert notifications == []


async def test_ack_disallowed(hass, make_runtime):
    runtime = make_runtime(can_acknowledge=False)
    await set_state(hass, "on")
    with pytest.raises(ServiceValidationError):
        runtime.acknowledge(True)
    assert runtime.state == "on"


async def test_attributes_do_not_restart(hass, make_runtime, notifications, freezer):
    runtime = make_runtime()
    await set_state(hass, "on")
    deadline = runtime.next_notification
    await set_state(hass, "on", attributes={"battery": 42})
    assert len(notifications) == 1
    assert runtime.next_notification == deadline


@pytest.mark.parametrize("value", ["off", "unknown", "unavailable"])
async def test_nonmatching_state_resolves(hass, make_runtime, notifications, value):
    runtime = make_runtime()
    await set_state(hass, "on")
    await set_state(hass, value)
    assert runtime.state == "idle"
    assert len(notifications) == 2


async def test_removed_source_ignored(hass, make_runtime):
    runtime = make_runtime()
    await set_state(hass, "on")
    hass.states.async_remove("binary_sensor.garage")
    await hass.async_block_till_done()
    assert runtime.state == "on"


@pytest.mark.parametrize("evaluate", [False, True])
async def test_existing_state_startup(hass, make_runtime, notifications, evaluate):
    await set_state(hass, "on")
    runtime = make_runtime(evaluate_on_start=evaluate)
    await hass.async_block_till_done()
    assert runtime.state == ("on" if evaluate else "idle")
    assert len(notifications) == int(evaluate)


async def test_fractional_repeat_and_custom_state(
    hass, make_runtime, notifications, freezer
):
    make_runtime(state="problem", repeat=0.5, done_message=None)
    await set_state(hass, "problem")
    await advance(hass, freezer, 0.5)
    await set_state(hass, "healthy")
    assert len(notifications) == 2


async def test_unload_cancels_everything(hass, make_runtime, notifications, freezer):
    runtime = make_runtime()
    await set_state(hass, "on")
    await runtime.async_stop()
    await set_state(hass, "off")
    await set_state(hass, "on")
    await advance(hass, freezer, 60)
    assert len(notifications) == 1
    assert runtime.next_notification is None


async def test_edit_preserves_ack_but_condition_edit_rearms(hass, make_runtime):
    runtime = make_runtime()
    await set_state(hass, "on")
    runtime.acknowledge(True)
    data = runtime.config.as_dict()
    data.update(name="New name", repeat=[5])
    await runtime.async_update_config(AlertConfig.from_dict(data, hass))
    assert runtime.state == "off"
    assert runtime.config.name == "New name"
    data["state"] = "problem"
    await runtime.async_update_config(AlertConfig.from_dict(data, hass))
    assert runtime.state == "idle"
    await set_state(hass, "problem")
    assert runtime.state == "on"


async def test_slow_provider_does_not_block_resolution(hass, make_runtime):
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def slow(call):
        calls.append(call.data["message"])
        started.set()
        await finish.wait()

    hass.services.async_register("notify", "slow", slow)
    runtime = make_runtime(notifiers=["slow"])
    resolved = asyncio.Event()
    runtime.subscribe(lambda: resolved.set() if runtime.state == "idle" else None)
    hass.states.async_set("binary_sensor.garage", "on")
    await started.wait()
    hass.states.async_set("binary_sensor.garage", "off")
    # Wait for the transition, not the intentionally blocked provider task.
    await resolved.wait()
    assert runtime.state == "idle"
    finish.set()
    await hass.async_block_till_done()
    assert calls == ["Garage", "Closed"]


async def test_test_notification_does_not_arm_done(hass, make_runtime, notifications):
    runtime = make_runtime(skip_first=True)
    await runtime.async_test_notification()
    await set_state(hass, "on")
    await set_state(hass, "off")
    assert len(notifications) == 1
    assert runtime.last_attempt is None


async def test_queued_old_incident_cannot_suppress_new_incident(
    hass, make_runtime, notifications
):
    runtime = make_runtime()
    # Hold delivery so these transitions happen before a provider is called,
    # even when Home Assistant uses eagerly started asyncio tasks.
    await runtime._delivery_lock.acquire()
    runtime._evaluate("on")
    runtime._evaluate("off")
    runtime._evaluate("on")
    runtime._delivery_lock.release()
    await hass.async_block_till_done()
    assert [call.data["message"] for call in notifications] == ["Garage"]
    assert runtime.state == "on"


async def test_stale_timer_after_resolution_or_edit_is_ignored(
    hass, make_runtime, notifications
):
    callbacks = []

    def timer(hass, action, when):
        callbacks.append(action)
        return lambda: None

    with patch(
        "custom_components.modern_alerts.runtime.async_track_point_in_utc_time", timer
    ):
        runtime = make_runtime()
        await set_state(hass, "on")
        old_callback = callbacks[0]
        await set_state(hass, "off")
        await set_state(hass, "on")
        old_callback(dt_util.utcnow())
        await hass.async_block_till_done()
        assert len(notifications) == 3
        old_callback = callbacks[-1]
        await runtime.async_update_config(
            AlertConfig.from_dict({**runtime.config.as_dict(), "repeat": [1]}, hass)
        )
        old_callback(dt_util.utcnow())
        await hass.async_block_till_done()
        assert len(notifications) == 3


async def test_template_failure_retries_at_next_interval(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(
        message="{{ 10 / (states('sensor.divisor') | float) }}", repeat=[1]
    )
    hass.states.async_set("sensor.divisor", "0")
    await set_state(hass, "on")
    assert runtime.errors == {"template": "render_failed"}
    assert runtime.next_notification is not None
    hass.states.async_set("sensor.divisor", "2")
    await advance(hass, freezer, 1)
    assert runtime.errors == {}
    assert notifications[-1].data["message"] == "5.0"


async def test_unload_cancels_slow_notification(hass, make_runtime):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(call):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    hass.services.async_register("notify", "slow", slow)
    runtime = make_runtime(notifiers=["slow"])
    hass.states.async_set("binary_sensor.garage", "on")
    await started.wait()
    await runtime.async_stop()
    assert cancelled.is_set()
    assert not runtime._tasks


async def test_disabling_acknowledgement_on_edit_resumes(hass, make_runtime):
    runtime = make_runtime()
    await set_state(hass, "on")
    runtime.acknowledge(True)
    await runtime.async_update_config(
        AlertConfig.from_dict(
            {**runtime.config.as_dict(), "can_acknowledge": False}, hass
        )
    )
    assert runtime.state == "on"


async def test_test_notification_failure_is_visible(hass, make_runtime):
    runtime = make_runtime(notifiers=["missing"])
    with pytest.raises(ServiceValidationError):
        await runtime.async_test_notification()
    assert runtime.errors == {"notify.missing": "ServiceNotFound"}
    assert runtime.state == "idle"
    assert not runtime.attempted
