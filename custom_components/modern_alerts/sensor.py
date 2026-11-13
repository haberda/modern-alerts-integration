"""Alert lifecycle status and action targets."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ModernAlertsEntry
from .entity import ModernAlertEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ModernAlertsEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[SensorEntity] = [AlertStatus(entry)]
    if entry.runtime_data.is_task:
        entities += [TaskNextDue(entry), TaskDaysRemaining(entry)]
    async_add_entities(entities)


class AlertStatus(ModernAlertEntity, SensorEntity):
    """Expose idle/on/off without confusing acknowledgement with disablement."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "on", "off"]
    _attr_icon = "mdi:bell-alert"
    # Diagnostic detail that changes on every event and can exceed the
    # recorder's per-state attribute limit; live state still carries it.
    _unrecorded_attributes = frozenset(
        {
            "history",
            "notification_errors",
            "output_errors",
            "policy_errors",
            "configured_outputs",
            "active_outputs",
        }
    )

    def __init__(self, entry: ModernAlertsEntry) -> None:
        super().__init__(entry, "status")

    @property
    def native_value(self) -> str:
        self.runtime.status_entity_id = self.entity_id
        return self.runtime.state

    @property
    def extra_state_attributes(self):
        runtime = self.runtime
        return {
            "source": runtime.config.source,
            "watched_entity": None if runtime.is_task else runtime.config.entity_id,
            "matching_state": runtime.config.state,
            "can_acknowledge": runtime.config.can_acknowledge,
            "next_notification": runtime.next_notification,
            "last_attempt": runtime.last_attempt,
            "notification_errors": runtime.errors,
            "snoozed_until": runtime.snoozed_until,
            "source_suspended": runtime.source_suspended,
            "pending_transition": runtime.pending_active,
            "pending_deadline": runtime.pending_due,
            "incident_started": runtime.started_at,
            "escalation_stage": runtime.config.stages[runtime.stage_index - 1]["name"]
            if runtime.stage_index
            else "Initial",
            "history": runtime.history,
            "policy_errors": runtime.policy_errors,
            "active_outputs": runtime.outputs.active,
            "output_errors": runtime.outputs.errors,
            "configured_outputs": {
                item["id"]: item["name"] for item in runtime.all_outputs
            },
        }

    async def async_turn_off(self) -> None:
        self.runtime.acknowledge(True, self._context)

    async def async_turn_on(self) -> None:
        self.runtime.acknowledge(False, self._context)

    async def async_toggle(self) -> None:
        self.runtime.acknowledge(not self.runtime.acknowledged, self._context)

    async def async_test_notification(self) -> None:
        await self.runtime.async_test_notification(self._context)

    async def async_snooze(self, minutes: float | None = None) -> None:
        self.runtime.snooze(minutes, self._context)

    async def async_cancel_snooze(self) -> None:
        self.runtime.cancel_snooze()

    async def async_test_output(self, output_id: str | None = None) -> None:
        await self.runtime.async_test_output(output_id)

    async def async_stop_outputs(self) -> None:
        await self.runtime.async_stop_outputs()

    async def async_clear_history(self) -> None:
        self.runtime.history.clear()
        self.runtime._publish()

    async def async_complete(self, completed_at: datetime | None = None) -> None:
        await self.runtime.async_complete(
            self._context, completed_at=completed_at, via="service"
        )


class TaskSensor(ModernAlertEntity, SensorEntity):
    """A read-only task sensor.

    Actions that target a whole device reach every sensor of the alert; only
    the status sensor acts on them, so one request is applied exactly once.
    """

    async def _not_a_target(self, **kwargs) -> None:
        return None

    async_turn_off = async_turn_on = async_toggle = _not_a_target
    async_test_notification = async_snooze = async_cancel_snooze = _not_a_target
    async_test_output = async_stop_outputs = async_clear_history = _not_a_target
    async_complete = _not_a_target


class TaskNextDue(TaskSensor):
    """When the task is next due; stays at the due time while overdue."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ModernAlertsEntry) -> None:
        super().__init__(entry, "next_due")

    @property
    def native_value(self) -> datetime | None:
        return self.runtime.next_due

    @property
    def extra_state_attributes(self):
        runtime = self.runtime
        config = runtime.config
        return {
            "last_completed": runtime.last_completed,
            "completed_by": runtime.completed_by,
            "mode": config.task_mode,
            "interval": config.task_interval,
            "rrule": config.task_rrule,
            "window_end": runtime.window_end,
            "days_remaining": runtime.days_remaining,
            "upcoming": runtime.upcoming,
            "due": runtime.firing,
            "missed": runtime.missed,
        }


class TaskDaysRemaining(TaskSensor):
    """Whole days until due, negative when overdue; suits gauge cards."""

    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_icon = "mdi:calendar-end"

    def __init__(self, entry: ModernAlertsEntry) -> None:
        super().__init__(entry, "days_remaining")

    @property
    def native_value(self) -> int | None:
        return self.runtime.days_remaining
