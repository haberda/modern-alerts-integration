"""Native acknowledgement, resume, and explicit test controls."""

from homeassistant.components.button import ButtonEntity
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
    async_add_entities(
        AlertButton(entry, key)
        for key in (
            "acknowledge",
            "unacknowledge",
            "snooze",
            "cancel_snooze",
            "test_notification",
        )
    )


class AlertButton(ModernAlertEntity, ButtonEntity):
    """Control an incident from the default device UI or any standard card."""

    def __init__(self, entry: ModernAlertsEntry, key: str) -> None:
        super().__init__(entry, key)
        self.key = key
        self._attr_icon = {
            "acknowledge": "mdi:bell-check",
            "unacknowledge": "mdi:bell-ring",
            "test_notification": "mdi:message-badge",
            "snooze": "mdi:bell-sleep",
            "cancel_snooze": "mdi:bell-cancel",
        }[key]

    @property
    def available(self) -> bool:
        if self.key == "snooze":
            return (
                self.runtime.config.enable_snooze
                and self.runtime.firing
                and not self.runtime.acknowledged
            )
        if self.key == "cancel_snooze":
            return self.runtime.snoozed_until is not None
        if self.key == "test_notification":
            return bool(
                self.runtime.config.notifiers or self.runtime.config.notify_entities
            )
        if self.key == "acknowledge":
            return (
                self.runtime.config.can_acknowledge
                and self.runtime.firing
                and not self.runtime.acknowledged
            )
        return self.runtime.firing and (
            self.runtime.acknowledged or self.runtime.snoozed_until is not None
        )

    async def async_press(self) -> None:
        if self.key == "test_notification":
            await self.runtime.async_test_notification(self._context)
        elif self.key == "snooze":
            self.runtime.snooze(context=self._context)
        elif self.key == "cancel_snooze":
            self.runtime.cancel_snooze()
        else:
            self.runtime.acknowledge(self.key == "acknowledge", self._context)
