"""Modern Alerts integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_platform_entity_service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .models import AlertConfig, InvalidConfig
from .runtime import AlertRuntime

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
type ModernAlertsEntry = ConfigEntry[AlertRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions targeting only this integration's status entities."""
    for service, method in {
        "acknowledge": "async_turn_off",
        "unacknowledge": "async_turn_on",
        "turn_off": "async_turn_off",
        "turn_on": "async_turn_on",
        "toggle": "async_toggle",
        "test_notification": "async_test_notification",
        "snooze": "async_snooze",
    }.items():
        async_register_platform_entity_service(
            hass, DOMAIN, service, entity_domain="sensor", schema={}, func=method
        )
    async def mobile_action(event: Event) -> None:
        action = event.data.get("action")
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state.name != "LOADED":
                continue
            if action == "MODERN_ALERTS_ACK":
                entry.runtime_data.acknowledge(True, event.context)
            elif action == "MODERN_ALERTS_SNOOZE":
                try:
                    entry.runtime_data.snooze(30)
                except Exception:
                    continue
    hass.bus.async_listen("mobile_app_notification_action", mobile_action)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ModernAlertsEntry) -> bool:
    """Create all UI entities before listening for source changes."""
    try:
        config = AlertConfig.from_dict(entry.options or entry.data, hass)
    except InvalidConfig as err:
        raise ConfigEntryError(str(err)) from err
    entry.runtime_data = AlertRuntime(hass, config)
    store = Store(hass, 1, f"modern_alerts.{entry.entry_id}")
    saved = await store.async_load()
    if isinstance(saved, dict):
        entry.runtime_data.restore(saved)
    entry.runtime_data.store = store
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.runtime_data.start()
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    entry.async_on_unload(lambda: entry.runtime_data.async_save())
    return True


async def async_update_options(hass: HomeAssistant, entry: ModernAlertsEntry) -> None:
    """Apply options in place so acknowledgement is retained during ordinary edits."""
    config = AlertConfig.from_dict(entry.options or entry.data, hass)
    if config == entry.runtime_data.config:
        return
    await entry.runtime_data.async_update_config(config)
    if entry.title != config.name:
        hass.config_entries.async_update_entry(entry, title=config.name)
    registry = dr.async_get(hass)
    if device := registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}):
        registry.async_update_device(device.id, name=config.name)


async def async_unload_entry(hass: HomeAssistant, entry: ModernAlertsEntry) -> bool:
    """Unload without sending a misleading resolution notification."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        return True
    return False
