"""Escalation, time/presence routing, grouping, profiles, and history."""

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service
from test_integration import setup_alert
from test_outputs import output, settle
from test_runtime import advance as tick
from test_runtime import set_state as source


async def state(hass, value):
    await source(hass, value)
    await settle(hass)


async def advance(hass, freezer, minutes):
    await tick(hass, freezer, minutes)
    await settle(hass)


async def test_stages_add_recipients_and_change_cadence(
    hass, make_runtime, notifications, freezer
):
    second = async_mock_service(hass, "notify", "second")
    runtime = make_runtime(
        repeat=[30],
        stages=[
            {"name": "Escalated", "after": 2, "interval": 1, "notifiers": ["second"]}
        ],
    )
    await state(hass, "on")
    assert len(notifications) == 1 and not second
    await advance(hass, freezer, 2)
    assert runtime.stage_index == 1 and len(second) == 1 and len(notifications) == 2
    await advance(hass, freezer, 1)
    assert len(second) == 2
    runtime.acknowledge(True)
    await advance(hass, freezer, 5)
    assert len(second) == 2


async def test_ack_stops_escalation_snooze_keeps_age(hass, make_runtime, freezer):
    runtime = make_runtime(stages=[{"name": "Later", "after": 2}])
    await state(hass, "on")
    runtime.acknowledge(True)
    await advance(hass, freezer, 5)
    assert runtime.stage_index == 0
    runtime.acknowledge(False)
    runtime.snooze(2)
    await advance(hass, freezer, 2)
    assert runtime.stage_index == 1


async def test_restart_catches_up_to_latest_stage_once(hass, notifications, freezer):
    extra = async_mock_service(hass, "notify", "extra")
    entry = await setup_alert(
        hass,
        restore_state=True,
        stages=[
            {"name": "First", "after": 2},
            {"name": "Second", "after": 4, "notifiers": ["extra"], "interval": 3},
        ],
    )
    await state(hass, "on")
    started = entry.runtime_data.started_at
    await hass.config_entries.async_unload(entry.entry_id)
    await advance(hass, freezer, 20)
    await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert entry.runtime_data.stage_index == 2
    assert entry.runtime_data.started_at == started
    assert len(extra) == 1 and len(notifications) == 2


async def test_quiet_hours_hold_then_one_catchup(
    hass, make_runtime, notifications, freezer
):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-09-14 23:00:00+00:00")
    runtime = make_runtime(
        repeat=[1], delivery={"quiet_start": "22:00:00", "quiet_end": "07:00:00"}
    )
    await state(hass, "on")
    assert not notifications and not runtime.attempted
    await advance(hass, freezer, 60)
    assert not notifications
    await advance(hass, freezer, 7 * 60)
    assert len(notifications) == 1
    assert runtime.attempted


async def test_presence_routes_without_waiting_for_next_reminder(
    hass, make_runtime, notifications
):
    hass.states.async_set("binary_sensor.occupied", "off")
    runtime = make_runtime(delivery={"presence_entities": ["binary_sensor.occupied"]})
    await state(hass, "on")
    assert not notifications
    hass.states.async_set("binary_sensor.occupied", "on")
    await settle(hass)
    assert len(notifications) == 1
    assert runtime.attempted


async def test_group_combines_and_drops_acknowledged_member(
    hass, make_runtime, notifications, freezer
):
    first = make_runtime(
        name="First", message="Open", delivery={"group": "doors", "group_window": 30}
    )
    second = make_runtime(
        name="Second", message="Open", delivery={"group": "doors", "group_window": 30}
    )
    await state(hass, "on")
    assert not notifications and not first.attempted
    await advance(hass, freezer, 0.5)
    assert len(notifications) == 1
    assert notifications[0].data["message"] == "First: Open\nSecond: Open"
    assert first.attempted and second.attempted
    await state(hass, "off")
    await state(hass, "on")
    first.acknowledge(True)
    await advance(hass, freezer, 0.5)
    assert notifications[-1].data["message"] == "Open"


async def test_group_resolved_before_flush_has_no_notification(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(delivery={"group": "doors"})
    await state(hass, "on")
    await state(hass, "off")
    await advance(hass, freezer, 1)
    assert not notifications and not runtime.attempted


async def test_rate_limit_coalesces_repeats(hass, make_runtime, notifications, freezer):
    make_runtime(repeat=[1], delivery={"rate_limit": 5})
    await state(hass, "on")
    await advance(hass, freezer, 0.01)
    assert len(notifications) == 1
    for _ in range(4):
        await advance(hass, freezer, 1)
    assert len(notifications) == 1
    await advance(hass, freezer, 1)
    assert len(notifications) == 2


async def test_profile_edits_are_live_and_do_not_create_alert_entities(
    hass, notifications, freezer
):
    extra = async_mock_service(hass, "notify", "extra")
    profile = await setup_alert(
        hass, kind="profile", name="Shared", notifiers=["extra"]
    )
    assert profile.runtime_data._stopped
    entry = await setup_alert(hass, profile_ids=[profile.entry_id], repeat=[1])
    await state(hass, "on")
    assert len(extra) == 1 and len(notifications) == 1
    hass.config_entries.async_update_entry(
        profile, options={**profile.data, "notifiers": []}
    )
    await settle(hass)
    await advance(hass, freezer, 1)
    assert len(extra) == 1 and len(notifications) == 2
    await hass.config_entries.async_remove(profile.entry_id)
    await advance(hass, freezer, 1)
    assert entry.runtime_data.policy_errors == {"profiles": "unavailable"}


async def test_history_is_bounded_redacted_and_restored(hass, notifications):
    entry = await setup_alert(
        hass, restore_state=True, history_limit=4, message="private message"
    )
    await state(hass, "on")
    entry.runtime_data.acknowledge(True)
    entry.runtime_data.acknowledge(False)
    history = entry.runtime_data.history
    assert len(history) == 4
    assert "private message" not in str(history)
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.runtime_data.history == history


async def test_staged_output_waits_and_presence_stops_it(hass, make_runtime, freezer):
    hass.states.async_set("siren.hall", "off")
    hass.states.async_set("binary_sensor.occupied", "on")
    on = async_mock_service(hass, "siren", "turn_on")
    off = async_mock_service(hass, "siren", "turn_off")
    runtime = make_runtime(
        outputs=[
            output(
                "siren",
                entities=["siren.hall"],
                duration=120,
                delivery={"presence_entities": ["binary_sensor.occupied"]},
            )
        ],
        stages=[{"name": "Loud", "after": 1, "output_ids": ["siren"]}],
    )
    await state(hass, "on")
    assert not on
    await advance(hass, freezer, 1)
    assert len(on) == 1
    hass.states.async_set("binary_sensor.occupied", "off")
    await settle(hass)
    assert len(off) == 1
    assert not runtime.outputs.active


@pytest.mark.parametrize(
    "raw",
    [
        {"stages": [{"name": "Bad", "after": -1}]},
        {"delivery": {"quiet_start": "22:00"}},
        {"delivery": {"rate_limit": float("nan")}},
        {"history_limit": -1},
    ],
)
async def test_policy_validation(hass, raw):
    from custom_components.modern_alerts.models import AlertConfig, InvalidConfig

    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict({"name": "Test", "entity_id": "sensor.test", **raw}, hass)
