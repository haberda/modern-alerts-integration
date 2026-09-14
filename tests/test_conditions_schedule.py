"""Compound sources and weekly routing boundaries."""

from datetime import UTC, datetime

import pytest
from test_runtime import advance

from custom_components.modern_alerts.models import AlertConfig, InvalidConfig
from custom_components.modern_alerts.policies import allowed, reasons, validate_policy


async def test_compound_tracks_all_sources_and_debounces(
    hass, make_runtime, freezer, notifications
):
    runtime = make_runtime(
        conditions=[
            {"entity_id": "binary_sensor.door", "operator": "state", "value": "on"},
            {"entity_id": "sensor.temperature", "operator": "above", "value": "25"},
        ],
        activation_delay=60,
        unavailable_policy="suspend",
    )
    hass.states.async_set("binary_sensor.door", "on")
    hass.states.async_set("sensor.temperature", "26")
    await hass.async_block_till_done()
    assert runtime.pending_active is True
    await advance(hass, freezer, 1)
    assert runtime.firing and len(notifications) == 1
    hass.states.async_set("sensor.temperature", "unknown")
    await hass.async_block_till_done()
    assert runtime.source_suspended
    # A known false AND member is sufficient to resolve despite another unknown.
    hass.states.async_set("binary_sensor.door", "off")
    await hass.async_block_till_done()
    assert not runtime.firing and not runtime.source_suspended


async def test_any_and_removed_sources(hass, make_runtime):
    runtime = make_runtime(
        condition_mode="any",
        unavailable_policy="suspend",
        conditions=[
            {"entity_id": "binary_sensor.a", "operator": "state", "value": "on"},
            {"entity_id": "binary_sensor.b", "operator": "not_state", "value": "off"},
        ],
    )
    hass.states.async_set("binary_sensor.a", "on")
    await hass.async_block_till_done()
    assert runtime.firing and not runtime.source_suspended
    hass.states.async_remove("binary_sensor.a")
    await hass.async_block_till_done()
    assert runtime.firing and runtime.source_suspended


@pytest.mark.parametrize(
    "conditions",
    [[{}], [{"entity_id": "sensor.a", "operator": "above", "value": "nan"}], "bad"],
)
async def test_invalid_conditions(hass, conditions):
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(
            {"name": "Test", "entity_id": "sensor.a", "conditions": conditions}, hass
        )


async def test_weekly_overnight_and_daily_quiet_intersection(hass):
    await hass.config.async_set_time_zone("UTC")
    policy = validate_policy(
        {"weekly_windows": [{"days": ["mon"], "start": "22:00", "end": "02:00"}]}
    )
    assert allowed(hass, policy, datetime(2026, 9, 14, 22, tzinfo=UTC))
    assert allowed(hass, policy, datetime(2026, 9, 15, 1, tzinfo=UTC))
    assert not allowed(hass, policy, datetime(2026, 9, 15, 2, tzinfo=UTC))
    assert not allowed(hass, policy, datetime(2026, 9, 15, 22, tzinfo=UTC))
    policy.update(quiet_start="23:00:00", quiet_end="00:30:00")
    assert reasons(hass, policy, datetime(2026, 9, 14, 23, tzinfo=UTC)) == [
        "quiet_hours"
    ]


async def test_weekly_held_delivery_catches_up(
    hass, make_runtime, freezer, notifications
):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-09-14 08:59:00+00:00")
    runtime = make_runtime(
        delivery={
            "weekly_windows": [{"days": ["mon"], "start": "09:00", "end": "17:00"}]
        }
    )
    hass.states.async_set("binary_sensor.garage", "on")
    await hass.async_block_till_done()
    assert runtime.firing and not notifications
    await advance(hass, freezer, 1)
    assert len(notifications) == 1
