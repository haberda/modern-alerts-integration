"""Output effects, ownership, cleanup, and notification independence."""

import asyncio

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service
from test_runtime import advance as clock_advance
from test_runtime import set_state as source_state

from custom_components.modern_alerts.models import AlertConfig, InvalidConfig


async def settle(hass):
    # Background effect tasks deliberately do not join block_till_done: drain
    # ready continuations without waiting for their future duration deadlines.
    for _ in range(30):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def set_state(hass, state):
    await source_state(hass, state)
    await settle(hass)


async def advance(hass, freezer, minutes):
    await clock_advance(hass, freezer, minutes)
    await settle(hass)


def output(kind, **values):
    return {"id": kind, "name": kind.title(), "type": kind, "duration": 10, **values}


@pytest.fixture
def devices(hass):
    for entity, state, attrs in (
        (
            "light.hall",
            "on",
            {"brightness": 80, "color_mode": "rgb", "rgb_color": (1, 2, 3)},
        ),
        ("siren.hall", "off", {}),
        ("media_player.hall", "idle", {"volume_level": 0.2}),
    ):
        hass.states.async_set(entity, state, attrs)
    return {
        (domain, service): async_mock_service(hass, domain, service)
        for domain, services in (
            ("light", ("turn_on", "turn_off")),
            ("siren", ("turn_on", "turn_off")),
            ("media_player", ("volume_set", "play_media", "media_stop")),
            ("tts", ("speak",)),
        )
        for service in services
    }


@pytest.mark.parametrize(
    "bad",
    [
        output("light", entities=[]),
        output("light", entities=["switch.hall"]),
        output("siren", entities=["siren.hall"], duration=float("nan")),
        output("audio", entities=["media_player.hall"]),
        output("tts", entities=["media_player.hall"], tts_entity="notify.phone"),
        output("custom", actions=[]),
        output("custom", actions=[{"nonsense": True}]),
        output("light", entities=["light.hall"], interval=0.01),
        output("siren", entities=["siren.hall"], volume=2),
    ],
)
async def test_invalid_outputs(hass, bad):
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(
            {"name": "Bad", "entity_id": "sensor.test", "outputs": [bad]}, hass
        )


async def test_blink_and_restore_on_ack(hass, make_runtime, devices, freezer):
    runtime = make_runtime(
        outputs=[
            output("light", entities=["light.hall"], color=[255, 0, 0], brightness=100)
        ]
    )
    await set_state(hass, "on")
    assert devices["light", "turn_on"][-1].data["rgb_color"] == [255, 0, 0]
    await advance(hass, freezer, 1 / 60)
    assert len(devices["light", "turn_off"]) == 1
    runtime.acknowledge(True)
    await settle(hass)
    restored = devices["light", "turn_on"][-1].data
    assert restored["brightness"] == 80 and restored["rgb_color"] == (1, 2, 3)
    await advance(hass, freezer, 1)
    assert len(devices["light", "turn_on"]) == 2


async def test_siren_duration_and_snooze_cleanup(hass, make_runtime, devices, freezer):
    runtime = make_runtime(
        outputs=[output("siren", entities=["siren.hall"], volume=0.5, tone="alarm")]
    )
    await set_state(hass, "on")
    assert devices["siren", "turn_on"][0].data == {
        "entity_id": "siren.hall",
        "volume_level": 0.5,
        "tone": "alarm",
        "duration": 10,
    }
    runtime.snooze(5)
    await settle(hass)
    assert len(devices["siren", "turn_off"]) == 1
    runtime.cancel_snooze()
    await advance(hass, freezer, 30)
    assert len(devices["siren", "turn_on"]) == 2
    await advance(hass, freezer, 10 / 60)
    assert len(devices["siren", "turn_off"]) == 2


async def test_tts_recovery_and_volume_restore(hass, make_runtime, devices):
    runtime = make_runtime(
        outputs=[
            output(
                "tts",
                entities=["media_player.hall"],
                tts_entity="tts.local",
                message="{{ alert_name }} is open",
                recovery_message="{{ alert_name }} is closed",
                volume=0.6,
            )
        ]
    )
    await set_state(hass, "on")
    assert devices["tts", "speak"][-1].data["message"] == "Garage is open"
    await set_state(hass, "off")
    assert devices["tts", "speak"][-1].data["message"] == "Garage is closed"
    await runtime.async_stop_outputs()
    assert devices["media_player", "volume_set"][-1].data["volume_level"] == 0.2
    assert len(devices["media_player", "media_stop"]) == 2


