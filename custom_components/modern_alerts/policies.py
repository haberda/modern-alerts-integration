"""Validated delivery policies evaluated in Home Assistant's local timezone."""

from copy import deepcopy
from math import isfinite

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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
    windows = raw.get("weekly_windows", [])
    if not isinstance(windows, list) or len(windows) > 28:
        raise InvalidPolicy("weekly_windows")
    result["weekly_windows"] = []
    for window in windows:
        if not isinstance(window, dict):
            raise InvalidPolicy("weekly_windows")
        days = window.get("days", [])
        start, end = window.get("start"), window.get("end")
        if (
            not isinstance(days, list)
            or not days
            or any(day not in WEEKDAYS for day in days)
            or not isinstance(start, str)
            or not isinstance(end, str)
            or dt_util.parse_time(start) is None
            or dt_util.parse_time(end) is None
            or dt_util.parse_time(start) == dt_util.parse_time(end)
        ):
            raise InvalidPolicy("weekly_windows")
        result["weekly_windows"].append(
            {
                "days": list(dict.fromkeys(days)),
                "start": dt_util.parse_time(start).isoformat(),
                "end": dt_util.parse_time(end).isoformat(),
            }
        )
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


def reasons(hass, policy, now=None):
    """Return current policy blockers without changing scheduling or delivery."""
    result = []
    local_now = dt_util.as_local(now or dt_util.utcnow())
    local = local_now.time().replace(tzinfo=None)
    windows = policy.get("weekly_windows", [])
    if windows:
        inside = False
        today = WEEKDAYS[local_now.weekday()]
        yesterday = WEEKDAYS[(local_now.weekday() - 1) % 7]
        for window in windows:
            start, end = (
                dt_util.parse_time(window["start"]),
                dt_util.parse_time(window["end"]),
            )
            if start < end:
                inside |= today in window["days"] and start <= local < end
            else:
                inside |= (today in window["days"] and local >= start) or (
                    yesterday in window["days"] and local < end
                )
        if not inside:
            result.append("outside_weekly_schedule")
    start, end = policy.get("quiet_start"), policy.get("quiet_end")
    if start and end:
        start, end = dt_util.parse_time(start), dt_util.parse_time(end)
        quiet = start <= local < end if start < end else local >= start or local < end
        if quiet:
            result.append("quiet_hours")
    entities = policy.get("presence_entities", [])
    if entities:
        states = [hass.states.get(entity) for entity in entities]
        home = [state is not None and state.state in ("home", "on") for state in states]
        present = (
            any(home)
            if policy.get("presence_mode", "any_home") == "any_home"
            else all(
                state is not None
                and state.state not in ("unknown", "unavailable", "home", "on")
                for state in states
            )
        )
        if not present:
            result.append("presence")
    return result


def allowed(hass, policy, now=None):
    return not reasons(hass, policy, now)


def has_policy(policy):
    return bool(
        policy.get("quiet_start")
        or policy.get("presence_entities")
        or policy.get("weekly_windows")
    )


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
