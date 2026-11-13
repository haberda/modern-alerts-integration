"""Modern Alerts integration."""

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import (
    async_register_admin_service,
    async_register_platform_entity_service,
)
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
from .schedule import local_text, parse_local
from .tasks import async_actor

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT]
type ModernAlertsEntry = ConfigEntry[AlertRuntime]

IMPORT_TASK_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("mode", default="after_completion"): vol.In(
            ("after_completion", "fixed", "once")
        ),
        vol.Optional("interval"): cv.positive_time_period,
        vol.Optional("rrule"): cv.string,
        vol.Optional("time"): cv.time,
        vol.Optional("window"): cv.positive_time_period,
        vol.Optional("next_due"): cv.datetime,
        vol.Optional("last_completed"): cv.datetime,
        vol.Optional("lead_days"): vol.Coerce(float),
        vol.Optional("tags", default=list): [cv.string],
        vol.Optional("legacy_actions", default=list): [cv.string],
        vol.Optional("notifiers", default=list): [cv.string],
        vol.Optional("notify_entities", default=list): [cv.string],
        vol.Optional("repeat"): vol.All(cv.ensure_list, [vol.Coerce(float)]),
        vol.Optional("message"): cv.string,
        vol.Optional("title"): cv.string,
        vol.Optional("done_message"): cv.string,
        vol.Optional("action_buttons", default=False): cv.boolean,
    }
)


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
        "complete": "async_complete",
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
            else {vol.Optional("completed_at"): cv.datetime}
            if service == "complete"
            else {},
            func=method,
        )

    def tasks() -> list[AlertRuntime]:
        return [
            runtime
            for runtime in tuple(hass.data.get(RUNTIMES, {}).values())
            if runtime.is_task
        ]

    async def mobile_action(event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        parts = action.split(":")
        if len(parts) != 5 or parts[0] != "MODERN_ALERTS":
            # Plain action IDs from notifications and shortcuts that predate
            # the task, kept working while a household migrates.
            for runtime in tasks():
                if action in runtime.config.task_legacy_actions:
                    await runtime.async_complete(event.context, via="legacy_action")
            return
        entry = hass.config_entries.async_get_entry(parts[1])
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            entry.runtime_data.handle_mobile_action(
                action, event.context, await async_actor(hass, event.context)
            )

    hass.bus.async_listen("mobile_app_notification_action", mobile_action)

    async def tag_scanned(event: Event) -> None:
        tag = event.data.get("tag_id")
        if not isinstance(tag, str):
            return
        for runtime in tasks():
            if tag in runtime.config.task_tags:
                await runtime.async_complete(event.context, via="tag")

    hass.bus.async_listen("tag_scanned", tag_scanned)

    async def import_task(call: ServiceCall) -> None:
        """Create a task entry, carrying over an existing due date exactly."""
        data = call.data
        values = {
            "name": data["name"],
            "source": "schedule",
            "task_mode": data["mode"],
            "task_tags": data["tags"],
            "task_legacy_actions": data["legacy_actions"],
            "notifiers": data["notifiers"],
            "notify_entities": data["notify_entities"],
            "action_buttons": data["action_buttons"],
        }
        for source, target in (
            ("interval", "task_interval"),
            ("window", "task_window"),
        ):
            if source in data:
                values[target] = data[source].total_seconds() / 60
        if "time" in data:
            values["task_time"] = data["time"].isoformat()
        if "next_due" in data:
            values["task_first_due"] = local_text(parse_local(str(data["next_due"])))
        for key in ("rrule", "lead_days"):
            if key in data:
                values[f"task_{key}"] = data[key]
        for key in ("repeat", "message", "title", "done_message"):
            if key in data:
                values[key] = data[key]
        seed = {}
        if "last_completed" in data:
            seed["last_completed"] = parse_local(
                str(data["last_completed"])
            ).isoformat()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={"config": values, "task_seed": seed},
        )
        if result["type"] is not FlowResultType.CREATE_ENTRY:
            raise ServiceValidationError(
                f"The task could not be created: {result.get('reason', 'invalid')}"
            )

    async_register_admin_service(
        hass, DOMAIN, "import_task", import_task, schema=IMPORT_TASK_SCHEMA
    )

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
    elif isinstance(seed := entry.data.get("task_seed"), dict):
        # First start of an imported task: adopt its previous completion.
        entry.runtime_data.seed_task(parse_local(seed.get("last_completed")))
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 -> 1.2: mark existing alerts and profiles as entity-driven."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        changes = {"minor_version": 2}
        for key in ("data", "options"):
            current = getattr(entry, key)
            if current and "source" not in current:
                changes[key] = {**current, "source": "entity"}
        hass.config_entries.async_update_entry(entry, **changes)
    return True
