"""Event-driven alert lifecycle and timer ownership."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from math import isfinite
from typing import Any
from uuid import uuid4

from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .models import AlertConfig
from .notifications import async_notify


class AlertRuntime:
    """One alert. All transitions run atomically on HA's event loop.

    Provider I/O runs outside transitions, so a slow notifier cannot prevent
    acknowledgement/resolution. Delivery tasks are ordered and generation-guarded.
    """

    def __init__(self, hass: HomeAssistant, config: AlertConfig) -> None:
        self.hass = hass
        self.config = config
        self.firing = False
        self.acknowledged = False
        self.attempted = False
        self.next_index = 0
        self.next_notification: datetime | None = None
        self.last_attempt: datetime | None = None
        self.errors: dict[str, str] = {}
        self._generation = 0
        self._stopped = True
        self._cancel_timer: Callable[[], None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._tasks: set[asyncio.Task] = set()
        self._delivery_lock = asyncio.Lock()
        self._reminder_pending: int | None = None
        self._context: Context | None = None
        self.store = None
        self.snoozed_until: datetime | None = None
        self.pending_active: bool | None = None
        self.pending_due: datetime | None = None
        self.source_suspended = False
        self.entry_id = ""
        self.status_entity_id: str | None = None
        self.incident_id = ""
        self.action_token = ""
        self._timer_generation = 0
        self._restored = False
        self._awaiting_source = False

    @property
    def state(self) -> str:
        """Legacy lifecycle values, independent of administrative enablement."""
        return ("off" if self.acknowledged else "on") if self.firing else "idle"

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _publish(self) -> None:
        if self.store and not self._stopped and self.config.restore_state:
            snapshot = self.snapshot()
            self.store.async_delay_save(lambda: snapshot, 1)
        for listener in tuple(self._listeners):
            listener()

    def _condition(self) -> list[Any]:
        return [
            self.config.entity_id,
            self.config.state,
            self.config.numeric_below,
            self.config.numeric_above,
            self.config.numeric_recover_above,
            self.config.numeric_recover_below,
            self.config.numeric_unit,
        ]

    @callback
    def start(self) -> None:
        if not self._stopped:
            return
        self._stopped = False
        self._unsubscribe = async_track_state_change_event(
            self.hass, [self.config.entity_id], self._state_changed
        )
        if self._restored or self.config.evaluate_on_start:
            state = self.hass.states.get(self.config.entity_id)
            self._awaiting_source = self._restored
            self._evaluate(state.state if state else "unavailable")
        self._restored = False
        self._arm()

    def snapshot(self) -> dict[str, Any]:
        """Serialize deadlines, not time remaining, to survive downtime."""
        data = {
            key: getattr(self, key)
            for key in (
                "firing",
                "acknowledged",
                "attempted",
                "next_index",
                "pending_active",
                "incident_id",
                "action_token",
            )
        }
        data.update(schema=2, condition=self._condition())
        for key in (
            "next_notification",
            "last_attempt",
            "snoozed_until",
            "pending_due",
        ):
            value = getattr(self, key)
            data[key] = value.isoformat() if value else None
        return data

    def restore(self, data: dict[str, Any]) -> None:
        """Reject incomplete/old snapshots instead of restoring half an incident."""
        if not self.config.restore_state:
            return
        try:
            if data["schema"] != 2 or data["condition"] != self._condition():
                return
            values = {}
            for key in ("firing", "acknowledged", "attempted"):
                if type(data[key]) is not bool:
                    raise ValueError
                values[key] = data[key]
            index = data["next_index"]
            if type(index) is not int or not 0 <= index < len(self.config.repeat):
                raise ValueError
            values["next_index"] = index
            for key in (
                "next_notification",
                "last_attempt",
                "snoozed_until",
                "pending_due",
            ):
                raw = data[key]
                value = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
                if raw is not None and (value is None or value.tzinfo is None):
                    raise ValueError
                values[key] = value
            for key in ("incident_id", "action_token"):
                if not isinstance(data[key], str):
                    raise ValueError
                values[key] = data[key]
            pending = data["pending_active"]
            if pending is not None and type(pending) is not bool:
                raise ValueError
            if (pending is None) != (values["pending_due"] is None):
                raise ValueError
            if values["firing"] and (
                values["next_notification"] is None or not values["incident_id"]
            ):
                raise ValueError
            if not values["firing"] and (
                values["next_notification"] or values["snoozed_until"]
            ):
                raise ValueError
            values["pending_active"] = pending
        except KeyError, TypeError, ValueError, OverflowError:
            self.errors = {"restore": "invalid_snapshot"}
            return
        for key, value in values.items():
            setattr(self, key, value)
        self._restored = True

    async def async_save(self, snapshot: dict[str, Any] | None = None) -> None:
        if self.store:
            if self.config.restore_state:
                await self.store.async_save(
                    snapshot if snapshot is not None else self.snapshot()
                )
            else:
                await self.store.async_remove()

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        if self._stopped:
            return
        state = event.data["new_state"]
        if (
            state is None
            and self.config.unavailable_policy == "resolve"
            and not self._awaiting_source
            and self.config.numeric_below is None
            and self.config.numeric_above is None
        ):
            return
        self._context = event.context
        self._evaluate(state.state if state else "unavailable")

    @callback
    def _evaluate(self, state: str) -> None:
        matches = self._matches(state)
        uncertain = state in ("unknown", "unavailable") and state != self.config.state
        if matches is None or (
            uncertain
            and (self.config.unavailable_policy == "suspend" or self._awaiting_source)
        ):
            self.source_suspended = True
            # A gap in observed data breaks a sustained transition, except while
            # waiting for the first usable state of a restored incident.
            if not self._awaiting_source:
                self.pending_active = self.pending_due = None
            self._arm()
            self._publish()
            return
        self.source_suspended = False
        self._awaiting_source = False
        if matches == self.firing:
            self.pending_active = self.pending_due = None
        elif self.pending_active != matches:
            delay = (
                self.config.activation_delay if matches else self.config.recovery_delay
            )
            if delay:
                self.pending_active = matches
                self.pending_due = dt_util.utcnow() + timedelta(seconds=delay)
            else:
                self._transition(matches)
        self._process_due()
        self._arm()
        self._publish()

    def _matches(self, state: str) -> bool | None:
        if self.config.numeric_below is None and self.config.numeric_above is None:
            return state == self.config.state
        if self.config.numeric_unit:
            source = self.hass.states.get(self.config.entity_id)
            if (
                source is None
                or source.attributes.get("unit_of_measurement")
                != self.config.numeric_unit
            ):
                return None
        try:
            value = float(state)
            if not isfinite(value):
                return None
        except ValueError, TypeError:
            return None
        if self.firing:
            if self.config.numeric_recover_above is not None:
                return value <= self.config.numeric_recover_above
            if self.config.numeric_recover_below is not None:
                return value >= self.config.numeric_recover_below
        if self.config.numeric_below is not None:
            return value < self.config.numeric_below
        return value > self.config.numeric_above

    @callback
    def _transition(self, active: bool) -> None:
        self._generation += 1
        self.pending_active = self.pending_due = None
        self.snoozed_until = None
        self.acknowledged = False
        send_done = self.attempted
        self.attempted = False
        self.firing = active
        self.incident_id = uuid4().hex if active else ""
        self.action_token = uuid4().hex if active else ""
        if active:
            self.next_index = 0
            if not self.config.skip_first:
                self._queue_reminder()
            self._schedule()
        else:
            self.next_notification = None
            if send_done and self.config.done_message is not None:
                self._task(self._deliver(self.config, self._generation, done=True))

    @callback
    def _cancel(self) -> None:
        self._timer_generation += 1
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _schedule(self) -> None:
        self.next_notification = dt_util.utcnow() + timedelta(
            minutes=self.config.repeat[self.next_index]
        )
        self.next_index = min(self.next_index + 1, len(self.config.repeat) - 1)
        self._arm()

    @callback
    def _process_due(self) -> None:
        now = dt_util.utcnow()
        if self.snoozed_until and self.snoozed_until <= now:
            self.snoozed_until = None
        if self.source_suspended:
            return
        if self.pending_due and self.pending_due <= now:
            state = self.hass.states.get(self.config.entity_id)
            if state and self._matches(state.state) == self.pending_active:
                self._transition(self.pending_active)
            else:
                self.pending_active = self.pending_due = None
        if self.firing and self.next_notification and self.next_notification <= now:
            self._queue_reminder()
            self._schedule()

    @callback
    def _arm(self) -> None:
        self._cancel()
        if self._stopped:
            return
        deadlines = [self.snoozed_until]
        if not self.source_suspended:
            deadlines.extend((self.pending_due, self.next_notification))
        deadlines = [value for value in deadlines if value is not None]
        if not deadlines:
            return
        generation = self._timer_generation

        @callback
        def due(now: datetime) -> None:
            if self._stopped or generation != self._timer_generation:
                return
            self._process_due()
            self._arm()
            self._publish()

        self._cancel_timer = async_track_point_in_utc_time(
            self.hass, due, min(deadlines)
        )

    @callback
    def _task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = self.hass.async_create_task(coro, "modern_alerts notification")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _queue_reminder(self) -> None:
        if (
            self.acknowledged
            or self.snoozed_until
            or self.source_suspended
            or self._reminder_pending == self._generation
        ):
            return
        self._reminder_pending = self._generation
        self._task(self._deliver(self.config, self._generation))

    async def _deliver(
        self, config: AlertConfig, generation: int, *, done: bool = False
    ) -> None:
        context = self._context

        def valid() -> bool:
            return not self._stopped and (
                done
                or (
                    generation == self._generation
                    and self.firing
                    and not self.acknowledged
                    and not self.snoozed_until
                    and not self.source_suspended
                )
            )

        try:
            async with self._delivery_lock:
                if not valid():
                    return
                if not done:
                    self.attempted = True
                    self.last_attempt = dt_util.utcnow()
                    self._publish()
                self.errors = await async_notify(
                    self.hass,
                    config,
                    done=done,
                    context=context,
                    valid=valid,
                    actions=self.notification_actions() if not done else None,
                )
                self._publish()
        finally:
            if not done and self._reminder_pending == generation:
                self._reminder_pending = None

    @callback
    def acknowledge(self, acknowledged: bool, context: Context | None = None) -> None:
        if acknowledged and not self.config.can_acknowledge:
            raise ServiceValidationError("This alert cannot be acknowledged")
        self._context = context
        self.acknowledged = acknowledged
        self.snoozed_until = None
        self._arm()
        self._publish()

    @callback
    def snooze(
        self, minutes: float | None = None, context: Context | None = None
    ) -> None:
        if not self.config.enable_snooze or not self.firing:
            raise ServiceValidationError("Snooze is unavailable for this alert")
        minutes = self.config.snooze_minutes if minutes is None else minutes
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int | float)
            or not isfinite(minutes)
            or not 0 < minutes <= 10080
        ):
            raise ServiceValidationError(
                "Snooze duration must be between 0 and 10080 minutes (exclusive of 0)"
            )
        self._context = context
        self.snoozed_until = dt_util.utcnow() + timedelta(minutes=minutes)
        self._arm()
        self._publish()

    @callback
    def cancel_snooze(self) -> None:
        self.snoozed_until = None
        self._arm()
        self._publish()

    def notification_actions(self) -> list[dict[str, str]]:
        if not self.config.action_buttons or not self.entry_id or not self.firing:
            return []
        prefix = (
            f"MODERN_ALERTS:{self.entry_id}:{self.incident_id}:{self.action_token}:"
        )
        actions = []
        if self.config.can_acknowledge:
            actions.append({"action": prefix + "ACK", "title": "Acknowledge"})
        if self.config.enable_snooze:
            actions.append({"action": prefix + "SNOOZE", "title": "Snooze"})
        if self.status_entity_id:
            actions.append(
                {
                    "action": "URI",
                    "title": "Open alert",
                    "uri": f"entityId:{self.status_entity_id}",
                }
            )
        return actions

    @callback
    def handle_mobile_action(self, action: str, context: Context | None = None) -> None:
        if self._stopped or not self.firing or not self.attempted:
            return
        valid = {
            item["action"]
            for item in self.notification_actions()
            if item["action"] != "URI"
        }
        if action not in valid:
            return
        if action.endswith(":ACK"):
            self.acknowledge(True, context)
        else:
            self.snooze(context=context)
        # Repeated callbacks and controls from older notifications become inert.
        self.action_token = uuid4().hex
        self._publish()

    async def async_test_notification(self, context: Context | None = None) -> None:
        """Explicit test: no incident bookkeeping or resolution eligibility."""
        if not (self.config.notifiers or self.config.notify_entities):
            raise ServiceValidationError("This alert has no notification destinations")
        errors = await async_notify(
            self.hass, self.config, context=context, valid=lambda: not self._stopped
        )
        self.errors = errors
        self._publish()
        if errors:
            raise ServiceValidationError(
                "Test notification failed; inspect the status entity's errors"
            )

    async def async_stop(self) -> None:
        """Unload quietly; cancel timers, listeners, and pending deliveries."""
        if self._stopped:
            return
        snapshot = self.snapshot()
        self._stopped = True
        self._generation += 1
        self._cancel()
        self.next_notification = None
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.async_save(snapshot)

    async def async_update_config(self, config: AlertConfig) -> None:
        """Apply edits without losing acknowledgement for an unchanged condition."""
        old = self.config
        old_condition = self._condition()
        snapshot = self.snapshot()
        await self.async_stop()
        self.config = config
        if old.restore_state and not config.restore_state:
            await self.async_save()
        condition_changed = old_condition != self._condition()
        deadline = snapshot["next_notification"]
        self.next_notification = dt_util.parse_datetime(deadline) if deadline else None
        self.source_suspended = False
        if condition_changed:
            self.firing = self.acknowledged = self.attempted = False
            self.next_index = 0
            self.next_notification = self.pending_due = self.pending_active = None
            self.snoozed_until = None
            self.incident_id = self.action_token = ""
        if old.repeat != config.repeat:
            self.next_notification = None
            self.next_index = 0
        if (old.activation_delay, old.recovery_delay) != (
            config.activation_delay,
            config.recovery_delay,
        ):
            self.pending_due = self.pending_active = None
        if not config.enable_snooze:
            self.snoozed_until = None
        if not config.can_acknowledge:
            self.acknowledged = False
        self.next_index = min(self.next_index, len(config.repeat) - 1)
        self.start()
        # Edits reconcile the current source even in legacy startup mode.
        if (state := self.hass.states.get(config.entity_id)) is not None:
            self._evaluate(state.state)
        elif (
            config.unavailable_policy == "suspend"
            or config.numeric_below is not None
            or config.numeric_above is not None
        ):
            self._evaluate("unavailable")
        if self.firing and self.next_notification is None:
            self._schedule()
        self._publish()
