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


async def test_compound_restore_and_rebind(hass, notifications):
    from test_integration import setup_alert

    entry = await setup_alert(
        hass,
        restore_state=True,
        unavailable_policy="suspend",
        conditions=[
            {"entity_id": "binary_sensor.a", "operator": "state", "value": "on"}
        ],
    )
    hass.states.async_set("binary_sensor.a", "on")
    await hass.async_block_till_done()
    entry.runtime_data.acknowledge(True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.firing and entry.runtime_data.acknowledged
    values = entry.runtime_data.config.as_dict()
    values["conditions"] = [
        {"entity_id": "binary_sensor.b", "operator": "state", "value": "on"}
    ]
    await entry.runtime_data.async_update_config(AlertConfig.from_dict(values, hass))
    assert not entry.runtime_data.firing
    hass.states.async_set("binary_sensor.a", "off")
    await hass.async_block_till_done()
    assert not entry.runtime_data.firing
    hass.states.async_set("binary_sensor.b", "on")
    await hass.async_block_till_done()
    assert entry.runtime_data.firing and not entry.runtime_data.acknowledged


async def test_weekly_dst_both_repeated_hours(hass):
    from homeassistant.util import dt as dt_util

    await hass.config.async_set_time_zone("America/New_York")
    policy = validate_policy(
        {"weekly_windows": [{"days": ["sun"], "start": "01:00", "end": "02:00"}]}
    )
    for stamp in ("2026-11-01T05:30:00+00:00", "2026-11-01T06:30:00+00:00"):
        assert allowed(hass, policy, dt_util.parse_datetime(stamp))
    assert not allowed(
        hass, policy, dt_util.parse_datetime("2026-11-01T07:00:00+00:00")
    )


@pytest.mark.parametrize(
    "window",
    [
        {"days": [], "start": "09:00", "end": "17:00"},
        {"days": ["funday"], "start": "09:00", "end": "17:00"},
        {"days": ["mon"], "start": "09:00", "end": "09:00"},
        {"days": ["mon"], "start": "25:00", "end": "17:00"},
    ],
)
async def test_invalid_weekly_windows(hass, window):
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(
            {
                "name": "Invalid",
                "entity_id": "sensor.a",
                "delivery": {"weekly_windows": [window]},
            },
            hass,
        )


async def test_output_weekly_window_closes_with_cleanup(
    hass, make_runtime, freezer, notifications
):
    from pytest_homeassistant_custom_component.common import async_mock_service
    from test_outputs import advance as output_advance
    from test_outputs import output, settle

    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-09-14 16:59:00+00:00")
    hass.states.async_set("siren.hall", "off")
    on = async_mock_service(hass, "siren", "turn_on")
    off = async_mock_service(hass, "siren", "turn_off")
    runtime = make_runtime(
        outputs=[
            output(
                "siren",
                entities=["siren.hall"],
                duration=300,
                delivery={
                    "weekly_windows": [
                        {"days": ["mon"], "start": "09:00", "end": "17:00"}
                    ]
                },
            )
        ]
    )
    hass.states.async_set("binary_sensor.garage", "on")
    await settle(hass)
    assert len(on) == 1 and len(notifications) == 1
    await output_advance(hass, freezer, 1)
    assert len(off) == 1 and not runtime.outputs.active
    assert runtime.firing
