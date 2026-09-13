"""Common identity and event subscription for alert entities."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import ModernAlertsEntry
from .const import DOMAIN


class ModernAlertEntity(Entity):
    """An entity belonging to a single alert config entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ModernAlertsEntry, key: str) -> None:
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self.runtime.config.name,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Modern Alerts",
            model="Alert",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
