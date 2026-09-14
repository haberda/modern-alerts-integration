"""Validated, serializable alert configuration."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from math import isfinite
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .conditions import validate_conditions
from .const import MIN_REPEAT
from .output_config import InvalidOutput, validate_outputs
from .policies import InvalidPolicy, validate_policy, validate_stages


class InvalidConfig(ValueError):
    """Configuration error suitable for displaying beside a form field."""

    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


@dataclass(frozen=True, slots=True)
class AlertConfig:
    """An alert's configuration, independent of its current incident."""

    name: str
    entity_id: str
    state: str = "on"
    repeat: tuple[float, ...] = (30.0,)
    skip_first: bool = False
    can_acknowledge: bool = True
    resolution_after_ack: bool = True
    notifiers: tuple[str, ...] = ()
    notify_entities: tuple[str, ...] = ()
    message: str | None = None
    title: str | None = None
    done_message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    evaluate_on_start: bool = False
    restore_state: bool = False
    snooze_minutes: float = 30.0
    activation_delay: float = 0.0
    recovery_delay: float = 0.0
    unavailable_policy: str = "resolve"
    enable_snooze: bool = True
    action_buttons: bool = False
    numeric_below: float | None = None
    numeric_above: float | None = None
    numeric_recover_above: float | None = None
    numeric_recover_below: float | None = None
    numeric_unit: str | None = None
    outputs: tuple[dict[str, Any], ...] = ()
    kind: str = "alert"
    profile_ids: tuple[str, ...] = ()
    stages: tuple[dict[str, Any], ...] = ()
    delivery: dict[str, Any] = field(default_factory=dict)
    history_limit: int = 50
    conditions: tuple[dict[str, Any], ...] = ()
    condition_mode: str = "all"

    @classmethod
    def from_dict(cls, values: Mapping[str, Any], hass: HomeAssistant) -> AlertConfig:
        """Validate saved configuration as well as UI input."""
        for key in ("name", "entity_id", "state"):
            value = values.get(key, "on" if key == "state" else None)
            if not isinstance(value, str) or not value.strip():
                raise InvalidConfig(key, "required")
        try:
            entity_id = cv.entity_id(values["entity_id"])
        except vol.Invalid as err:
            raise InvalidConfig("entity_id", "invalid_entity") from err

        try:
            conditions = validate_conditions(values.get("conditions", []))
        except ValueError as err:
            raise InvalidConfig("conditions", "invalid_conditions") from err
        condition_mode = values.get("condition_mode", "all")
        if condition_mode not in ("all", "any"):
            raise InvalidConfig("condition_mode", "invalid_conditions")

        raw = values.get("repeat", [30])
        if not isinstance(raw, list | tuple):
            raw = [raw]
        try:
            if not raw or any(isinstance(n, bool) for n in raw):
                raise ValueError
            repeat = tuple(float(n) for n in raw)
            if any(not isfinite(n) or n < MIN_REPEAT for n in repeat):
                raise ValueError
            # Do not impose an arbitrary maximum, but prevent timer overflow.
            for minutes in repeat:
                dt_util.utcnow() + timedelta(minutes=minutes)
        except (ValueError, TypeError, OverflowError) as err:
            raise InvalidConfig("repeat", "invalid_repeat") from err

        templates = {}
        for key in ("message", "title", "done_message"):
            value = values.get(key)
            if value == "":
                value = None
            if value is not None:
                try:
                    if not isinstance(value, str):
                        raise ValueError
                    Template(value, hass).ensure_valid()
                except (TemplateError, ValueError) as err:
                    raise InvalidConfig(key, "invalid_template") from err
            templates[key] = value

        lists = {}
        for key in ("notifiers", "notify_entities"):
            raw_list = values.get(key, [])
            if not isinstance(raw_list, list | tuple):
                raise InvalidConfig(key, "invalid_destination")
            normalized = []
            for value in raw_list:
                if not isinstance(value, str):
                    raise InvalidConfig(key, "invalid_destination")
                value = value.removeprefix("notify.")
                try:
                    cv.slug(value)
                except vol.Invalid as err:
                    raise InvalidConfig(key, "invalid_destination") from err
                if key == "notifiers" and value in ("notify", "send_message"):
                    raise InvalidConfig(key, "invalid_destination")
                normalized.append(
                    f"notify.{value}" if key == "notify_entities" else value
                )
            lists[key] = tuple(dict.fromkeys(normalized))

        data = values.get("data", {})
        if not isinstance(data, dict):
            raise InvalidConfig("data", "invalid_data")
        # 2026.9.2 notify.send_message only accepts message and title.
        if data and lists["notify_entities"]:
            raise InvalidConfig("data", "entity_data_unsupported")
        flags = {}
        for key, default in (
            ("skip_first", False),
            ("can_acknowledge", True),
            ("resolution_after_ack", True),
            ("evaluate_on_start", False),
            ("restore_state", False),
        ):
            if not isinstance(value := values.get(key, default), bool):
                raise InvalidConfig(key, "invalid_boolean")
            flags[key] = value
        for key in ("activation_delay", "recovery_delay", "snooze_minutes"):
            try:
                raw_value = values.get(key, 30 if key == "snooze_minutes" else 0)
                value = float(raw_value)
                if isinstance(raw_value, bool) or not isfinite(value) or value < 0:
                    raise ValueError
                if key == "snooze_minutes" and not 0 < value <= 10080:
                    raise ValueError
                dt_util.utcnow() + timedelta(seconds=value)
            except TypeError, ValueError, OverflowError:
                raise InvalidConfig(key, "invalid_duration") from None
            flags[key] = value
        policy = values.get("unavailable_policy", "resolve")
        if policy not in ("resolve", "suspend"):
            raise InvalidConfig("unavailable_policy", "invalid_policy")
        flags["unavailable_policy"] = policy
        unit = values.get("numeric_unit")
        if unit == "":
            unit = None
        if unit is not None and (not isinstance(unit, str) or not unit.strip()):
            raise InvalidConfig("numeric_unit", "required")
        flags["numeric_unit"] = unit
        for key in ("enable_snooze", "action_buttons"):
            if not isinstance(
                value := values.get(key, True if key == "enable_snooze" else False),
                bool,
            ):
                raise InvalidConfig(key, "invalid_boolean")
            flags[key] = value
        for key in (
            "numeric_below",
            "numeric_above",
            "numeric_recover_above",
            "numeric_recover_below",
        ):
            value = values.get(key)
            if value is not None:
                try:
                    if isinstance(value, bool):
                        raise ValueError
                    value = float(value)
                    if not isfinite(value):
                        raise ValueError
                except TypeError, ValueError:
                    raise InvalidConfig(key, "invalid_number") from None
            flags[key] = value
        below, above = flags["numeric_below"], flags["numeric_above"]
        recover_above = flags["numeric_recover_above"]
        recover_below = flags["numeric_recover_below"]
        if below is not None and above is not None:
            raise InvalidConfig("numeric_above", "incompatible_thresholds")
        if recover_above is not None and (below is None or recover_above < below):
            raise InvalidConfig("numeric_recover_above", "incompatible_thresholds")
        if recover_below is not None and (above is None or recover_below > above):
            raise InvalidConfig("numeric_recover_below", "incompatible_thresholds")
        try:
            outputs = validate_outputs(values.get("outputs", []), hass)
        except InvalidOutput as err:
            raise InvalidConfig("outputs", err.code) from err
        try:
            delivery = validate_policy(values.get("delivery", {}))
            stages = validate_stages(values.get("stages", []))
        except InvalidPolicy as err:
            raise InvalidConfig(err.field, "invalid_policy") from err
        kind = values.get("kind", "alert")
        profiles = values.get("profile_ids", [])
        history_limit = values.get("history_limit", 50)
        if (
            kind not in ("alert", "profile")
            or not isinstance(profiles, list | tuple)
            or len(profiles) > 20
            or any(not isinstance(key, str) for key in profiles)
            or (kind == "profile" and profiles)
        ):
            raise InvalidConfig("profile_ids", "invalid_policy")
        if type(history_limit) is not int or not 0 <= history_limit <= 100:
            raise InvalidConfig("history_limit", "invalid_policy")
        if data and any(stage["notify_entities"] for stage in stages):
            raise InvalidConfig("data", "entity_data_unsupported")
        return cls(
            name=values["name"].strip(),
            entity_id=entity_id,
            state=values.get("state", "on"),
            repeat=repeat,
            data=deepcopy(data),
            outputs=outputs,
            kind=kind,
            profile_ids=tuple(dict.fromkeys(profiles)),
            stages=stages,
            delivery=delivery,
            history_limit=history_limit,
            conditions=conditions,
            condition_mode=condition_mode,
            **flags,
            **templates,
            **lists,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-compatible config entry data."""
        return {
            "name": self.name,
            "entity_id": self.entity_id,
            "state": self.state,
            "repeat": list(self.repeat),
            "skip_first": self.skip_first,
            "can_acknowledge": self.can_acknowledge,
            "resolution_after_ack": self.resolution_after_ack,
            "notifiers": list(self.notifiers),
            "notify_entities": list(self.notify_entities),
            "message": self.message,
            "title": self.title,
            "done_message": self.done_message,
            "data": deepcopy(self.data),
            "evaluate_on_start": self.evaluate_on_start,
            "restore_state": self.restore_state,
            "snooze_minutes": self.snooze_minutes,
            "activation_delay": self.activation_delay,
            "recovery_delay": self.recovery_delay,
            "unavailable_policy": self.unavailable_policy,
            "enable_snooze": self.enable_snooze,
            "action_buttons": self.action_buttons,
            "numeric_below": self.numeric_below,
            "numeric_above": self.numeric_above,
            "numeric_recover_above": self.numeric_recover_above,
            "numeric_recover_below": self.numeric_recover_below,
            "numeric_unit": self.numeric_unit,
            "outputs": deepcopy(list(self.outputs)),
            "kind": self.kind,
            "profile_ids": list(self.profile_ids),
            "stages": deepcopy(list(self.stages)),
            "delivery": deepcopy(self.delivery),
            "history_limit": self.history_limit,
            "conditions": deepcopy(list(self.conditions)),
            "condition_mode": self.condition_mode,
        }
