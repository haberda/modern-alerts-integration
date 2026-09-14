"""Validated delivery policies evaluated in Home Assistant's local timezone."""

from copy import deepcopy
from math import isfinite

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util


class InvalidPolicy(ValueError):
    def __init__(self, field):
        self.field = field
        super().__init__(field)


def finite(value, field, minimum=0, maximum=10080):
    try:
        if isinstance(value, bool):
            raise ValueError
        value = float(value)
        if not isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as err:
        raise InvalidPolicy(field) from err
    return value


def validate_policy(raw):
    if not isinstance(raw, dict):
        raise InvalidPolicy("delivery")
    result = deepcopy(raw)
    for key in ("quiet_start", "quiet_end"):
        value = raw.get(key)
        if value == "":
            value = None
        if value is not None and (
            not isinstance(value, str) or dt_util.parse_time(value) is None
        ):
            raise InvalidPolicy(key)
        result[key] = (
            dt_util.parse_time(value).isoformat() if value is not None else None
        )
    if bool(result["quiet_start"]) != bool(result["quiet_end"]) or (
        result["quiet_start"] and result["quiet_start"] == result["quiet_end"]
    ):
        raise InvalidPolicy("quiet_end")
    entities = raw.get("presence_entities", [])
    if not isinstance(entities, list) or len(entities) > 20:
        raise InvalidPolicy("presence_entities")
    try:
        for entity in entities:
            cv.entity_id(entity)
    except (vol.Invalid, TypeError) as err:
        raise InvalidPolicy("presence_entities") from err
    result["presence_entities"] = list(dict.fromkeys(entities))
    mode = raw.get("presence_mode", "any_home")
    if mode not in ("any_home", "all_away"):
        raise InvalidPolicy("presence_mode")
    result["presence_mode"] = mode
    group = raw.get("group", "")
    if not isinstance(group, str) or len(group) > 80:
        raise InvalidPolicy("group")
    result["group"] = group.strip()
    result["group_window"] = finite(raw.get("group_window", 30), "group_window", 1, 300)
    result["rate_limit"] = finite(raw.get("rate_limit", 0), "rate_limit")
    return result


def allowed(hass, policy, now=None):
    start, end = policy.get("quiet_start"), policy.get("quiet_end")
    if start and end:
        local = dt_util.as_local(now or dt_util.utcnow()).time().replace(tzinfo=None)
        start, end = dt_util.parse_time(start), dt_util.parse_time(end)
        quiet = start <= local < end if start < end else local >= start or local < end
        if quiet:
            return False
    entities = policy.get("presence_entities", [])
    if not entities:
        return True
    states = [hass.states.get(entity) for entity in entities]
    home = [state is not None and state.state in ("home", "on") for state in states]
    if policy.get("presence_mode", "any_home") == "any_home":
        return any(home)
    # Uncertain presence is not proof that everybody is away.
    return all(
        state is not None
        and state.state not in ("unknown", "unavailable", "home", "on")
        for state in states
    )


def has_policy(policy):
    return bool(policy.get("quiet_start") or policy.get("presence_entities"))


def validate_stages(raw):
    if not isinstance(raw, list | tuple) or len(raw) > 10:
        raise InvalidPolicy("stages")
    stages = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
        ):
            raise InvalidPolicy("stages")
        stage = deepcopy(item)
        stage["after"] = finite(item.get("after"), "after", 0.016)
        interval = item.get("interval")
        stage["interval"] = (
            finite(interval, "interval", 0.016) if interval is not None else None
        )
        for key in ("notifiers", "notify_entities", "output_ids"):
            values = item.get(key, [])
            if not isinstance(values, list) or any(
                not isinstance(v, str) for v in values
            ):
                raise InvalidPolicy(key)
            try:
                if key == "notifiers":
                    values = [cv.slug(v.removeprefix("notify.")) for v in values]
                    if set(values) & {"notify", "send_message"}:
                        raise vol.Invalid("Use specific destinations")
                elif key == "notify_entities":
                    for value in values:
                        if cv.entity_id(value).split(".")[0] != "notify":
                            raise vol.Invalid("Wrong domain")
            except vol.Invalid as err:
                raise InvalidPolicy(key) from err
            stage[key] = list(dict.fromkeys(values))
        stages.append(stage)
    stages.sort(key=lambda item: item["after"])
    if len({item["after"] for item in stages}) != len(stages):
        raise InvalidPolicy("after")
    return tuple(stages)