async def test_audio_individual_test_does_not_start_incident(
    hass, make_runtime, devices, notifications, freezer
):
    runtime = make_runtime(
        outputs=[
            output(
                "audio",
                entities=["media_player.hall"],
                media_url="media-source://media_source/local/chime.mp3",
            ),
            output("siren", entities=["siren.hall"]),
        ]
    )
    await runtime.async_test_output("audio")
    await settle(hass)
    assert len(devices["media_player", "play_media"]) == 1
    assert not devices["siren", "turn_on"] and not notifications
    assert not runtime.firing and not runtime.attempted and not runtime.outputs.seen
    await advance(hass, freezer, 10 / 60)
    assert len(devices["media_player", "media_stop"]) == 1
    with pytest.raises(ServiceValidationError):
        await runtime.async_test_output("missing")


async def test_once_output_new_incident_rearms(hass, make_runtime, devices, freezer):
    runtime = make_runtime(
        repeat=[1], outputs=[output("siren", entities=["siren.hall"], repeat=False)]
    )
    await set_state(hass, "on")
    await advance(hass, freezer, 1)
    await advance(hass, freezer, 1)
    assert len(devices["siren", "turn_on"]) == 1
    assert runtime.snapshot()["outputs_seen"] == ["siren"]
    await set_state(hass, "off")
    await set_state(hass, "on")
    assert len(devices["siren", "turn_on"]) == 2


async def test_shared_device_waiter_cannot_stop_owner(hass, make_runtime, devices):
    first = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    second = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    await set_state(hass, "on")
    assert len(devices["siren", "turn_on"]) == 1
    second.acknowledge(True)
    await settle(hass)
    assert not devices["siren", "turn_off"]
    first.acknowledge(True)
    await settle(hass)
    assert len(devices["siren", "turn_off"]) == 1


async def test_manual_light_change_not_overwritten(
    hass, make_runtime, devices, freezer
):
    make_runtime(outputs=[output("light", entities=["light.hall"])])
    await set_state(hass, "on")
    hass.states.async_set("light.hall", "on", {"brightness": 123}, context=Context())
    await settle(hass)
    await advance(hass, freezer, 1)
    assert len(devices["light", "turn_on"]) == 1
    assert not devices["light", "turn_off"]
    assert hass.states.get("light.hall").attributes["brightness"] == 123


async def test_suspension_and_unload_cleanup(hass, make_runtime, devices):
    runtime = make_runtime(
        unavailable_policy="suspend", outputs=[output("siren", entities=["siren.hall"])]
    )
    await set_state(hass, "on")
    await set_state(hass, "unavailable")
    assert len(devices["siren", "turn_off"]) == 1
    await runtime.async_test_output("siren")
    await settle(hass)
    await runtime.async_stop()
    assert len(devices["siren", "turn_off"]) == 2
    assert not runtime.outputs._tasks


async def test_custom_actions_cancel_wait_and_run_cleanup(
    hass, make_runtime, notifications
):
    runtime = make_runtime(
        outputs=[
            output(
                "custom",
                actions=[
                    {
                        "action": "notify.phone",
                        "data": {"message": "{{ alert_name }} custom"},
                    },
                    {"delay": 120},
                    {"action": "notify.phone", "data": {"message": "Must not run"}},
                ],
                stop_actions=[
                    {"action": "notify.phone", "data": {"message": "Stopped"}}
                ],
            )
        ]
    )
    await set_state(hass, "on")
    assert {call.data["message"] for call in notifications} == {
        "Garage",
        "Garage custom",
    }
    runtime.acknowledge(True)
    await settle(hass)
    assert notifications[-1].data["message"] == "Stopped"
    assert not runtime.outputs.active


async def test_failing_output_does_not_block_other_targets_or_phone(
    hass, make_runtime, devices, notifications
):
    runtime = make_runtime(
        outputs=[output("siren", entities=["siren.missing", "siren.hall"])]
    )
    await set_state(hass, "on")
    assert len(notifications) == 1 and len(devices["siren", "turn_on"]) == 1
    assert runtime.outputs.errors == {"siren:siren.missing": "ValueError"}
    assert not devices["siren", "turn_off"]


