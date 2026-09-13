"""Controlled-time regressions for the six reliability extensions."""

from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service
from test_integration import entity_id, setup_alert
from test_runtime import advance, set_state

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.models import AlertConfig, InvalidConfig
from custom_components.modern_alerts.runtime import AlertRuntime


async def test_sustained_activation_and_recovery(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(activation_delay=60, recovery_delay=60)
    await set_state(hass, "on")
    await advance(hass, freezer, 0.5)
    await set_state(hass, "off")
    await advance(hass, freezer, 1)
    assert not runtime.firing and not notifications
    await set_state(hass, "on")
    await advance(hass, freezer, 0.5)
    deadline = runtime.pending_due
    await set_state(hass, "on", attributes={"changed": True})
    assert runtime.pending_due == deadline
    await advance(hass, freezer, 0.5)
    assert runtime.firing and len(notifications) == 1
    await set_state(hass, "off")
    await advance(hass, freezer, 0.5)
    await set_state(hass, "on")
    await advance(hass, freezer, 1)
    assert runtime.firing and len(notifications) == 1
    await set_state(hass, "off")
    await advance(hass, freezer, 1)
    assert not runtime.firing and notifications[-1].data["message"] == "Closed"


async def test_suspension_pauses_overdue_without_catchup_burst(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(unavailable_policy="suspend", repeat=[1, 2, 3])
    await set_state(hass, "on")
    deadline = runtime.next_notification
    await set_state(hass, "unavailable")
    await advance(hass, freezer, 60)
    assert runtime.source_suspended and runtime.firing
    assert runtime.next_notification == deadline
    assert len(notifications) == 1
    await set_state(hass, "on")
    assert not runtime.source_suspended and len(notifications) == 2
    assert runtime.next_notification == dt_util.utcnow() + timedelta(minutes=2)
    await set_state(hass, "unknown")
    await set_state(hass, "off")
    assert not runtime.firing and notifications[-1].data["message"] == "Closed"


async def test_snooze_replacement_ack_and_new_incident(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(repeat=[1])
    await set_state(hass, "on")
    runtime.snooze(2)
    assert not runtime.acknowledged
    await advance(hass, freezer, 1)
    runtime.snooze(3)
    await advance(hass, freezer, 1)
    assert runtime.snoozed_until and len(notifications) == 1
    runtime.acknowledge(True)
    await advance(hass, freezer, 3)
    assert runtime.acknowledged and len(notifications) == 1
    runtime.acknowledge(False)
    runtime.snooze(1)
    await advance(hass, freezer, 1)
    assert runtime.snoozed_until is None and len(notifications) == 2
    runtime.snooze(5)
    await set_state(hass, "off")
    await set_state(hass, "on")
    assert runtime.snoozed_until is None and len(notifications) == 4


@pytest.mark.parametrize(
    "minutes", [0, -1, float("nan"), float("inf"), True, "10", 10081]
)
async def test_snooze_rejects_invalid_duration(hass, make_runtime, minutes):
    runtime = make_runtime()
    await set_state(hass, "on")
    with pytest.raises(ServiceValidationError):
        runtime.snooze(minutes)


@pytest.mark.parametrize(
    "values,states",
    [
        ({"numeric_below": 15, "numeric_recover_above": 20}, [14, 15, 20, 21]),
        ({"numeric_above": 25, "numeric_recover_below": 20}, [26, 25, 20, 19]),
    ],
)
async def test_numeric_hysteresis_and_invalid_values(
    hass, make_runtime, notifications, values, states
):
    runtime = make_runtime(**values)
    for value in states[:3]:
        await set_state(hass, str(value))
        assert runtime.firing
    for invalid in ("nan", "inf", "broken", "unavailable"):
        await set_state(hass, invalid)
        assert runtime.firing and runtime.source_suspended
    assert len(notifications) == 1
    await set_state(hass, str(states[-1]))
    assert not runtime.firing and not runtime.source_suspended


@pytest.mark.parametrize(
    "values",
    [
        {"numeric_below": 15, "numeric_above": 20},
        {"numeric_recover_above": 20},
        {"numeric_below": 15, "numeric_recover_above": 10},
        {"numeric_above": 15, "numeric_recover_below": 20},
        {"numeric_below": True},
        {"activation_delay": True},
        {"activation_delay": 1e100},
        {"snooze_minutes": 0},
    ],
)
async def test_extension_validation(hass, values):
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(
            {"name": "Test", "entity_id": "sensor.test", **values}, hass
        )


async def test_restore_deadline_snooze_and_single_overdue(hass, notifications, freezer):
    entry = await setup_alert(hass, restore_state=True, repeat=[1, 2, 3])
    await set_state(hass, "on")
    entry.runtime_data.snooze(2)
    deadline = entry.runtime_data.next_notification
    incident = entry.runtime_data.incident_id
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.next_notification == deadline
    assert entry.runtime_data.incident_id == incident
    assert entry.runtime_data.snoozed_until
    assert await hass.config_entries.async_unload(entry.entry_id)
    await advance(hass, freezer, 60)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(notifications) == 2
    assert entry.runtime_data.snoozed_until is None
    assert entry.runtime_data.next_notification == dt_util.utcnow() + timedelta(
        minutes=2
    )


async def test_restore_waits_for_source_and_keeps_pending_deadline(
    hass, notifications, freezer
):
    entry = await setup_alert(hass, restore_state=True, activation_delay=120)
    await set_state(hass, "on")
    deadline = entry.runtime_data.pending_due
    assert await hass.config_entries.async_unload(entry.entry_id)
    await set_state(hass, "unavailable")
    await advance(hass, freezer, 10)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.source_suspended and not notifications
    assert entry.runtime_data.pending_due == deadline
    await set_state(hass, "on")
    assert entry.runtime_data.firing and len(notifications) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        {"next_index": -1},
        {"next_notification": "bad"},
        {"firing": "true"},
        {"pending_active": "true"},
    ],
)
async def test_corrupt_snapshot_is_atomic(hass, make_runtime, corruption):
    original = make_runtime(restore_state=True)
    await set_state(hass, "on")
    restored = AlertRuntime(hass, original.config)
    restored.restore({**original.snapshot(), **corruption})
    assert not restored.firing and restored.next_notification is None
    assert restored.errors == {"restore": "invalid_snapshot"}


async def test_phone_actions_are_scoped_and_replays_inert(hass, notifications):
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    first = await setup_alert(hass, action_buttons=True, notifiers=["mobile_app_phone"])
    second = await setup_alert(hass, name="Other", action_buttons=True)
    await set_state(hass, "on")
    actions = calls[-1].data["data"]["actions"]
    assert (
        actions[-1]["uri"] == f"entityId:{entity_id(hass, first, 'sensor', 'status')}"
    )
    action = actions[0]["action"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert first.runtime_data.acknowledged and not second.runtime_data.acknowledged
    first.runtime_data.acknowledge(False)
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert not first.runtime_data.acknowledged
    await set_state(hass, "off")
    await set_state(hass, "on")
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": "MODERN_ALERTS_ACK"}
    )
    await hass.async_block_till_done()
    assert not first.runtime_data.acknowledged and not second.runtime_data.acknowledged
    await first.runtime_data.async_test_notification()
    assert "data" not in calls[-1].data


async def test_snooze_service_target_and_cancel(hass, notifications, freezer):
    entry = await setup_alert(hass, snooze_minutes=45)
    await set_state(hass, "on")
    status = entity_id(hass, entry, "sensor", "status")
    await hass.services.async_call(
        DOMAIN, "snooze", {"entity_id": status}, blocking=True
    )
    assert entry.runtime_data.snoozed_until == dt_util.utcnow() + timedelta(minutes=45)
    await hass.services.async_call(
        DOMAIN, "snooze", {"entity_id": status, "minutes": 5}, blocking=True
    )
    assert entry.runtime_data.snoozed_until == dt_util.utcnow() + timedelta(minutes=5)
    await hass.services.async_call(
        DOMAIN, "cancel_snooze", {"entity_id": status}, blocking=True
    )
    assert entry.runtime_data.snoozed_until is None
