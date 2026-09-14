"""Owned, cancellable device effects and embedded Home Assistant actions."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NOTIFY_TIMEOUT


async def _wait(hass: HomeAssistant, seconds: float) -> None:
    """A cancellable wall-clock deadline, also exercised by controlled-time tests."""
    future = hass.loop.create_future()

    @callback
    def wake(now):
        if not future.done():
            future.set_result(None)

    cancel = async_track_point_in_utc_time(
        hass, wake, dt_util.utcnow() + timedelta(seconds=seconds)
    )
    try:
        await future
    finally:
        cancel()


class DeviceSession:
    """Exclusive device use, retaining external changes instead of undoing them."""

    def __init__(self, hass, entity_id, context):
        self.hass = hass
        self.entity_id = entity_id
        self.context = context
        self.before = hass.states.get(entity_id)
        self.external_change = False
        self.used = False
        self.playback_started = False
        self.last = self.signature(self.before)
        self.unsubscribe = async_track_state_change_event(
            hass, [entity_id], self.changed
        )

    @staticmethod
    def signature(state):
        if state is None:
            return None
        return (
            state.state,
            {
                key: state.attributes.get(key)
                for key in (
                    "brightness",
                    "color_mode",
                    "rgb_color",
                    "rgbw_color",
                    "rgbww_color",
                    "hs_color",
                    "xy_color",
                    "color_temp_kelvin",
                    "effect",
                    "volume_level",
                    "media_content_id",
                )
            },
        )

    @callback
    def changed(self, event):
        signature = self.signature(event.data["new_state"])
        own = (
            event.context.id == self.context.id
            or event.context.parent_id == self.context.id
        )
        if not own and signature != self.last:
            self.external_change = True
        self.last = signature

    async def call(self, domain, service, **data):
        if self.external_change:
            return
        self.used = True
        async with asyncio.timeout(NOTIFY_TIMEOUT):
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": self.entity_id, **data},
                blocking=True,
                context=self.context,
            )

    async def restore_light(self):
        if self.before is None or self.before.state not in ("on", "off"):
            return
        if self.before.state == "off":
            await self.call("light", "turn_off")
            return
        attrs = self.before.attributes
        data = {
            key: attrs[key]
            for key in ("brightness", "effect")
            if attrs.get(key) is not None
        }
        color_key = {
            "color_temp": "color_temp_kelvin",
            "hs": "hs_color",
            "xy": "xy_color",
            "rgb": "rgb_color",
            "rgbw": "rgbw_color",
            "rgbww": "rgbww_color",
        }.get(attrs.get("color_mode"))
        if color_key and attrs.get(color_key) is not None:
            data[color_key] = attrs[color_key]
        await self.call("light", "turn_on", **data)


class OutputManager:
    """One alert's effects; shared locks serialize built-in outputs per device.

    Effects have bounded durations. Waiting for another alert is bounded too;
    a busy device is reported and can be retried at the next reminder.
    """

    def __init__(self, hass: HomeAssistant, publish: Callable[[], None]):
        self.hass = hass
        self.publish = publish
        self.errors: dict[str, str] = {}
        self.seen: set[str] = set()
        self._runs: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._serial: dict[str, asyncio.Lock] = {}
        self._devices: dict[str, asyncio.Lock] = hass.data.setdefault(
            f"{DOMAIN}_output_locks", {}
        )

    @property
    def active(self) -> list[str]:
        return [
            key
            for key, task in self._runs.items()
            if not task.done() and not task.cancelling()
        ]

    @callback
    def stop(self) -> None:
        for task in tuple(self._tasks):
            if not task.done() and not task.cancelling():
                task.cancel()

    async def async_stop(self) -> None:
        self.stop()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    @callback
    def dispatch(self, outputs, variables, valid, *, recovery=False, test_id=None):
        """Start independent effects without blocking phone dispatch or timers."""
        for output in outputs:
            key = output["id"]
            if test_id is not None and key != test_id:
                continue
            if recovery and not (
                output.get("recovery_message") or output.get("recovery_actions")
            ):
                continue
            if (
                not recovery
                and test_id is None
                and not output["repeat"]
                and key in self.seen
            ):
                continue
            if (old := self._runs.get(key)) and not old.done() and not old.cancelling():
                continue
            if not valid():
                return
            if test_id is None and not recovery:
                self.seen.add(key)
            self.errors.pop(key, None)
            for error_key in tuple(self.errors):
                if error_key.startswith(f"{key}:"):
                    self.errors.pop(error_key)
            task = self.hass.async_create_background_task(
                self._run(output, variables, valid, recovery=recovery),
                f"Modern Alerts output {key}",
                eager_start=False,
            )
            self._runs[key] = task
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(lambda _: self.publish())
        self.publish()

    async def _run(self, output, variables, valid, *, recovery):
        key = output["id"]
        try:
            async with self._serial.setdefault(key, asyncio.Lock()):
                if not valid():
                    return
                variables = {
                    **variables,
                    "message": Template(variables["message"], self.hass).async_render(
                        variables, parse_result=False
                    ),
                }
                if output["type"] == "custom":
                    await self._custom(output, variables, recovery)
                else:
                    # A failed or occupied device does not block other targets.
                    await asyncio.gather(
                        *(
                            self._device(output, entity, variables, valid, recovery)
                            for entity in output["entities"]
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.errors[key] = type(err).__name__
        finally:
            self.publish()

    async def _script(self, actions, variables, duration):
        if not actions:
            return
        validated = await async_validate_actions_config(
            self.hass, cv.SCRIPT_SCHEMA(deepcopy(actions))
        )
        script = Script(
            self.hass, validated, "Modern Alerts output", DOMAIN, log_exceptions=False
        )
        try:
            async with asyncio.timeout(duration):
                await script.async_run(variables, context=Context())
        finally:
            await script.async_unload()

    async def _custom(self, output, variables, recovery):
        # Stop actions are explicit: arbitrary actions have no general inverse.
        if recovery:
            await self._script(
                output.get("recovery_actions"), variables, output["duration"]
            )
            return
        try:
            end = dt_util.utcnow() + timedelta(seconds=output["duration"])
            await self._script(output["actions"], variables, output["duration"])
            # Retain ownership until duration or cancellation so stop actions
            # still run after a short sequence that leaves a device enabled.
            remaining = (end - dt_util.utcnow()).total_seconds()
            if remaining > 0:
                await _wait(self.hass, remaining)
        finally:
            await self._script(output.get("stop_actions"), variables, NOTIFY_TIMEOUT)

    async def _device(self, output, entity, variables, valid, recovery):
        session = None
        acquired = False
        lock = self._devices.setdefault(entity, asyncio.Lock())
        try:
            async with asyncio.timeout(NOTIFY_TIMEOUT):
                await lock.acquire()
            acquired = True
            if not valid():
                return
            session = DeviceSession(self.hass, entity, Context())
            if session.before is None or session.before.state in (
                "unknown",
                "unavailable",
            ):
                raise ValueError("Output unavailable")
            await self._effect(output, session, variables, recovery)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.errors[f"{output['id']}:{entity}"] = type(err).__name__
        finally:
            if session:
                try:
                    if session.used:
                        await self._cleanup(output, session)
                except Exception as err:
                    self.errors[f"{output['id']}:{entity}:cleanup"] = type(err).__name__
                finally:
                    session.unsubscribe()
            if acquired:
                lock.release()
            self.publish()

    async def _effect(self, output, session, variables, recovery):
        kind = output["type"]
        duration = output["duration"]
        if kind == "light":
            data = {}
            if output.get("brightness") is not None:
                data["brightness_pct"] = output["brightness"]
            if output.get("color") is not None:
                data["rgb_color"] = output["color"]
            end = dt_util.utcnow() + timedelta(seconds=duration)
            on = True
            while not session.external_change and dt_util.utcnow() < end:
                await session.call(
                    "light", "turn_on" if on else "turn_off", **(data if on else {})
                )
                remaining = (end - dt_util.utcnow()).total_seconds()
                await _wait(
                    self.hass,
                    max(0, min(output["interval"], remaining))
                    if output["pattern"] == "blink"
                    else max(0, remaining),
                )
                on = not on
            return
        if kind == "siren":
            data = {"duration": max(1, round(duration))}
            if output.get("volume") is not None:
                data["volume_level"] = output["volume"]
            if output.get("tone"):
                data["tone"] = output["tone"]
            await session.call("siren", "turn_on", **data)
        else:
            if output.get("volume") is not None:
                await session.call(
                    "media_player", "volume_set", volume_level=output["volume"]
                )
            if kind == "tts":
                source = (
                    output.get("recovery_message")
                    if recovery
                    else output.get("message")
                )
                message = (
                    Template(source, self.hass).async_render(
                        variables, parse_result=False
                    )
                    if source
                    else variables["message"]
                )
                data = {
                    "entity_id": output["tts_entity"],
                    "media_player_entity_id": session.entity_id,
                    "message": message,
                }
                if output.get("language"):
                    data["language"] = output["language"]
                if session.external_change:
                    return
                session.used = session.playback_started = True
                async with asyncio.timeout(NOTIFY_TIMEOUT):
                    await self.hass.services.async_call(
                        "tts", "speak", data, blocking=True, context=session.context
                    )
            else:
                session.playback_started = True
                await session.call(
                    "media_player",
                    "play_media",
                    media_content_id=output["media_url"],
                    media_content_type=output.get("media_type") or "music",
                )
        await _wait(self.hass, duration)

    async def _cleanup(self, output, session):
        if session.external_change:
            return
        kind = output["type"]
        if kind == "light":
            if output["restore"]:
                await session.restore_light()
            else:
                await session.call("light", "turn_off")
        elif kind == "siren":
            await session.call("siren", "turn_off")
        else:
            if session.playback_started:
                await session.call("media_player", "media_stop")
            if (
                output["restore"]
                and output.get("volume") is not None
                and session.before
            ):
                volume = session.before.attributes.get("volume_level")
                if volume is not None:
                    await session.call(
                        "media_player", "volume_set", volume_level=volume
                    )
