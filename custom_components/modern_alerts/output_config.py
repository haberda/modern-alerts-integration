"""Serializable output settings shared by setup forms and the runtime."""

from copy import deepcopy
from math import isfinite
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template

OUTPUT_TYPES = ("light", "siren", "tts", "audio", "custom")


class InvalidOutput(ValueError):
    """An output form field failed validation."""

    def __init__(self, field: str, code: str = "invalid_output") -> None:
        self.field = field
        self.code = code
        super().__init__(field)


def validate_output(raw: dict[str, Any], hass) -> dict[str, Any]:
    """Validate without storing HA Template objects in config entries."""
    if not isinstance(raw, dict) or raw.get("type") not in OUTPUT_TYPES:
        raise InvalidOutput("type")
    result = deepcopy(raw)
    kind = raw["type"]
    for key in ("id", "name"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise InvalidOutput(key)
    try:
        cv.slug(raw["id"])
    except vol.Invalid as err:
        raise InvalidOutput("id") from err
    for key, default in (("repeat", True), ("restore", True)):
        value = raw.get(key, default)
        if type(value) is not bool:
            raise InvalidOutput(key)
        result[key] = value
    for key, default, minimum, maximum in (
        ("duration", 10 if kind == "light" else 30, 0.1, 300),
        ("interval", 1, 0.5, 60),
        ("brightness", None, 1, 100),
        ("volume", None, 0, 1),
    ):
        value = raw.get(key, default)
        if value is not None:
            try:
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
                if not isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError
            except (ValueError, TypeError, OverflowError) as err:
                raise InvalidOutput(key) from err
        result[key] = value
    if kind != "custom":
        domain = {
            "light": "light",
            "siren": "siren",
            "tts": "media_player",
            "audio": "media_player",
        }[kind]
        entities = raw.get("entities")
        if not isinstance(entities, list) or not entities or len(entities) > 20:
            raise InvalidOutput("entities")
        try:
            for entity in entities:
                if cv.entity_id(entity).split(".")[0] != domain:
                    raise vol.Invalid("Wrong domain")
        except (vol.Invalid, TypeError, AttributeError) as err:
            raise InvalidOutput("entities") from err
        result["entities"] = list(dict.fromkeys(entities))
    if kind == "light":
        if raw.get("pattern", "blink") not in ("blink", "steady"):
            raise InvalidOutput("pattern")
        result["pattern"] = raw.get("pattern", "blink")
        if raw.get("color") is not None:
            try:
                color = raw["color"]
                if (
                    not isinstance(color, list | tuple)
                    or len(color) != 3
                    or any(type(n) is not int or not 0 <= n <= 255 for n in color)
                ):
                    raise ValueError
                result["color"] = list(color)
            except (vol.Invalid, TypeError, ValueError) as err:
                raise InvalidOutput("color") from err
    if kind == "tts":
        try:
            if cv.entity_id(raw.get("tts_entity", "")).split(".")[0] != "tts":
                raise vol.Invalid("Wrong domain")
        except vol.Invalid as err:
            raise InvalidOutput("tts_entity") from err
    if kind == "audio" and (
        not isinstance(raw.get("media_url"), str) or not raw["media_url"].strip()
    ):
        raise InvalidOutput("media_url")
    for key in ("message", "recovery_message"):
        value = raw.get(key)
        if value:
            try:
                if not isinstance(value, str):
                    raise ValueError
                Template(value, hass).ensure_valid()
            except Exception as err:
                raise InvalidOutput(key, "invalid_template") from err
    for key in ("language", "tone", "media_type"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise InvalidOutput(key)
    if kind == "custom":
        for key in ("actions", "stop_actions", "recovery_actions"):
            actions = raw.get(key, [])
            try:
                if not isinstance(actions, list) or (key == "actions" and not actions):
                    raise vol.Invalid("Expected actions")
                cv.SCRIPT_SCHEMA(deepcopy(actions))
            except (vol.Invalid, TypeError, ValueError) as err:
                raise InvalidOutput(key, "invalid_actions") from err
            result[key] = deepcopy(actions)
    return result


def validate_outputs(raw, hass) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list | tuple) or len(raw) > 20:
        raise InvalidOutput("outputs")
    outputs = tuple(validate_output(item, hass) for item in raw)
    if len({item["id"] for item in outputs}) != len(outputs):
        raise InvalidOutput("id")
    return outputs
