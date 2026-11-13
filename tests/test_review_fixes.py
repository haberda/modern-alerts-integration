"""Regressions for defects found in the 0.5.0 review."""

# ruff: noqa: F811 - the imported `devices` fixture is requested by name.

import json

from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from test_integration import entity_id, setup_alert
from test_outputs import advance, devices, output, set_state, settle  # noqa: F401

from custom_components.modern_alerts.const import DOMAIN


async def test_volume_restored_after_playback_ends_by_itself(
    hass, make_runtime, devices, freezer
):
    runtime = make_runtime(
        outputs=[
            output(
                "tts",
                entities=["media_player.hall"],
                tts_entity="tts.local",
                volume=0.6,
                duration=30,
            )
        ]
    )
    await set_state(hass, "on")
    # The speaker reports its own playback lifecycle without our context,
    # including a slightly rounded volume and the generated media item.
    playing = {"volume_level": 0.601, "media_content_id": "tts-proxy/1.mp3"}
    hass.states.async_set("media_player.hall", "playing", playing)
    await settle(hass)
    await advance(hass, freezer, 8 / 60)
    hass.states.async_set("media_player.hall", "idle", {"volume_level": 0.601})
    await settle(hass)
    await advance(hass, freezer, 1)
    assert not runtime.outputs.active and not runtime.outputs.errors
    assert devices["media_player", "volume_set"][-1].data["volume_level"] == 0.2
    assert len(devices["media_player", "media_stop"]) == 1


async def test_other_media_or_volume_change_on_speaker_is_respected(
    hass, make_runtime, devices, freezer
):
    make_runtime(
        outputs=[
            output(
                "audio",
                entities=["media_player.hall"],
                media_url="https://example.invalid/chime.mp3",
                volume=0.6,
                duration=30,
            )
        ],
        repeat=[60],
    )
    await set_state(hass, "on")
    ours = {"volume_level": 0.6, "media_content_id": "chime"}
    hass.states.async_set("media_player.hall", "playing", ours)
    await settle(hass)
    # Somebody starts their own music on the same speaker.
    theirs = {"volume_level": 0.6, "media_content_id": "radio"}
    hass.states.async_set("media_player.hall", "playing", theirs)
    await settle(hass)
    await advance(hass, freezer, 1)
    assert not devices["media_player", "media_stop"]
    assert len(devices["media_player", "volume_set"]) == 1


async def test_late_light_report_is_not_a_manual_change(
    hass, make_runtime, devices, freezer
):
    make_runtime(
        outputs=[
            output(
                "light",
                entities=["light.hall"],
                pattern="steady",
                color=[255, 0, 0],
                brightness=100,
                duration=30,
            )
        ]
    )
    await set_state(hass, "on")
    # A Zigbee light confirms later, gamut-limited, with no service context.
    reported = {"brightness": 254, "color_mode": "xy", "rgb_color": (255, 30, 12)}
    hass.states.async_set("light.hall", "on", reported, context=Context())
    await settle(hass)
    await advance(hass, freezer, 1)
    restore = devices["light", "turn_on"][-1].data
    assert len(devices["light", "turn_on"]) == 2
    assert restore["brightness"] == 80 and restore["rgb_color"] == (1, 2, 3)


async def test_snooze_keeps_other_recipients_controls_working(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(action_buttons=True, repeat=[60], snooze_minutes=30)
    runtime.entry_id = "entry"
    await set_state(hass, "on")
    actions = {
        item["action"].rsplit(":", 1)[-1]: item["action"]
        for item in runtime.notification_actions()
    }
    runtime.handle_mobile_action(actions["SNOOZE"])
    until = runtime.snoozed_until
    await advance(hass, freezer, 5)
    runtime.handle_mobile_action(actions["SNOOZE"])  # A replay does not extend.
    assert runtime.snoozed_until == until
    runtime.handle_mobile_action(actions["ACK"])  # A second person's phone.
    assert runtime.acknowledged and runtime.snoozed_until is None
    runtime.handle_mobile_action(actions["SNOOZE"])
    assert runtime.snoozed_until is None


async def test_snooze_expiry_reminds_and_restarts_interval(
    hass, make_runtime, notifications, freezer
):
    runtime = make_runtime(repeat=[60], snooze_minutes=30)
    await set_state(hass, "on")
    runtime.snooze()
    await advance(hass, freezer, 29)
    assert len(notifications) == 1
    await advance(hass, freezer, 1)
    assert len(notifications) == 2 and runtime.snoozed_until is None
    await advance(hass, freezer, 59)
    assert len(notifications) == 2
    await advance(hass, freezer, 1)
    assert len(notifications) == 3
    # Cancelling a snooze still follows the existing schedule.
    runtime.snooze()
    runtime.cancel_snooze()
    await settle(hass)
    assert len(notifications) == 3


async def test_acknowledge_leaves_output_test_running(hass, make_runtime, devices):
    runtime = make_runtime(outputs=[output("siren", entities=["siren.hall"])])
    await runtime.async_test_output("siren")
    await settle(hass)
    assert runtime.outputs.active == ["siren"]
    runtime.acknowledge(True)
    await settle(hass)
    assert runtime.outputs.active == ["siren"] and not devices["siren", "turn_off"]
    # Nothing is recorded for acknowledging an alert with no incident.
    assert not [item for item in runtime.history if item["event"] == "acknowledged"]
    await runtime.async_stop_outputs()
    assert len(devices["siren", "turn_off"]) == 1


async def test_run_once_output_retries_after_busy_device(
    hass, make_runtime, devices, freezer
):
    busy = output("siren", entities=["siren.hall"], duration=120)
    once = output("siren", entities=["siren.hall"], repeat=False)
    owner = make_runtime(outputs=[busy], repeat=[60])
    waiter = make_runtime(outputs=[once], repeat=[1])
    await set_state(hass, "on")
    assert len(devices["siren", "turn_on"]) == 1
    await advance(hass, freezer, 15 / 60)  # The wait for the device times out.
    assert "siren:siren.hall" in waiter.outputs.errors
    assert "siren" not in waiter.outputs.seen
    owner.acknowledge(True)
    await settle(hass)
    await advance(hass, freezer, 1)
    assert len(devices["siren", "turn_on"]) == 2 and "siren" in waiter.outputs.seen


async def test_history_is_not_recorded_and_panel_uses_manifest_version(
    hass, notifications
):
    entry = await setup_alert(hass, history_limit=100)
    runtime = entry.runtime_data
    for _ in range(100):
        runtime._record("notification_result", errors={"notify.phone": "TimeoutError"})
    runtime._publish()
    await hass.async_block_till_done()
    status = entity_id(hass, entry, "sensor", "status")
    entity = hass.data["sensor"].get_entity(status)
    state = hass.states.get(status)
    unrecorded = entity._Entity__combined_unrecorded_attributes
    assert "history" in unrecorded and len(state.attributes["history"]) == 100
    recorded = {
        key: value for key, value in state.attributes.items() if key not in unrecorded
    }
    assert len(json.dumps(recorded, default=str)) < 2048
    assert er.async_get(hass).async_get(status).platform == DOMAIN
    integration = await async_get_integration(hass, DOMAIN)
    panel = hass.data["frontend_panels"][DOMAIN]
    assert panel.config["_panel_custom"]["module_url"].endswith(
        f"?v={integration.version}"
    )