async def test_slow_output_is_cancelled_without_blocking_phone(
    hass, make_runtime, notifications, devices
):
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def slow(call):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    hass.services.async_register("siren", "turn_on", slow)
    runtime = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    hass.states.async_set("binary_sensor.garage", "on")
    await entered.wait()
    assert len(notifications) == 1
    runtime.acknowledge(True)
    await finished.wait()
    await settle(hass)
    assert len(devices["siren", "turn_off"]) == 1


async def test_output_roundtrip_and_duplicate_ids(hass):
    config = AlertConfig.from_dict(
        {
            "name": "Test",
            "entity_id": "sensor.test",
            "outputs": [
                output(
                    "custom",
                    actions=[{"delay": 1}],
                    stop_actions=[{"event": "output_stopped"}],
                )
            ],
        },
        hass,
    )
    assert AlertConfig.from_dict(config.as_dict(), hass) == config
    assert config.as_dict()["outputs"][0]["actions"] == [{"delay": 1}]
    with pytest.raises(InvalidConfig):
        AlertConfig.from_dict(
            {**config.as_dict(), "outputs": list(config.outputs) * 2}, hass
        )


async def test_shared_device_handoff_after_owner_finishes(hass, make_runtime, devices):
    first = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    second = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    await set_state(hass, "on")
    first.acknowledge(True)
    await settle(hass)
    assert len(devices["siren", "turn_on"]) == 2
    assert len(devices["siren", "turn_off"]) == 1
    assert second.outputs.active == ["siren"]
    first.acknowledge(True)
    await settle(hass)
    assert len(devices["siren", "turn_off"]) == 1


async def test_provider_exception_cleans_up_and_retries(
    hass, make_runtime, devices, freezer
):
    async_mock_service(
        hass,
        "siren",
        "turn_on",
        raise_exception=RuntimeError("private provider detail"),
    )
    runtime = make_runtime(
        repeat=[1], outputs=[output("siren", entities=["siren.hall"])]
    )
    await set_state(hass, "on")
    assert runtime.outputs.errors == {"siren:siren.hall": "RuntimeError"}
    assert len(devices["siren", "turn_off"]) == 1
    good = async_mock_service(hass, "siren", "turn_on")
    await advance(hass, freezer, 1)
    assert len(good) == 1 and not runtime.outputs.errors


async def test_once_output_stays_once_after_reload(
    hass, devices, notifications, freezer
):
    from test_integration import setup_alert

    entry = await setup_alert(
        hass,
        restore_state=True,
        repeat=[1],
        outputs=[output("siren", entities=["siren.hall"], repeat=False)],
    )
    await set_state(hass, "on")
    assert len(devices["siren", "turn_on"]) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    await advance(hass, freezer, 1)
    assert len(devices["siren", "turn_on"]) == 1
    assert len(devices["siren", "turn_off"]) == 1


async def test_selected_output_buttons_and_live_options(hass, devices, notifications):
    from test_integration import entity_id, setup_alert

    entry = await setup_alert(
        hass,
        outputs=[
            output(
                "audio",
                entities=["media_player.hall"],
                media_url="https://example.com/chime.mp3",
            ),
            output("siren", entities=["siren.hall"]),
        ],
    )
    selection = entity_id(hass, entry, "select", "test_output")
    button = entity_id(hass, entry, "button", "test_output")
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": selection, "option": "Siren (siren)"},
        blocking=True,
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    await settle(hass)
    assert (
        len(devices["siren", "turn_on"]) == 1
        and not devices["media_player", "play_media"]
    )
    assert not entry.runtime_data.attempted
    await hass.services.async_call(
        "modern_alerts",
        "stop_outputs",
        {"entity_id": entity_id(hass, entry, "sensor", "status")},
        blocking=True,
    )
    assert len(devices["siren", "turn_off"]) == 1
    hass.config_entries.async_update_entry(entry, options={**entry.data, "outputs": []})
    await settle(hass)
    assert hass.states.get(selection).state == "unavailable"
    assert hass.states.get(button).state == "unavailable"
