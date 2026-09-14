"""Reusable recipient/output profiles stored as native config entries."""

from copy import deepcopy
from dataclasses import replace

from .const import DOMAIN

PROFILES = f"{DOMAIN}_profiles"
RUNTIMES = f"{DOMAIN}_runtimes"


def resolve(hass, config):
    notifiers = list(config.notifiers)
    entities = list(config.notify_entities)
    outputs = deepcopy(list(config.outputs))
    missing = []
    for key in config.profile_ids:
        profile = hass.data.get(PROFILES, {}).get(key)
        if profile is None:
            missing.append(key)
            continue
        notifiers.extend(profile.notifiers)
        entities.extend(profile.notify_entities)
        for output in profile.outputs:
            outputs.append(
                {**deepcopy(output), "id": f"profile_{key.lower()}_{output['id']}"}
            )
    return replace(
        config,
        notifiers=tuple(dict.fromkeys(notifiers)),
        notify_entities=tuple(dict.fromkeys(entities)),
        outputs=tuple(outputs),
    ), missing


async def refresh(hass):
    for runtime in tuple(hass.data.get(RUNTIMES, {}).values()):
        await runtime.async_profiles_changed()
