"""Configuration validation and round trips."""

import pytest

from custom_components.modern_alerts.models import AlertConfig, InvalidConfig


@pytest.mark.parametrize("repeat", [[], 0, -1, True, [False], "nan", "inf", [None], {}])
def test_invalid_intervals(hass, repeat):
    with pytest.raises(InvalidConfig, match="repeat"):
        AlertConfig.from_dict(
            {"name": "Name", "entity_id": "sensor.test", "repeat": repeat}, hass
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("state", True),
        ("entity_id", "invalid"),
        ("message", "{{ broken"),
        ("data", []),
        ("notifiers", ["notify"]),
        ("notifiers", ["send_message"]),
        ("notifiers", ["bad space"]),
        ("skip_first", "false"),
    ],
)
def test_invalid_fields(hass, field, value):
    with pytest.raises(InvalidConfig) as error:
        AlertConfig.from_dict(
            {"name": "Name", "entity_id": "sensor.test", field: value}, hass
        )
    assert error.value.field == field


def test_round_trip(hass):
    config = AlertConfig.from_dict(
        {
            "name": "Name",
            "entity_id": "sensor.test",
            "repeat": 0.016,
            "notifiers": ["notify.phone", "phone"],
        },
        hass,
    )
    assert config.notifiers == ("phone",)
    assert AlertConfig.from_dict(config.as_dict(), hass) == config


def test_notify_entity_rejects_extra_data(hass):
    with pytest.raises(InvalidConfig, match="entity_data_unsupported"):
        AlertConfig.from_dict(
            {
                "name": "Name",
                "entity_id": "sensor.test",
                "notify_entities": ["notify.phone"],
                "data": {"tag": "garage"},
            },
            hass,
        )
