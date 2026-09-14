"""Flat AND/OR condition groups with explicit three-valued evaluation."""

from math import isfinite

import voluptuous as vol
from homeassistant.helpers import config_validation as cv


def validate_conditions(raw):
    if not isinstance(raw, list | tuple) or len(raw) > 20:
        raise ValueError("Expected at most 20 conditions")
    result = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Invalid condition")
        try:
            entity = cv.entity_id(row["entity_id"])
        except (KeyError, TypeError, vol.Invalid) as err:
            raise ValueError("Invalid entity") from err
        operator = row.get("operator", "state")
        if operator not in ("state", "not_state", "above", "below"):
            raise ValueError("Invalid operator")
        value = row.get("value")
        if operator in ("above", "below"):
            try:
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
                if not isfinite(value):
                    raise ValueError
            except (TypeError, ValueError) as err:
                raise ValueError("Invalid threshold") from err
        elif not isinstance(value, str) or not value:
            raise ValueError("Invalid state")
        result.append({"entity_id": entity, "operator": operator, "value": value})
    return tuple(result)


def evaluate(hass, conditions, mode):
    """Unknown inputs only suspend when the other inputs cannot decide the result."""
    results = []
    for row in conditions:
        source = hass.states.get(row["entity_id"])
        if source is None or source.state in ("unknown", "unavailable"):
            results.append(None)
            continue
        value, operator = row["value"], row["operator"]
        if operator in ("state", "not_state"):
            matched = source.state == value
            results.append(matched if operator == "state" else not matched)
        else:
            try:
                number = float(source.state)
                results.append(
                    (number > value if operator == "above" else number < value)
                    if isfinite(number)
                    else None
                )
            except TypeError, ValueError:
                results.append(None)
    if mode == "all":
        return False if False in results else None if None in results else True
    return True if True in results else None if None in results else False
