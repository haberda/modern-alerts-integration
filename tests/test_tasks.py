"""Recurring tasks: schedule maths, completion paths, restore, and edits."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from test_integration import entity_id, setup_alert

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.modern_alerts.models import AlertConfig, InvalidConfig
from custom_components.modern_alerts.schedule import TASK_ENTITY

TZ = ZoneInfo("America/Chicago")


def local(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


@pytest.fixture(autouse=True)
async def chicago(hass):
    await hass.config.async_set_time_zone("America/Chicago")


async def move(hass, freezer, text):
    freezer.move_to(local(text))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def filter_task(**overrides):
    return {
        "source": "schedule",
        "name": "RO filter",
        "task_mode": "after_completion",
        "task_interval": 180 * 1440,
        "repeat": [60],
        **overrides,
    }


def garbage(**overrides):
    return {
        "source": "schedule",
        "name": "Garbage",
        "task_mode": "fixed",
        "task_rrule": "FREQ=WEEKLY;BYDAY=TU",
        "task_time": "17:00",
        "task_window": 300,
        "repeat": [30],
        **overrides,
    }


async def test_after_completion_schedule_and_backdated_completion(
    hass, make_runtime, notifications, freezer
):
    freezer.move_to(local("2026-05-23 09:30"))
    runtime = make_runtime(**filter_task())
    # 180 days from today, at the default noon rather than at 09:30.
    assert runtime.next_due == local("2026-11-19 12:00")
    assert runtime.days_remaining == 180 and not runtime.firing
    await move(hass, freezer, "2026-11-19 11:59")
    assert not runtime.firing and not notifications
    await move(hass, freezer, "2026-11-19 12:00")
    assert runtime.firing and len(notifications) == 1
    await move(hass, freezer, "2026-11-21 12:00")
    assert runtime.days_remaining == -2
    runtime.complete(completed_at=local("2026-11-20 18:45"), via="service")
    assert not runtime.firing and notifications[-1].data["message"] == "Closed"
    assert runtime.last_completed == local("2026-11-20 18:45")
    assert runtime.next_due == local("2027-05-19 12:00")
    with pytest.raises(ServiceValidationError):
        runtime.complete(completed_at=dt_util.utcnow() + timedelta(days=1))


async def test_short_interval_is_exact_and_backdate_can_be_due_again(
    hass, make_runtime, notifications, freezer
):
    freezer.move_to(local("2026-06-01 08:00"))
    runtime = make_runtime(**filter_task(task_interval=90))
    assert runtime.next_due == local("2026-06-01 09:30")
    await move(hass, freezer, "2026-06-01 09:30")
    assert runtime.firing
    first = runtime.incident_id
    runtime.complete(completed_at=local("2026-06-01 07:00"))
    # 07:00 + 90 minutes is already in the past: a new occurrence starts.
    assert runtime.firing and runtime.incident_id != first


async def test_fixed_window_nags_then_records_a_miss(
    hass, make_runtime, notifications, freezer
):
    freezer.move_to(local("2026-11-13 10:00"))  # A Friday.
    runtime = make_runtime(**garbage())
    assert runtime.next_due == local("2026-11-17 17:00")
    await move(hass, freezer, "2026-11-17 17:00")
    assert runtime.firing and len(notifications) == 1
    assert runtime.window_end == local("2026-11-17 22:00")
    await move(hass, freezer, "2026-11-17 17:30")
    await move(hass, freezer, "2026-11-17 18:00")
    assert len(notifications) == 3
    await move(hass, freezer, "2026-11-17 22:00")
    # Nobody did it: no "done" message, no nagging outside the window.
    assert not runtime.firing and runtime.missed == 1 and len(notifications) == 3
    assert [item["event"] for item in runtime.history].count("missed") == 1
    assert runtime.next_due == local("2026-11-24 17:00")
    await move(hass, freezer, "2026-11-23 09:00")
    assert len(notifications) == 3
    await move(hass, freezer, "2026-11-24 17:00")
    runtime.complete(via="button")
    assert notifications[-1].data["message"] == "Closed" and runtime.missed == 1
    assert runtime.next_due == local("2026-12-01 17:00")


async def test_fixed_schedule_keeps_local_time_across_dst(hass, make_runtime, freezer):
    freezer.move_to(local("2026-10-28 09:00"))
    runtime = make_runtime(**garbage())
    before = runtime.next_due
    assert before == local("2026-11-03 17:00")  # DST ended on 1 November.
    assert dt_util.as_local(before).hour == 17
    assert before.astimezone(dt_util.UTC).hour == 23
    await move(hass, freezer, "2026-10-27 17:00")
    freezer.move_to(local("2026-03-04 09:00"))
    spring = make_runtime(**garbage(name="Spring"))
    spring.complete()  # Not due and no lead time: the schedule is unchanged.
    assert spring.next_due == local("2026-03-10 17:00")
    assert spring.next_due.astimezone(dt_util.UTC).hour == 22  # CDT began 8 March.
    daily = make_runtime(
        **filter_task(name="Daily", task_interval=1440, task_time="02:30")
    )
    freezer.move_to(local("2026-03-07 10:00"))
    daily.complete()
    # 02:30 does not exist on 8 March; the task still lands on that morning.
    assert dt_util.as_local(daily.next_due).date().isoformat() == "2026-03-08"


async def test_fixed_without_window_stays_due_and_skips_stale_occurrences(
    hass, make_runtime, notifications, freezer
):
    freezer.move_to(local("2026-11-13 10:00"))
    runtime = make_runtime(**garbage(task_window=None, task_lead_days=1))
    await move(hass, freezer, "2026-11-27 08:00")
    assert runtime.firing and runtime.missed == 0
    assert runtime.next_due == local("2026-11-17 17:00")
    runtime.complete()
    assert runtime.next_due == local("2026-12-01 17:00")
    assert not runtime.upcoming
    await move(hass, freezer, "2026-11-30 18:00")
    assert runtime.upcoming and not runtime.firing
    runtime.complete()  # Done early, inside the lead time: skip that Tuesday.
    assert runtime.next_due == local("2026-12-08 17:00")


async def test_once_finishes_permanently(hass, make_runtime, notifications, freezer):
    freezer.move_to(local("2026-11-13 10:00"))
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(filter_task(task_mode="once"), hass)
    runtime = make_runtime(
        **filter_task(task_mode="once", task_first_due="2026-11-14 08:00:00")
    )
    assert runtime.next_due == local("2026-11-14 08:00")
    await move(hass, freezer, "2026-11-14 08:00")
    assert runtime.firing
    runtime.complete()
    assert not runtime.firing and runtime.task_done and runtime.next_due is None
    await move(hass, freezer, "2027-11-14 08:00")
    assert not runtime.firing and len(notifications) == 2


async def test_acknowledge_is_not_completion(
    hass, make_runtime, notifications, freezer
):
    freezer.move_to(local("2026-05-23 09:30"))
    runtime = make_runtime(**filter_task())
    await move(hass, freezer, "2026-11-19 12:00")
    due = runtime.next_due
    runtime.acknowledge(True)
    await move(hass, freezer, "2026-11-22 12:00")
    assert runtime.firing and runtime.state == "off" and runtime.next_due == due
    assert runtime.days_remaining == -3 and len(notifications) == 1
    runtime.complete()
    assert not runtime.firing and runtime.next_due == local("2027-05-21 12:00")
    events = [item["event"] for item in runtime.history]
    assert events.index("completed") < events.index("resolved")
    assert notifications[-1].data["message"] == "Closed"


async def test_entity_alert_cannot_be_completed(hass, make_runtime):
    runtime = make_runtime()
    assert not runtime.is_task and runtime.next_due is None
    with pytest.raises(ServiceValidationError):
        runtime.complete()


@pytest.mark.parametrize(
    "bad",
    [
        {"task_interval": 0},
        {"task_interval": None},
        {"task_mode": "sometimes"},
        {"task_mode": "fixed", "task_rrule": "FREQ=MINUTELY"},
        {"task_mode": "fixed", "task_rrule": "nonsense"},
        {"task_mode": "fixed"},
        {"task_time": "25:00"},
        {"task_first_due": "tomorrow"},
        {"task_lead_days": 400},
        {"task_tags": ["a"] * 21},
        {"task_legacy_actions": ["MODERN_ALERTS:x"]},
        {"kind": "profile"},
    ],
)
async def test_invalid_task_configuration(hass, bad):
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(filter_task(**bad), hass)


async def test_task_config_roundtrip_and_entity_defaults(hass):
    task = AlertConfig.from_dict(
        garbage(task_tags=["tag-1", "tag-1"], task_interval={"days": 3}), hass
    )
    assert task.entity_id == TASK_ENTITY and task.task_interval is None
    assert task.task_tags == ("tag-1",) and task.task_time == "17:00:00"
    assert AlertConfig.from_dict(task.as_dict(), hass) == task
    legacy = AlertConfig.from_dict(
        {"name": "Garage", "entity_id": "binary_sensor.garage"}, hass
    )
    assert legacy.source == "entity" and not legacy.is_task


async def setup_task(hass, **overrides):
    return await setup_alert(hass, **{"entity_id": TASK_ENTITY, **overrides})


async def test_entities_and_every_completion_path(
    hass, notifications, freezer, hass_admin_user
):
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    freezer.move_to(local("2026-11-13 10:00"))
    entry = await setup_task(
        hass,
        **filter_task(
            task_interval=1440,
            task_tags=["tag-ro"],
            task_legacy_actions=["rocomplete"],
            notifiers=["mobile_app_phone"],
            action_buttons=True,
        ),
    )
    other = await setup_task(hass, **filter_task(name="Furnace", task_interval=1440))
    runtime = entry.runtime_data
    status = entity_id(hass, entry, "sensor", "status")
    next_due = entity_id(hass, entry, "sensor", "next_due")
    days = entity_id(hass, entry, "sensor", "days_remaining")
    done = entity_id(hass, entry, "button", "complete")
    # The task name appears once, from the device, never doubled.
    assert (status, next_due, days, done) == (
        "sensor.ro_filter_status",
        "sensor.ro_filter_next_due",
        "sensor.ro_filter_days_remaining",
        "button.ro_filter_done",
    )
    assert hass.states.get(days).state == "1"
    state = hass.states.get(next_due)
    assert dt_util.parse_datetime(state.state) == local("2026-11-14 12:00")
    assert state.attributes["mode"] == "after_completion"
    assert state.attributes["upcoming"] is False

    async def due():
        freezer.move_to(runtime.next_due)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert runtime.firing and other.runtime_data.firing
        assert hass.states.get(
            entity_id(hass, entry, "binary_sensor", "problem")
        ).state == ("on")
        other.runtime_data.complete()

    # 1. Action, with a back-dated time; targeting every sensor applies once.
    await due()
    context = Context(user_id=hass_admin_user.id)
    await hass.services.async_call(
        DOMAIN,
        "complete",
        {"entity_id": [status, next_due, days], "completed_at": "2026-11-14 09:00:00"},
        blocking=True,
        context=context,
    )
    assert not runtime.firing and runtime.last_completed == local("2026-11-14 09:00")
    assert runtime.completed_by == hass_admin_user.name
    assert [item["event"] for item in runtime.history].count("completed") == 1
    assert hass.states.get(next_due).attributes["completed_by"] == hass_admin_user.name

    # 2. Done button.
    await due()
    await hass.services.async_call(
        "button", "press", {"entity_id": done}, blocking=True
    )
    await hass.async_block_till_done()
    completions = [item for item in runtime.history if item["event"] == "completed"]
    assert not runtime.firing and completions[-1]["via"] == "button"

    # 3. Phone action, scoped to this incident.
    await due()
    actions = calls[-1].data["data"]["actions"]
    assert [item["title"] for item in actions] == ["Done", "Acknowledge", "Snooze"]
    stale = actions[0]["action"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": stale})
    await hass.async_block_till_done()
    assert not runtime.firing and runtime.completed_by == "phone_action"

    # 4. NFC tag; a stale phone button from the previous incident is ignored.
    await due()
    hass.bus.async_fire("mobile_app_notification_action", {"action": stale})
    hass.bus.async_fire("tag_scanned", {"tag_id": "someone-elses-tag"})
    await hass.async_block_till_done()
    assert runtime.firing
    hass.bus.async_fire("tag_scanned", {"tag_id": "tag-ro"})
    await hass.async_block_till_done()
    assert not runtime.firing and runtime.completed_by == "tag"

    # 5. Legacy action ID from an old notification or shortcut.
    await due()
    count = [item["event"] for item in other.runtime_data.history].count("completed")
    hass.bus.async_fire("mobile_app_notification_action", {"action": "rocomplete"})
    await hass.async_block_till_done()
    assert not runtime.firing and runtime.completed_by == "legacy_action"
    assert [item["event"] for item in other.runtime_data.history].count(
        "completed"
    ) == count

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["task"]["completed_by"] == "**REDACTED**"
    assert diagnostics["task"]["tags"] == 1 and "tag-ro" not in str(diagnostics)


async def test_complete_action_rejects_entity_alert(hass, notifications):
    entry = await setup_alert(hass)
    assert entity_id(hass, entry, "sensor", "next_due") is None
    assert entity_id(hass, entry, "button", "complete") is None
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "complete",
            {"entity_id": entity_id(hass, entry, "sensor", "status")},
            blocking=True,
        )


async def test_schedule_survives_restart_without_restore_state(
    hass, notifications, freezer
):
    freezer.move_to(local("2026-05-23 09:30"))
    entry = await setup_task(hass, **filter_task())
    entry.runtime_data.complete(completed_at=local("2026-05-20 08:00"))
    due = entry.runtime_data.next_due
    assert due == local("2026-11-16 12:00")
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(local("2026-08-01 09:00"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    assert runtime.next_due == due and not runtime.firing
    assert runtime.last_completed == local("2026-05-20 08:00")
    # Home Assistant was down at the due time: the task fires on startup.
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(local("2026-11-18 07:00"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.firing and len(notifications) == 1
    assert entry.runtime_data.next_due == due


async def test_restore_mid_window_and_after_missed_windows(
    hass, notifications, freezer
):
    freezer.move_to(local("2026-11-13 10:00"))
    entry = await setup_task(hass, **garbage(restore_state=True))
    await move(hass, freezer, "2026-11-17 17:00")
    incident = entry.runtime_data.incident_id
    assert entry.runtime_data.firing and len(notifications) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(local("2026-11-17 17:10"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    # Same incident, no duplicate notification, window still closes on time.
    assert runtime.firing and runtime.incident_id == incident
    assert len(notifications) == 1
    assert runtime.window_end == local("2026-11-17 22:00")
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(local("2026-12-02 09:00"))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    # 17 Nov (open at shutdown), 24 Nov and 1 Dec all closed unanswered.
    assert not runtime.firing and runtime.missed == 3 and len(notifications) == 1
    assert runtime.next_due == local("2026-12-08 17:00")
    assert runtime.history[-2]["event"] == "missed"
    assert runtime.history[-2]["count"] == 3


async def test_schedule_edit_recomputes_and_keeps_history(hass, notifications, freezer):
    freezer.move_to(local("2026-05-23 09:30"))
    entry = await setup_task(hass, **filter_task())
    runtime = entry.runtime_data
    runtime.complete(completed_at=local("2026-05-20 08:00"))
    history = list(runtime.history)

    async def edit(**changes):
        hass.config_entries.async_update_entry(
            entry, options={**runtime.config.as_dict(), **changes}
        )
        await hass.async_block_till_done()

    await edit(task_interval=30 * 1440)
    assert entry.runtime_data is runtime and runtime.history == history
    assert runtime.next_due == local("2026-06-19 12:00")
    await edit(name="Reverse osmosis filter")
    assert runtime.next_due == local("2026-06-19 12:00")
    await move(hass, freezer, "2026-06-19 12:00")
    assert runtime.firing
    # Lengthening the interval while overdue quietly clears the reminder.
    await edit(task_interval=90 * 1440)
    assert not runtime.firing and runtime.next_due == local("2026-08-18 12:00")
    assert notifications[-1].data["message"] != "Closed"
    await edit(task_mode="fixed", task_rrule="FREQ=WEEKLY;BYDAY=TU", task_time="17:00")
    assert runtime.next_due == local("2026-06-23 17:00")
    assert runtime.last_completed == local("2026-05-20 08:00")


async def test_migration_only_adds_entity_source(hass, notifications):
    data = AlertConfig.from_dict(
        {"name": "Garage", "entity_id": "binary_sensor.garage", "notifiers": ["phone"]},
        hass,
    ).as_dict()
    old = {key: value for key, value in data.items() if not key.startswith("task_")}
    old.pop("source")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Garage", data=dict(old), options=dict(old)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED and entry.minor_version == 2
    assert entry.data == {**old, "source": "entity"}
    assert entry.options == {**old, "source": "entity"}
    runtime = entry.runtime_data
    assert not runtime.is_task and runtime.task_snapshot() is None
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    assert runtime.firing and len(notifications) == 1
    assert entity_id(hass, entry, "sensor", "next_due") is None


async def test_task_creation_and_options_flow(hass, notifications, freezer):
    freezer.move_to(local("2026-11-13 10:00"))
    manager = hass.config_entries.flow
    result = await manager.async_init(DOMAIN, context={"source": "user"})
    result = await manager.async_configure(
        result["flow_id"], {"name": "Garbage", "source": "schedule"}
    )
    assert result["step_id"] == "task"
    result = await manager.async_configure(
        result["flow_id"], {"task_mode": "fixed", "task_rrule": "every tuesday"}
    )
    assert result["errors"] == {"task_rrule": "invalid_rrule"}
    result = await manager.async_configure(
        result["flow_id"],
        {
            "task_mode": "fixed",
            "task_rrule": "FREQ=WEEKLY;BYDAY=TU",
            "task_time": "17:00:00",
            "task_window": {"hours": 5},
            "task_tags": ["tag-garbage"],
            "task_legacy_actions": ["dismisstrash"],
        },
    )
    assert result["step_id"] == "timing"
    shown = {key.schema for key in result["data_schema"].schema}
    assert "activation_delay" not in shown and "intervals" in shown
    result = await manager.async_configure(
        result["flow_id"], {"intervals": [{"minutes": 30}]}
    )
    result = await manager.async_configure(result["flow_id"], {"notifiers": ["phone"]})
    result = await manager.async_configure(result["flow_id"], {"message": "Bins out"})
    assert result["step_id"] == "review"
    assert "FREQ=WEEKLY;BYDAY=TU" in result["description_placeholders"]["summary"]
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.data["task_window"] == 300 and entry.data["source"] == "schedule"
    assert entry.runtime_data.next_due == local("2026-11-17 17:00")

    options = hass.config_entries.options
    result = await options.async_init(entry.entry_id)
    assert {key.schema for key in result["data_schema"].schema} == {"name"}
    result = await options.async_configure(result["flow_id"], {"name": "Garbage"})
    assert result["step_id"] == "task"
    result = await options.async_configure(
        result["flow_id"],
        {
            "task_mode": "fixed",
            "task_rrule": "FREQ=WEEKLY;BYDAY=WE",
            "task_time": "17:00:00",
        },
    )
    for data in ({"intervals": [{"minutes": 30}]}, {"notifiers": ["phone"]}, {}, {}):
        result = await options.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["task_window"] is None and entry.options["task_tags"] == []
    assert entry.runtime_data.next_due == local("2026-11-18 17:00")


async def test_import_task_carries_due_date_over(
    hass, notifications, freezer, hass_admin_user, hass_read_only_user
):
    freezer.move_to(local("2026-11-13 10:00"))
    seed = await setup_alert(hass)  # Loads the integration and its actions.
    payload = {
        "name": "RO filter",
        "interval": {"days": 180},
        "next_due": "2026-11-19 12:00:00",
        "last_completed": "2026-05-23 12:00:00",
        "tags": ["tag-ro"],
        "legacy_actions": ["rocomplete"],
        "notifiers": ["phone"],
        "message": "Change the RO filter",
    }
    with pytest.raises(Exception):  # noqa: B017 - Unauthorized
        await hass.services.async_call(
            DOMAIN,
            "import_task",
            payload,
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    await hass.services.async_call(DOMAIN, "import_task", payload, blocking=True)
    await hass.async_block_till_done()
    entry = next(
        item
        for item in hass.config_entries.async_entries(DOMAIN)
        if item.entry_id != seed.entry_id
    )
    runtime = entry.runtime_data
    assert entry.title == "RO filter" and runtime.is_task
    assert runtime.next_due == local("2026-11-19 12:00")
    assert runtime.last_completed == local("2026-05-23 12:00")
    assert runtime.config.task_tags == ("tag-ro",)
    await move(hass, freezer, "2026-11-19 12:00")
    assert notifications[-1].data["message"] == "Change the RO filter"
    hass.bus.async_fire("mobile_app_notification_action", {"action": "rocomplete"})
    await hass.async_block_till_done()
    assert runtime.next_due == local("2027-05-18 12:00")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "import_task", {"name": "Broken", "mode": "fixed"}, blocking=True
        )
