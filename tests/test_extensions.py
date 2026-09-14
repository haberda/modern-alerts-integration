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


async def test_unit_change_suspends_numeric_incident(hass, make_runtime, notifications):
    runtime = make_runtime(numeric_below=15, numeric_recover_above=20, numeric_unit="%")
    await set_state(hass, "14", attributes={"unit_of_measurement": "%"})
    assert runtime.firing
    await set_state(hass, "30", attributes={"unit_of_measurement": "V"})
    assert runtime.firing and runtime.source_suspended and len(notifications) == 1
    await set_state(hass, "30", attributes={"unit_of_measurement": "%"})
    assert not runtime.firing and len(notifications) == 2


@pytest.mark.parametrize("evaluate", [False, True])
async def test_reload_without_restore_starts_fresh(hass, notifications, evaluate):
    entry = await setup_alert(hass, evaluate_on_start=evaluate)
    await set_state(hass, "on")
    entry.runtime_data.acknowledge(True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.state == ("on" if evaluate else "idle")
    assert len(notifications) == (2 if evaluate else 1)


async def test_pending_recovery_survives_reload(hass, notifications, freezer):
    entry = await setup_alert(hass, restore_state=True, recovery_delay=120)
    await set_state(hass, "on")
    await set_state(hass, "off")
    deadline = entry.runtime_data.pending_due
    await advance(hass, freezer, 1)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.pending_due == deadline
    await advance(hass, freezer, 1)
    assert not entry.runtime_data.firing and len(notifications) == 2


async def test_restore_clear_source_resolves_once(hass, notifications, freezer):
    entry = await setup_alert(hass, restore_state=True)
    await set_state(hass, "on")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await set_state(hass, "off")
    await advance(hass, freezer, 60)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not entry.runtime_data.firing
    assert len(notifications) == 2 and notifications[-1].data["message"] == "Closed"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(notifications) == 2


async def test_edits_preserve_deadline_and_reset_changed_condition(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(numeric_below=15, numeric_recover_above=20)
    await set_state(hass, "14")
    deadline = runtime.next_notification
    runtime.acknowledge(True)
    await advance(hass, freezer, 1)
    values = runtime.config.as_dict()
    await runtime.async_update_config(
        AlertConfig.from_dict({**values, "name": "Renamed"}, hass)
    )
    assert runtime.next_notification == deadline and runtime.acknowledged
    await runtime.async_update_config(
        AlertConfig.from_dict({**values, "numeric_below": 10}, hass)
    )
    assert not runtime.firing and runtime.next_notification is None
    assert len(notifications) == 1  # No misleading completion after a condition edit.
    await set_state(hass, "9")
    assert runtime.firing and not runtime.acknowledged


async def test_phone_snooze_permissions_and_disabled_controls(
    hass, notifications, freezer
):
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = await setup_alert(
        hass,
        action_buttons=True,
        can_acknowledge=False,
        snooze_minutes=7,
        notifiers=["mobile_app_phone"],
    )
    await set_state(hass, "on")
    actions = calls[-1].data["data"]["actions"]
    assert [a["title"] for a in actions] == ["Snooze", "Open alert"]
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": actions[0]["action"]}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.snoozed_until == dt_util.utcnow() + timedelta(minutes=7)
    entry.runtime_data.cancel_snooze()
    hass.config_entries.async_update_entry(
        entry, options={**entry.data, "action_buttons": False}
    )
    await hass.async_block_till_done()
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": actions[0]["action"]}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.snoozed_until is None


async def test_device_snooze_and_resume_buttons(hass, notifications):
    entry = await setup_alert(hass)
    await set_state(hass, "on")
    for key in ("snooze", "cancel_snooze", "snooze", "unacknowledge"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id(hass, entry, "button", key)},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert bool(entry.runtime_data.snoozed_until) == (key == "snooze")
    assert len(notifications) == 1


async def test_unload_cancels_pending_activation_and_snooze(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(activation_delay=60)
    await set_state(hass, "on")
    await runtime.async_stop()
    await advance(hass, freezer, 2)
    assert not runtime.firing and not notifications
    other = make_runtime(evaluate_on_start=True)
    await hass.async_block_till_done()
    other.snooze(1)
    await other.async_stop()
    await advance(hass, freezer, 60)
    assert len(notifications) == 1 and other._cancel_timer is None


async def test_missing_numeric_source_and_edit_remain_suspended(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(numeric_below=15, repeat=[1])
    await set_state(hass, "14")
    hass.states.async_remove("binary_sensor.garage")
    await hass.async_block_till_done()
    assert runtime.source_suspended
    await runtime.async_update_config(
        AlertConfig.from_dict({**runtime.config.as_dict(), "name": "Renamed"}, hass)
    )
    await advance(hass, freezer, 10)
    assert runtime.firing and runtime.source_suspended and len(notifications) == 1
    await set_state(hass, "14")
    assert len(notifications) == 2


async def test_disabling_restore_and_deleting_clear_storage(hass, notifications):
    from homeassistant.helpers.storage import Store

    entry = await setup_alert(hass, restore_state=True)
    await set_state(hass, "on")
    await entry.runtime_data.async_save()
    key = f"modern_alerts.{entry.entry_id}"
    saved = await Store(hass, 1, key).async_load()
    assert saved["firing"] and saved["next_notification"]
    hass.config_entries.async_update_entry(
        entry, options={**entry.data, "restore_state": False}
    )
    await hass.async_block_till_done()
    assert await Store(hass, 1, key).async_load() is None
    hass.config_entries.async_update_entry(
        entry, options={**entry.data, "restore_state": True}
    )
    await hass.async_block_till_done()
    await entry.runtime_data.async_save()
    assert await Store(hass, 1, key).async_load() is not None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert await Store(hass, 1, key).async_load() is None


@pytest.mark.parametrize(
    "enabled,ack,snooze,expected",
    [
        (True, True, False, 2),
        (False, True, False, 1),
        (False, False, False, 2),
        (False, False, True, 2),
    ],
)
async def test_resolution_after_ack_policy(
    hass, make_runtime, notifications, enabled, ack, snooze, expected
):
    runtime = make_runtime(resolution_after_ack=enabled)
    await set_state(hass, "on")
    if ack:
        runtime.acknowledge(True)
    if snooze:
        runtime.snooze(5)
    await set_state(hass, "off")
    assert len(notifications) == expected
    assert not runtime.firing
    await set_state(hass, "on")
    assert not runtime.acknowledged
