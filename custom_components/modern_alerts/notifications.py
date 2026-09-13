"""Notification rendering and isolated provider dispatch."""

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from .const import NOTIFY_TIMEOUT
from .models import AlertConfig

_LOGGER = logging.getLogger(__name__)


async def async_notify(
    hass: HomeAssistant,
    config: AlertConfig,
    *,
    done: bool = False,
    context: Context | None = None,
    valid: Callable[[], bool] = lambda: True,
) -> dict[str, str]:
    """Send to each destination, returning redacted error categories.

    Action completion is not a delivery/read receipt. The caller's validity
    check prevents queued work for a resolved or replaced incident being sent.
    """
    if not valid():
        return {}
    source = config.done_message if done else config.message
    if done and source is None:
        return {}
    try:
        payload = {
            "message": (
                Template(source, hass).async_render(parse_result=False)
                if source is not None
                else config.name
            )
        }
        if config.title is not None:
            payload["title"] = Template(config.title, hass).async_render(
                parse_result=False
            )
    except TemplateError:
        _LOGGER.warning("An alert notification template could not be rendered")
        return {"template": "render_failed"}

    errors = {}
    destinations = [(name, None) for name in config.notifiers] + [
        ("send_message", entity_id) for entity_id in config.notify_entities
    ]
    for service, entity_id in destinations:
        if not valid():
            break
        data = deepcopy(payload)
        if entity_id:
            data["entity_id"] = entity_id
        elif config.data:
            data["data"] = deepcopy(config.data)
        try:
            async with asyncio.timeout(NOTIFY_TIMEOUT):
                await hass.services.async_call(
                    "notify", service, data, blocking=True, context=context
                )
        except Exception as err:  # Providers may raise non-HA exceptions too.
            key = entity_id or f"notify.{service}"
            errors[key] = type(err).__name__
            _LOGGER.warning("Alert destination %s failed (%s)", key, type(err).__name__)
    return errors
