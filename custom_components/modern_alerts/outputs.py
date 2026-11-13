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


def _close(left, right, tolerance):
    """Compare reported values, allowing device rounding and gamut limits."""
    if left is None or right is None:
        return left is right
    if isinstance(left, list | tuple) and isinstance(right, list | tuple):
        return len(left) == len(right) and all(
            _close(a, b, tolerance) for a, b in zip(left, right, strict=True)
        )
    try:
        return abs(float(left) - float(right)) <= tolerance
    except TypeError, ValueError:
        return left == right


# Attribute -> tolerance. Lights always expose a derived rgb_color while in a
# colour mode, so it stands in for the hs/xy/rgbw representations.
LIGHT_SIGNATURE = {
    "brightness": 3,
    "rgb_color": 12,
    "color_temp_kelvin": 100,
    "effect": 0,
}


class DeviceSession:
    """Exclusive device use, retaining external changes instead of undoing them.

    A change counts as external only when it is not ours by context, differs
    from the last report beyond rounding, and is not the device confirming
    what was just commanded. Speakers report their own playback lifecycle
    (playing, idle) without a service context; that is never a manual change.
    """

    def __init__(self, hass, entity_id, context):
        self.hass = hass
        self.entity_id = entity_id
        self.domain = entity_id.split(".", 1)[0]
        self.context = context
        self.before = hass.states.get(entity_id)
        self.external_change = False
        self.used = False
        self.playback_started = False
        self.expected: dict = {}
        self.content = None
        self.last = self.before
        self.unsubscribe = async_track_state_change_event(
            hass, [entity_id], self.changed
        )

    def _same(self, old, new) -> bool:
        if old is None or new is None:
            return old is new
        if self.domain == "media_player":
            # Volume becomes unreported while some speakers are off or idle.
            old_volume = old.attributes.get("volume_level")
            new_volume = new.attributes.get("volume_level")
            return (
                old_volume is None
                or new_volume is None
                or _close(old_volume, new_volume, 0.011)
            )
        if old.state != new.state:
            return False
        if self.domain != "light":
            return True
        return all(
            _close(old.attributes.get(key), new.attributes.get(key), tolerance)
            for key, tolerance in LIGHT_SIGNATURE.items()
        )

    def _confirms(self, new) -> bool:
        """Whether a late report matches the most recent command."""
        if new is None or not self.expected:
            return False
        if self.domain == "media_player":
            volume = self.expected.get("volume_level")
            return volume is not None and _close(
                new.attributes.get("volume_level"), volume, 0.02
            )
        if new.state != self.expected.get("state"):
            return False
        if self.domain != "light" or new.state != "on":
            return True
        turned_on = self.last is None or self.last.state != new.state
        for key, tolerance in LIGHT_SIGNATURE.items():
            wanted = self.expected.get(key)
            reported = new.attributes.get(key)
            if wanted is not None:
                # Commanded values may be clipped to what the device supports.
                if reported is not None and not _close(reported, wanted, tolerance * 3):
                    return False
            elif key == "color_temp_kelvin" and "rgb_color" in self.expected:
                continue
            elif not turned_on and not _close(
                self.last.attributes.get(key), reported, tolerance
            ):
                # Anything that was not commanded must be left as it was.
                return False
        return True

    def _foreign_media(self, new) -> bool:
        """Someone started different media on a speaker this session is using."""
        if self.domain != "media_player" or new is None:
            return False
        content = new.attributes.get("media_content_id")
        before = self.before.attributes.get("media_content_id") if self.before else None
        if content is None or content in (before, self.content):
            return False
        if self.playback_started and self.content is None:
            # The first new item after our play request is our own playback,
            # even when a slow speaker reports it without our context.
            self.content = content
            return False
        return True

    @callback
    def changed(self, event):
        new = event.data["new_state"]
        own = (
            event.context.id == self.context.id
            or event.context.parent_id == self.context.id
        )
        if own:
            if (
                new is not None
                and self.playback_started
                and (content := new.attributes.get("media_content_id")) is not None
            ):
                self.content = content
        elif self._foreign_media(new) or not (
            self._same(self.last, new) or self._confirms(new)
        ):
            self.external_change = True
        self.last = new

    async def call(self, domain, service, **data):
        if self.external_change:
            return
        self.used = True
        if service in ("turn_on", "turn_off"):
            self.expected = {"state": service.removeprefix("turn_")}
            if data.get("brightness_pct") is not None:
                self.expected["brightness"] = data["brightness_pct"] * 2.55
            elif data.get("brightness") is not None:
                self.expected["brightness"] = data["brightness"]
            if data.get("rgb_color") is not None:
                self.expected["rgb_color"] = data["rgb_color"]
        elif service == "volume_set":
            self.expected = {"volume_level": data["volume_level"]}
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

    def __init__(self, hass: HomeAssistant, publish: Callable[[], None], event=None):
        self.hass = hass
        self.publish = publish
        self.event = event or (lambda *args, **kwargs: None)
        self.errors: dict[str, str] = {}
        self.seen: set[str] = set()
        self._runs: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        # Alert effects only; tests and recovery announcements are not listed.
        self._effects: set[asyncio.Task] = set()
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
    def stop(self, *, effects_only: bool = False) -> None:
        """Cancel runs. Silencing an incident leaves tests and recovery alone."""
        for task in tuple(self._effects if effects_only else self._tasks):
            if not task.done() and not task.cancelling():
                task.cancel()

    @callback
    def stop_ids(self, ids) -> None:
        for key in ids:
            if (
                (task := self._runs.get(key))
                and not task.done()
                and not task.cancelling()
            ):
                task.cancel()

    async def async_stop(self) -> None:
        self.stop()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    @callback
    def dispatch(self, outputs, variables, valid, *, recovery=False, test_id=None):
        """Start independent effects without blocking phone dispatch or timers."""
        launched = []
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
                break
            effect = test_id is None and not recovery
            first_run = effect and key not in self.seen
            if effect:
                self.seen.add(key)
            self.errors.pop(key, None)
            for error_key in tuple(self.errors):
                if error_key.startswith(f"{key}:"):
                    self.errors.pop(error_key)
            task = self.hass.async_create_background_task(
                self._run(
                    output, variables, valid, recovery=recovery, first_run=first_run
                ),
                f"Modern Alerts output {key}",
                eager_start=False,
            )
            self._runs[key] = task
            launched.append(key)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            if effect:
                self._effects.add(task)
                task.add_done_callback(self._effects.discard)
            task.add_done_callback(lambda _: self.publish())
        self.publish()
        return launched

    async def _run(self, output, variables, valid, *, recovery, first_run=False):
        key = output["id"]
        try:
            async with self._serial.setdefault(key, asyncio.Lock()):
                if not valid():
                    return
                self.event(
                    "output_started",
                    output_id=key,
                    incident_id=variables.get("incident_id", ""),
                )
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
                    reached = await asyncio.gather(
                        *(
                            self._device(output, entity, variables, valid, recovery)
                            for entity in output["entities"]
                        )
                    )
                    if first_run and not any(reached):
                        # Every device was busy: a run-once output has not run.
                        self.seen.discard(key)
        except asyncio.CancelledError:
            self.event(
                "output_stopped",
                output_id=key,
                incident_id=variables.get("incident_id", ""),
            )
            raise
        except Exception as err:
            self.errors[key] = type(err).__name__
            self.event("output_error", output_id=key, error=type(err).__name__)
        else:
            self.event(
                "output_finished",
                output_id=key,
                incident_id=variables.get("incident_id", ""),
            )
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

    async def _device(self, output, entity, variables, valid, recovery) -> bool:
        """Run one device's effect; False when the device could not be claimed."""
        session = None
        acquired = False
        lock = self._devices.setdefault(entity, asyncio.Lock())
        try:
            async with asyncio.timeout(NOTIFY_TIMEOUT):
                await lock.acquire()
            acquired = True
            if not valid():
                return True
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
            self.event(
                "output_error",
                output_id=output["id"],
                entity_id=entity,
                error=type(err).__name__,
            )
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
        return acquired

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
