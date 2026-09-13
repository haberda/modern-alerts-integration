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

from .const import MIN_REPEAT


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
    notifiers: tuple[str, ...] = ()
    notify_entities: tuple[str, ...] = ()
    message: str | None = None
    title: str | None = None
    done_message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    evaluate_on_start: bool = False

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
            ("evaluate_on_start", False),
        ):
            if not isinstance(value := values.get(key, default), bool):
                raise InvalidConfig(key, "invalid_boolean")
            flags[key] = value
        return cls(
            name=values["name"].strip(),
            entity_id=entity_id,
            state=values.get("state", "on"),
            repeat=repeat,
            data=deepcopy(data),
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
            "notifiers": list(self.notifiers),
            "notify_entities": list(self.notify_entities),
            "message": self.message,
            "title": self.title,
            "done_message": self.done_message,
            "data": deepcopy(self.data),
            "evaluate_on_start": self.evaluate_on_start,
        }
