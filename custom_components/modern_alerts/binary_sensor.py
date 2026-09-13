"""Problem state remains active even when reminders are acknowledged."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    async_add_entities([AlertProblem(entry)])


class AlertProblem(ModernAlertEntity, BinarySensorEntity):
    """Whether the alert has an unresolved incident."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ModernAlertsEntry) -> None:
        super().__init__(entry, "problem")

    @property
    def is_on(self) -> bool:
        return self.runtime.firing
