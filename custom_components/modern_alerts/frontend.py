"""Built-in sidebar overview and authenticated read-only preview commands."""

from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.core import callback

from .const import DOMAIN
from .preview import explain
from .profiles import RUNTIMES

URL = "/modern_alerts_static/overview.js"


async def async_setup_frontend(hass):
    websocket_api.async_register_command(hass, overview)
    websocket_api.async_register_command(hass, preview)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                URL, str(Path(__file__).parent / "frontend" / "overview.js"), False
            )
        ]
    )


async def async_ensure_panel(hass):
    if DOMAIN not in hass.data.get("frontend_panels", {}):
        await async_register_panel(
            hass,
            frontend_url_path=DOMAIN,
            webcomponent_name="modern-alerts-overview",
            sidebar_title="Modern Alerts",
            sidebar_icon="mdi:bell-alert",
            module_url=URL + "?v=0.5.0",
        )


@callback
def async_remove_panel_if_unused(hass):
    if not hass.data.get(RUNTIMES):
        frontend.async_remove_panel(hass, DOMAIN)


def readable(connection, runtime):
    return bool(
        connection.user
        and (
            connection.user.is_admin
            or (
                runtime.status_entity_id
                and connection.user.permissions.check_entity(
                    runtime.status_entity_id, POLICY_READ
                )
            )
        )
    )


@websocket_api.websocket_command({vol.Required("type"): "modern_alerts/overview"})
@callback
def overview(hass, connection, msg):
    rows = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (entry.options or entry.data).get("kind") == "profile":
            continue
        runtime = hass.data.get(RUNTIMES, {}).get(entry.entry_id)
        if runtime and readable(connection, runtime):
            rows.append(
                {
                    "entry_id": entry.entry_id,
                    "loaded": True,
                    "can_control": bool(
                        runtime.status_entity_id
                        and connection.user.permissions.check_entity(
                            runtime.status_entity_id, POLICY_CONTROL
                        )
                    ),
                    **explain(runtime),
                }
            )
        elif runtime is None and connection.user.is_admin:
            rows.append(
                {
                    "entry_id": entry.entry_id,
                    "name": entry.title,
                    "loaded": False,
                    "state": entry.state.value,
                }
            )
    connection.send_result(msg["id"], rows)


@websocket_api.websocket_command(
    {vol.Required("type"): "modern_alerts/preview", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@callback
def preview(hass, connection, msg):
    runtime = hass.data.get(RUNTIMES, {}).get(msg["entry_id"])
    if runtime is None:
        connection.send_error(msg["id"], "not_found", "Alert is not loaded")
        return
    connection.send_result(msg["id"], explain(runtime, render=True))
