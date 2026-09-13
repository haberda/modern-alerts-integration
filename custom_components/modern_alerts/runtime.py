"""Event-driven alert lifecycle and timer ownership."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

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
        for listener in tuple(self._listeners):
            listener()

    @callback
    def start(self) -> None:
        self._stopped = False
        self._unsubscribe = async_track_state_change_event(
            self.hass, [self.config.entity_id], self._state_changed
        )
        if self.config.evaluate_on_start:
            state = self.hass.states.get(self.config.entity_id)
            if state is not None:
                self._evaluate(state.state)

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        if self._stopped or (state := event.data["new_state"]) is None:
            return
        self._context = event.context
        self._evaluate(state.state)

    @callback
    def _evaluate(self, state: str) -> None:
        matches = state == self.config.state
        if matches == self.firing:
            return
        self._generation += 1
        if matches:
            self.firing = True
            self.acknowledged = False
            self.attempted = False
            self.next_index = 0
            if not self.config.skip_first:
                self._queue_reminder()
            self._schedule()
        else:
            send_done = self.attempted
            self._cancel()
            self.firing = False
            self.acknowledged = False
            self.attempted = False
            if send_done and self.config.done_message is not None:
                self._task(self._deliver(self.config, self._generation, done=True))
        self._publish()

    @callback
    def _cancel(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        self.next_notification = None

    @callback
    def _schedule(self) -> None:
        self._cancel()
        self.next_notification = dt_util.utcnow() + timedelta(
            minutes=self.config.repeat[self.next_index]
        )
        self.next_index = min(self.next_index + 1, len(self.config.repeat) - 1)
        generation = self._generation

        @callback
        def due(now: datetime) -> None:
            if self._stopped or generation != self._generation or not self.firing:
                return
            self._queue_reminder()
            self._schedule()
            self._publish()

        self._cancel_timer = async_track_point_in_utc_time(
            self.hass, due, self.next_notification
        )

    @callback
    def _task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = self.hass.async_create_task(coro, "modern_alerts notification")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _queue_reminder(self) -> None:
        if self.acknowledged or self._reminder_pending == self._generation:
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
                )
            )

        try:
            async with self._delivery_lock:
                if not valid():
                    return
                if not done:
                    self.attempted = True
                    self.last_attempt = dt_util.utcnow()
                self.errors = await async_notify(
                    self.hass, config, done=done, context=context, valid=valid
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
        self._publish()

    async def async_test_notification(self, context: Context | None = None) -> None:
        """Explicit test: no incident bookkeeping or resolution eligibility."""
        errors = await async_notify(self.hass, self.config, context=context)
        self.errors = errors
        self._publish()
        if errors:
            raise ServiceValidationError(
                "Test notification failed; inspect the status entity's errors"
            )

    async def async_stop(self) -> None:
        """Unload quietly; cancel timers, listeners, and pending deliveries."""
        self._stopped = True
        self._generation += 1
        self._cancel()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def async_update_config(self, config: AlertConfig) -> None:
        """Apply edits without losing acknowledgement for an unchanged condition."""
        old = self.config
        condition_changed = (old.entity_id, old.state) != (
            config.entity_id,
            config.state,
        )
        await self.async_stop()
        self.config = config
        if condition_changed:
            self.firing = self.acknowledged = self.attempted = False
            self.next_index = 0
        if not config.can_acknowledge:
            self.acknowledged = False
        self.next_index = min(self.next_index, len(config.repeat) - 1)
        self.start()
        # Edits reconcile the current source even in legacy startup mode.
        if (state := self.hass.states.get(config.entity_id)) is not None:
            self._evaluate(state.state)
        if self.firing and self.next_notification is None:
            self._schedule()
        self._publish()
