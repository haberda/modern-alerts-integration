"""Event-driven alert lifecycle and timer ownership."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
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

from .conditions import evaluate as evaluate_conditions
from .grouping import groups
from .models import AlertConfig
from .notifications import async_notify
from .outputs import OutputManager
from .persistence import PersistenceMixin
from .policies import allowed, has_policy
from .profiles import resolve
from .tasks import TaskMixin


class AlertRuntime(PersistenceMixin, TaskMixin):
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
        self.history: list[dict[str, Any]] = []
        self.started_at: datetime | None = None
        self.stage_index = 0
        self.policy_errors: dict[str, str] = {}
        self._pending_outputs: set[str] = set()
        self._pending_notification = False
        self._unsub_presence = None
        self.outputs = OutputManager(hass, self._publish, self._record)
        self.test_output_id: str | None = None
        self._init_task()

    def _record(self, event: str, **details) -> None:
        if not self.config.history_limit:
            return
        self.history.append(
            {
                "at": dt_util.utcnow().isoformat(),
                "incident_id": self.incident_id,
                "event": event,
                **details,
            }
        )
        self.history = self.history[-self.config.history_limit :]

    @property
    def effective_config(self):
        return self.delivery_config(update_errors=True)

    def delivery_config(self, *, update_errors=False):
        """Resolve current destinations; previews do not mutate diagnostics."""
        config, missing = resolve(self.hass, self.config)
        errors = {"profiles": "unavailable"} if missing else {}
        staged = {key for stage in config.stages for key in stage["output_ids"]}
        if staged - {item["id"] for item in config.outputs}:
            errors["stages"] = "missing_outputs"
        enabled = set()
        notifiers, entities = list(config.notifiers), list(config.notify_entities)
        for stage in config.stages[: self.stage_index]:
            enabled.update(stage["output_ids"])
            notifiers.extend(stage["notifiers"])
            entities.extend(stage["notify_entities"])
        if config.data and entities:
            errors["notify_entities"] = "extra_data_unsupported"
            entities = []
        if update_errors:
            self.policy_errors = errors
        return replace(
            config,
            notifiers=tuple(dict.fromkeys(notifiers)),
            notify_entities=tuple(dict.fromkeys(entities)),
            outputs=tuple(
                item
                for item in config.outputs
                if item["id"] not in staged or item["id"] in enabled
            ),
        )

    @property
    def all_outputs(self):
        return resolve(self.hass, self.config)[0].outputs

    def _intervals(self):
        for stage in reversed(self.config.stages[: self.stage_index]):
            if stage["interval"] is not None:
                return (stage["interval"],)
        return self.config.repeat

    def _stage_deadline(self):
        if (
            self.started_at
            and self.stage_index < len(self.config.stages)
            and not self.acknowledged
        ):
            return self.started_at + timedelta(
                minutes=self.config.stages[self.stage_index]["after"]
            )
        return None

    def _bind_presence(self):
        if self._unsub_presence:
            self._unsub_presence()
            self._unsub_presence = None
        entities = set(self.config.delivery.get("presence_entities", []))
        for output in self.all_outputs:
            entities.update(output.get("delivery", {}).get("presence_entities", []))
        if entities:
            self._unsub_presence = async_track_state_change_event(
                self.hass, list(entities), self._presence_changed
            )

    @callback
    def _presence_changed(self, event):
        if not self._stopped:
            self._route_due()
            self._arm()
            self._publish()

    def _delivery_valid(self, generation):
        return (
            not self._stopped
            and self.firing
            and not self.acknowledged
            and not self.snoozed_until
            and not self.source_suspended
            and generation == self._generation
        )

    def _dispatch_outputs(
        self, config, *, pending_only=False, recovery=False, variables=None
    ):
        eligible = []
        for output in config.outputs:
            key = output["id"]
            if pending_only and key not in self._pending_outputs:
                continue
            if allowed(self.hass, output.get("delivery", {})):
                eligible.append(output)
                self._pending_outputs.discard(key)
            elif not recovery:
                self._pending_outputs.add(key)
        generation = self._generation
        started = self.outputs.dispatch(
            eligible,
            variables or self._output_variables(),
            lambda: (
                not self._stopped
                and generation == self._generation
                and (not self.firing if recovery else self._delivery_valid(generation))
            ),
            recovery=recovery,
        )
        if started and not recovery:
            self.attempted = True
        return started

    def _route_due(self):
        if not self._delivery_valid(self._generation):
            return
        config = self.effective_config
        blocked = {
            item["id"]
            for item in config.outputs
            if not allowed(self.hass, item.get("delivery", {}))
        }
        active_blocked = blocked.intersection(self.outputs.active)
        self.outputs.stop_ids(active_blocked)
        self._pending_outputs.update(active_blocked)
        self._pending_outputs.intersection_update(item["id"] for item in config.outputs)
        self._dispatch_outputs(config, pending_only=True)
        if self._pending_notification and allowed(self.hass, config.delivery):
            self._pending_notification = False
            self._task(self._deliver(config, self._generation))

    async def async_profiles_changed(self):
        if self._stopped:
            return
        self._generation += 1
        await self.outputs.async_stop()
        groups(self.hass).cancel(self.entry_id or str(id(self)))
        self._bind_presence()
        self._record("profiles_changed")
        self._publish()

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
        if (
            self.store
            and not self._stopped
            and (self.config.restore_state or self.is_task)
        ):
            snapshot = self.snapshot()
            self.store.async_delay_save(lambda: snapshot, 1)
        for listener in tuple(self._listeners):
            listener()

    def _condition(self) -> list[Any]:
        if self.is_task:
            # Schedule edits keep the incident and history; see TaskMixin.
            return ["schedule"]
        if self.config.conditions:
            return [
                "compound",
                self.config.condition_mode,
                list(self.config.conditions),
            ]
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
        if self.is_task:
            # Nothing is watched: the schedule decides when the task is due.
            self._restored = False
            self._task_reconcile(dt_util.utcnow())
            self._process_due()
            self._bind_presence()
            self._arm()
            self._publish()
            return
        self._unsubscribe = async_track_state_change_event(
            self.hass,
            list({row["entity_id"] for row in self.config.conditions})
            if self.config.conditions
            else [self.config.entity_id],
            self._state_changed,
        )
        if self._restored or self.config.evaluate_on_start:
            state = self.hass.states.get(self.config.entity_id)
            self._awaiting_source = self._restored
            self._evaluate(state.state if state else "unavailable")
        self._restored = False
        self._bind_presence()
        self._arm()

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        if self._stopped:
            return
        state = event.data["new_state"]
        if (
            state is None
            and not self.config.conditions
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
        uncertain = (
            not self.config.conditions
            and state in ("unknown", "unavailable")
            and state != self.config.state
        )
        if matches is None or (
            uncertain
            and (self.config.unavailable_policy == "suspend" or self._awaiting_source)
        ):
            if not self.source_suspended:
                self.outputs.stop(effects_only=True)
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
        if self.config.conditions:
            result = evaluate_conditions(
                self.hass, self.config.conditions, self.config.condition_mode
            )
            if (
                result is None
                and self.config.unavailable_policy == "resolve"
                and not self._awaiting_source
            ):
                return False
            return result
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
    def _transition(self, active: bool, *, notify_done: bool = True) -> None:
        """Start or end an incident. A missed task window ends without fanfare."""
        groups(self.hass).cancel(self.entry_id or str(id(self)))
        previous_variables = self._output_variables()
        send_done = (
            notify_done
            and self.attempted
            and (self.config.resolution_after_ack or not self.acknowledged)
        )
        self.outputs.stop()
        self._generation += 1
        self.pending_active = self.pending_due = None
        self.snoozed_until = None
        self.acknowledged = False
        self.attempted = False
        self.firing = active
        self.incident_id = uuid4().hex if active else ""
        self.action_token = uuid4().hex if active else ""
        self._pending_outputs.clear()
        self._pending_notification = False
        if active:
            self.started_at = dt_util.utcnow()
            self.stage_index = 0
            self._record("started")
            self.outputs.seen.clear()
            self.next_index = 0
            if not self.config.skip_first:
                self._queue_reminder()
            self._schedule()
        else:
            self.next_notification = None
            if send_done:
                self._dispatch_outputs(
                    self.effective_config, variables=previous_variables, recovery=True
                )
            self._record("resolved", incident_id=previous_variables["incident_id"])
            if send_done and self.config.done_message is not None:
                self._task(
                    self._deliver(self.effective_config, self._generation, done=True)
                )
            self.started_at = None

    @callback
    def _cancel(self) -> None:
        self._timer_generation += 1
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _schedule(self) -> None:
        self.next_notification = dt_util.utcnow() + timedelta(
            minutes=self._intervals()[min(self.next_index, len(self._intervals()) - 1)]
        )
        self.next_index = min(self.next_index + 1, len(self._intervals()) - 1)
        self._arm()

    @callback
    def _process_due(self) -> None:
        now = dt_util.utcnow()
        if self.snoozed_until and self.snoozed_until <= now:
            self.snoozed_until = None
            self._record("snooze_expired")
            if self.firing and not self.acknowledged:
                # The pause is over: remind now and restart the interval from
                # here, instead of staying silent until the old schedule.
                self.next_notification = now
        self._task_process(now)
        if self.source_suspended:
            return
        if self.pending_due and self.pending_due <= now:
            state = self.hass.states.get(self.config.entity_id)
            if (state or self.config.conditions) and self._matches(
                state.state if state else "unavailable"
            ) == self.pending_active:
                self._transition(self.pending_active)
            else:
                self.pending_active = self.pending_due = None
        if self.firing and not self.acknowledged:
            changed = False
            while (deadline := self._stage_deadline()) and deadline <= now:
                self.stage_index += 1
                self._record(
                    "escalated", stage=self.config.stages[self.stage_index - 1]["name"]
                )
                changed = True
            if changed:
                self.next_index = 0
                self.next_notification = now
        if self.firing and self.next_notification and self.next_notification <= now:
            self._queue_reminder()
            self._schedule()
        self._route_due()

    @callback
    def _arm(self) -> None:
        self._cancel()
        if self._stopped:
            return
        deadlines = [self.snoozed_until, *self._task_deadlines()]
        if not self.source_suspended:
            deadlines.extend((self.pending_due, self.next_notification))
            if self.firing:
                deadlines.append(self._stage_deadline())
                if not self.acknowledged and (
                    has_policy(self.config.delivery)
                    or any(
                        has_policy(item.get("delivery", {}))
                        for item in self.all_outputs
                    )
                ):
                    deadlines.append(dt_util.utcnow() + timedelta(minutes=1))
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
        config = self.effective_config
        self._dispatch_outputs(config)
        if allowed(self.hass, config.delivery):
            self._pending_notification = False
        self._task(self._deliver(config, self._generation))

    def _output_variables(self) -> dict[str, Any]:
        return {
            "alert_name": self.config.name,
            "entity_id": self.config.entity_id,
            "incident_id": self.incident_id,
            "message": self.config.message or self.config.name,
        }

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
                if not allowed(self.hass, config.delivery):
                    if not done and (config.notifiers or config.notify_entities):
                        self._pending_notification = True
                    self._record("delivery_held")
                    self._publish()
                    return
                if (
                    not done
                    and not (config.notifiers or config.notify_entities)
                    and (
                        self.all_outputs
                        or self.config.profile_ids
                        or self.config.stages
                    )
                ):
                    return
                if not done and (
                    config.delivery.get("group") or config.delivery.get("rate_limit")
                ):
                    self._pending_notification = True

                    def grouped_result(errors, phase):
                        if self._stopped or generation != self._generation:
                            return
                        if phase == "attempt":
                            self._pending_notification = False
                            self.attempted = True
                            self.last_attempt = dt_util.utcnow()
                            self._record("notification_attempt")
                        else:
                            self.errors = dict(errors)
                            self._record("notification_result", errors=dict(errors))
                        self._publish()

                    groups(self.hass).submit(
                        self.entry_id or str(id(self)),
                        config,
                        lambda: valid() and allowed(self.hass, config.delivery),
                        self.notification_actions(),
                        grouped_result,
                    )
                    self._publish()
                    return
                if not done:
                    self.attempted = True
                    self.last_attempt = dt_util.utcnow()
                    self._publish()
                self._record("resolution_attempt" if done else "notification_attempt")
                self.errors = await async_notify(
                    self.hass,
                    config,
                    done=done,
                    context=context,
                    valid=valid,
                    actions=self.notification_actions() if not done else None,
                )
                self._record("notification_result", errors=dict(self.errors))
                self._publish()
        finally:
            if not done and self._reminder_pending == generation:
                self._reminder_pending = None

    @callback
    def acknowledge(self, acknowledged: bool, context: Context | None = None) -> None:
        if acknowledged and not self.config.can_acknowledge:
            raise ServiceValidationError("This alert cannot be acknowledged")
        self._context = context
        if self.acknowledged != acknowledged and self.firing:
            self._record("acknowledged" if acknowledged else "resumed")
            if not acknowledged:
                # Someone asked to be reminded again: a replayed Acknowledge
                # from an earlier notification must not silence it.
                self.action_token = uuid4().hex
        self.acknowledged = acknowledged
        if acknowledged:
            groups(self.hass).cancel(self.entry_id or str(id(self)))
            self._pending_notification = False
            self._pending_outputs.clear()
            self.outputs.stop(effects_only=True)
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
        self.outputs.stop(effects_only=True)
        groups(self.hass).cancel(self.entry_id or str(id(self)))
        self._record("snoozed")
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
        if self.is_task:
            actions.append({"action": prefix + "DONE", "title": "Done"})
        if self.config.can_acknowledge:
            actions.append({"action": prefix + "ACK", "title": "Acknowledge"})
        if self.config.enable_snooze:
            actions.append({"action": prefix + "SNOOZE", "title": "Snooze"})
        # Android shows at most three actions; the controls take priority.
        if self.status_entity_id and len(actions) < 3:
            actions.append(
                {
                    "action": "URI",
                    "title": "Open alert",
                    "uri": f"entityId:{self.status_entity_id}",
                }
            )
        return actions

    @callback
    def handle_mobile_action(
        self, action: str, context: Context | None = None, by: str | None = None
    ) -> None:
        """Apply a phone control for the current incident.

        The token stays valid while the incident is being handled, so every
        recipient's copy keeps working, and repeats are harmless because each
        control is idempotent. A new incident or an explicit Resume issues a
        new token.
        """
        if self._stopped or not self.firing or not self.attempted:
            return
        valid = {
            item["action"]
            for item in self.notification_actions()
            if item["action"] != "URI"
        }
        if action not in valid:
            return
        if action.endswith(":DONE"):
            self.complete(context, via="phone_action", by=by)
        elif action.endswith(":ACK"):
            if not self.acknowledged:
                self.acknowledge(True, context)
        elif not self.acknowledged and not self.snoozed_until:
            self.snooze(context=context)

    async def async_test_notification(self, context: Context | None = None) -> None:
        """Explicit test: no incident bookkeeping or resolution eligibility."""
        if not (
            self.effective_config.notifiers or self.effective_config.notify_entities
        ):
            raise ServiceValidationError("This alert has no notification destinations")
        errors = await async_notify(
            self.hass,
            self.effective_config,
            context=context,
            valid=lambda: not self._stopped,
        )
        self.errors = errors
        self._publish()
        if errors:
            raise ServiceValidationError(
                "Test notification failed; inspect the status entity's errors"
            )

    async def async_test_output(self, output_id: str | None = None) -> None:
        """Start only the selected bounded effect; never create an incident."""
        key = output_id or self.test_output_id
        if key is None and self.all_outputs:
            key = self.all_outputs[0]["id"]
        if self._stopped or not any(item["id"] == key for item in self.all_outputs):
            raise ServiceValidationError("Select a configured output to test")
        if key in self.outputs.active:
            raise ServiceValidationError("This output is already running")
        self.outputs.dispatch(
            self.all_outputs,
            self._output_variables(),
            lambda: not self._stopped,
            test_id=key,
        )

    async def async_stop_outputs(self) -> None:
        """Stop effects and test runs without acknowledging the incident."""
        await self.outputs.async_stop()

    async def async_stop(self) -> None:
        """Unload quietly; cancel timers, listeners, and pending deliveries."""
        if self._stopped:
            return
        snapshot = self.snapshot()
        self._stopped = True
        groups(self.hass).cancel(self.entry_id or str(id(self)))
        self._generation += 1
        self._cancel()
        self.next_notification = None
        if self._unsub_presence:
            self._unsub_presence()
            self._unsub_presence = None
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.outputs.async_stop()
        await self.async_save(snapshot)

    async def async_update_config(self, config: AlertConfig) -> None:
        """Apply edits without losing acknowledgement for an unchanged condition."""
        old = self.config
        old_condition = self._condition()
        snapshot = self.snapshot()
        await self.async_stop()
        self.config = config
        self.history = (
            self.history[-config.history_limit :] if config.history_limit else []
        )
        self.stage_index = min(self.stage_index, len(config.stages))
        if not any(item["id"] == self.test_output_id for item in config.outputs):
            self.test_output_id = None
        if old.restore_state and not config.restore_state:
            await self.async_save()
        condition_changed = old_condition != self._condition()
        deadline = snapshot["next_notification"]
        self.next_notification = dt_util.parse_datetime(deadline) if deadline else None
        self.source_suspended = False
        if condition_changed:
            self.firing = self.acknowledged = self.attempted = False
            self.next_index = 0
            self.started_at = None
            self.stage_index = 0
            self.next_notification = self.pending_due = self.pending_active = None
            self.snoozed_until = None
            self.incident_id = self.action_token = ""
        if old.repeat != config.repeat or old.stages != config.stages:
            self.next_notification = None
            self.next_index = 0
            self.stage_index = 0
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
        if self.is_task:
            pass
        elif (state := self.hass.states.get(config.entity_id)) is not None:
            self._evaluate(state.state)
        elif (
            config.conditions
            or config.unavailable_policy == "suspend"
            or config.numeric_below is not None
            or config.numeric_above is not None
        ):
            self._evaluate("unavailable")
        if self.firing and self.next_notification is None:
            self._schedule()
        self._publish()
