"""Alert lifecycle status and action targets."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    async_add_entities([AlertStatus(entry)])


class AlertStatus(ModernAlertEntity, SensorEntity):
    """Expose idle/on/off without confusing acknowledgement with disablement."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "on", "off"]
    _attr_icon = "mdi:bell-alert"

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
            "watched_entity": runtime.config.entity_id,
            "matching_state": runtime.config.state,
            "can_acknowledge": runtime.config.can_acknowledge,
            "next_notification": runtime.next_notification,
            "last_attempt": runtime.last_attempt,
            "notification_errors": runtime.errors,
            "snoozed_until": runtime.snoozed_until,
            "source_suspended": runtime.source_suspended,
            "pending_transition": runtime.pending_active,
            "pending_deadline": runtime.pending_due,
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
