"""Modern Alerts integration."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_platform_entity_service
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .frontend import (
    async_ensure_panel,
    async_remove_panel_if_unused,
    async_setup_frontend,
)
from .grouping import groups
from .models import AlertConfig, InvalidConfig
from .profiles import PROFILES, RUNTIMES, refresh
from .runtime import AlertRuntime

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT]
type ModernAlertsEntry = ConfigEntry[AlertRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions targeting only this integration's status entities."""
    await async_setup_frontend(hass)
    for service, method in {
        "acknowledge": "async_turn_off",
        "unacknowledge": "async_turn_on",
        "turn_off": "async_turn_off",
        "turn_on": "async_turn_on",
        "toggle": "async_toggle",
        "test_notification": "async_test_notification",
        "snooze": "async_snooze",
        "cancel_snooze": "async_cancel_snooze",
        "test_output": "async_test_output",
        "stop_outputs": "async_stop_outputs",
        "clear_history": "async_clear_history",
    }.items():
        async_register_platform_entity_service(
            hass,
            DOMAIN,
            service,
            entity_domain="sensor",
            schema={
                vol.Optional("minutes"): vol.All(
                    vol.Coerce(float), vol.Range(min=0, min_included=False, max=10080)
                )
            }
            if service == "snooze"
            else {vol.Optional("output_id"): str}
            if service == "test_output"
            else {},
            func=method,
        )

    async def mobile_action(event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        parts = action.split(":")
        if len(parts) != 5 or parts[0] != "MODERN_ALERTS":
            return
        entry = hass.config_entries.async_get_entry(parts[1])
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            entry.runtime_data.handle_mobile_action(action, event.context)

    hass.bus.async_listen("mobile_app_notification_action", mobile_action)

    async def stop_groups(event):
        await groups(hass).async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_groups)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ModernAlertsEntry) -> bool:
    """Create all UI entities before listening for source changes."""
    try:
        config = AlertConfig.from_dict(entry.options or entry.data, hass)
    except InvalidConfig as err:
        raise ConfigEntryError(str(err)) from err
    entry.runtime_data = AlertRuntime(hass, config)
    entry.runtime_data.entry_id = entry.entry_id
    if config.kind == "profile":
        hass.data.setdefault(PROFILES, {})[entry.entry_id] = config
        entry.async_on_unload(entry.add_update_listener(async_update_options))
        await refresh(hass)
        return True
    hass.data.setdefault(RUNTIMES, {})[entry.entry_id] = entry.runtime_data
    await async_ensure_panel(hass)
    store = Store(
        hass, 1, f"modern_alerts.{entry.entry_id}", private=True, atomic_writes=True
    )
    saved = await store.async_load()
    if isinstance(saved, dict):
        entry.runtime_data.restore(saved)
    entry.runtime_data.store = store
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def start(hass: HomeAssistant) -> None:
        entry.runtime_data.start()

    entry.async_on_unload(async_at_started(hass, start))
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    async def shutdown(event: Event) -> None:
        await entry.runtime_data.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    )
    return True


async def async_update_options(hass: HomeAssistant, entry: ModernAlertsEntry) -> None:
    """Apply options in place so acknowledgement is retained during ordinary edits."""
    config = AlertConfig.from_dict(entry.options or entry.data, hass)
    if config == entry.runtime_data.config:
        return
    if config.kind == "profile":
        entry.runtime_data.config = config
        hass.data.setdefault(PROFILES, {})[entry.entry_id] = config
        await refresh(hass)
    else:
        await entry.runtime_data.async_update_config(config)
    title = f"Profile: {config.name}" if config.kind == "profile" else config.name
    if entry.title != title:
        hass.config_entries.async_update_entry(entry, title=title)
    registry = dr.async_get(hass)
    if device := registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}):
        registry.async_update_device(device.id, name=config.name)


async def async_unload_entry(hass: HomeAssistant, entry: ModernAlertsEntry) -> bool:
    """Unload without sending a misleading resolution notification."""
    if entry.runtime_data.config.kind == "profile":
        hass.data.get(PROFILES, {}).pop(entry.entry_id, None)
        await refresh(hass)
        return True
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data.get(RUNTIMES, {}).pop(entry.entry_id, None)
        await entry.runtime_data.async_stop()
        async_remove_panel_if_unused(hass)
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: ModernAlertsEntry) -> None:
    """Delete private incident data when the alert is deleted."""
    await Store(hass, 1, f"modern_alerts.{entry.entry_id}").async_remove()
