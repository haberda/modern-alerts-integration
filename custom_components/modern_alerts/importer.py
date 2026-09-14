"""Import pasted legacy Alert YAML without file access or template rendering."""

import json
import re
from copy import deepcopy

import voluptuous as vol
import yaml
from homeassistant.components.alert import ALERT_SCHEMA
from homeassistant.helpers.template import Template

from .models import AlertConfig


class AlertLoader(yaml.SafeLoader):
    """Keep on/off state strings while retaining explicit true/false booleans."""

    yaml_implicit_resolvers = {
        key: [
            (tag, pattern)
            for tag, pattern in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
        for key, resolvers in deepcopy(yaml.SafeLoader.yaml_implicit_resolvers).items()
    }


AlertLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def parse_alerts(text, hass):
    """Accept a single definition or a named mapping, with optional alert wrapper."""
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError("Paste up to 64 KiB of YAML")
    try:
        # Reject aliases rather than accepting cyclic or exponentially expanded data.
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
            raise ValueError("YAML aliases are not supported")
        raw = yaml.load(text, Loader=AlertLoader)
    except (yaml.YAMLError, RecursionError) as err:
        raise ValueError(
            "Invalid YAML; includes and secrets must be expanded before pasting"
        ) from err
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as err:
        raise ValueError("Use JSON-compatible values in alert definitions") from err
    if not isinstance(raw, dict):
        raise ValueError("Expected an alert mapping")
    if "alert" in raw:
        if set(raw) != {"alert"}:
            raise ValueError("Paste only the alert section")
        raw = raw["alert"]
    if isinstance(raw, dict) and "entity_id" in raw:
        raw = {"imported": raw}
    if not isinstance(raw, dict) or not raw or len(raw) > 100:
        raise ValueError("Expected 1–100 named alerts")
    result = {}
    for key, values in raw.items():
        if not isinstance(key, str) or not isinstance(values, dict):
            raise ValueError("Invalid named alert")
        try:
            validated = ALERT_SCHEMA(values)
        except vol.Invalid as err:
            raise ValueError(f"Invalid legacy alert: {key}") from err
        normalized = {
            field: value.template if isinstance(value, Template) else value
            for field, value in validated.items()
        }
        result[key] = AlertConfig.from_dict(normalized, hass).as_dict()
    return result
