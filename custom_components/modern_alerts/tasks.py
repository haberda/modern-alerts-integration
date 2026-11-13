"""Recurring tasks: an incident driven by a schedule instead of a watched entity.

"Due" takes the place of "the watched condition holds". Everything after an
incident starts (reminders, acknowledgement, snooze, outputs, escalation) is
the ordinary alert lifecycle. Only completion ends a due task; acknowledgement
merely stops the reminders for this occurrence.
"""

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import Context, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from . import schedule


class TaskMixin:
    """Schedule state and completion for AlertRuntime."""

    def _init_task(self) -> None:
        self.next_due: datetime | None = None
        self.last_completed: datetime | None = None
        self.completed_by: str | None = None
        self.missed = 0
        self.task_done = False
        self._task_sig: list[Any] | None = None
        self._task_seed: str | None = None

    @property
    def is_task(self) -> bool:
        return self.config.is_task

    def _task_signature(self) -> list[Any]:
        config = self.config
        return [
            config.task_mode,
            config.task_interval,
            config.task_rrule,
            config.task_time,
            config.task_window,
        ]

    @property
    def window_end(self) -> datetime | None:
        """When a due fixed-schedule occurrence stops being actionable."""
        length = schedule.window(self.config)
        if self.is_task and length and self.firing and self.next_due:
            return self.next_due + length
        return None

    @property
    def days_remaining(self) -> int | None:
        return schedule.days_remaining(self.next_due, dt_util.utcnow())

    @property
    def upcoming(self) -> bool:
        """Within the lead time of the next due date, but not yet due."""
        lead = self.config.task_lead_days
        return bool(
            self.is_task
            and lead
            and self.next_due
            and not self.firing
            and dt_util.utcnow() >= self.next_due - timedelta(days=lead)
        )

    def seed_task(self, last_completed: datetime | None) -> None:
        """Carry a previous completion into a newly imported task."""
        if self._task_sig is None and last_completed is not None:
            self.last_completed = dt_util.as_utc(last_completed)
            self.completed_by = "import"

    def _task_reconcile(self, now: datetime) -> None:
        """Seed a new task, or recompute after its schedule was edited."""
        config = self.config
        signature, seed = self._task_signature(), config.task_first_due
        if self._task_sig is None or signature != self._task_sig:
            # History and the last completion survive a schedule edit.
            self.task_done = False
            self.next_due = schedule.initial_due(
                config,
                now,
                self.last_completed,
                first_schedule=self._task_sig is None,
            )
        elif seed != self._task_seed and seed:
            self.task_done = False
            self.next_due = (
                schedule.open_or_next(config, now)
                if config.task_mode == "fixed"
                else schedule.parse_local(seed)
            )
        self._task_sig, self._task_seed = signature, seed
        if self.firing and (self.next_due is None or self.next_due > now):
            # The edit moved the due date into the future.
            self._transition(False, notify_done=False)

    @callback
    def _task_process(self, now: datetime) -> None:
        """Close missed windows and start an incident when the task is due."""
        if not self.is_task or self.task_done or self.next_due is None:
            return
        length = schedule.window(self.config)
        if length and self.next_due + length <= now:
            count, resume = schedule.missed_between(self.config, self.next_due, now)
            if count:
                self.missed += count
                self._record("missed", count=count)
                if self.firing:
                    # Nobody did it: stop without a "done" message.
                    self._transition(False, notify_done=False)
            self.next_due = resume
        if not self.firing and self.next_due and self.next_due <= now:
            self._transition(True)

    def _task_deadlines(self) -> list[datetime]:
        """Exact wake-up times; a task is never polled."""
        if not self.is_task or self.task_done or self.next_due is None:
            return []
        now = dt_util.utcnow()
        deadlines = [self.window_end if self.firing else self.next_due]
        # Day counts and the "upcoming" flag change without any event.
        tomorrow = dt_util.as_local(now).date() + timedelta(days=1)
        deadlines.append(dt_util.as_utc(dt_util.start_of_local_day(tomorrow)))
        if self.config.task_lead_days:
            flip = self.next_due - timedelta(days=self.config.task_lead_days)
            if flip > now:
                deadlines.append(flip)
        return [value for value in deadlines if value is not None]

    @callback
    def complete(
        self,
        context: Context | None = None,
        *,
        completed_at: datetime | None = None,
        via: str = "service",
        by: str | None = None,
    ) -> None:
        """Mark the task done, resolve its incident, and schedule the next one."""
        if not self.is_task:
            raise ServiceValidationError("Only recurring tasks can be completed")
        now = dt_util.utcnow()
        when = dt_util.as_utc(completed_at) if completed_at else now
        if when > now + timedelta(minutes=1):
            raise ServiceValidationError("A completion time cannot be in the future")
        when = min(when, now)
        config = self.config
        was_due = self.firing
        was_upcoming = self.upcoming
        self._context = context
        self._record("completed", via=via, **({"by": by} if by else {}))
        if self.firing:
            self._transition(False)
        self.last_completed = when
        self.completed_by = by or via
        if config.task_mode == "once":
            self.task_done = True
            self.next_due = None
        elif config.task_mode == "after_completion":
            self.next_due = schedule.due_after_completion(config, when)
        elif was_due:
            self.next_due = schedule.occurrence_after(config, now)
        elif was_upcoming and self.next_due:
            # Done shortly before it was due: skip that occurrence.
            self.next_due = schedule.occurrence_after(config, self.next_due)
        if not self._stopped:
            self._process_due()
            self._arm()
        self._publish()

    async def async_complete(
        self,
        context: Context | None = None,
        *,
        completed_at: datetime | None = None,
        via: str = "service",
    ) -> None:
        """Complete, naming the Home Assistant user behind the request."""
        self.complete(
            context,
            completed_at=completed_at,
            via=via,
            by=await async_actor(self.hass, context),
        )

    def task_snapshot(self) -> dict[str, Any] | None:
        if not self.is_task:
            return None
        return {
            "next_due": self.next_due.isoformat() if self.next_due else None,
            "last_completed": self.last_completed.isoformat()
            if self.last_completed
            else None,
            "completed_by": self.completed_by,
            "missed": self.missed,
            "done": self.task_done,
            "signature": self._task_signature()
            if self._task_sig is None
            else self._task_sig,
            "seed": self._task_seed,
        }

    def task_restore(self, data: Any) -> None:
        """Restore the schedule; anything malformed reseeds it from config."""
        if not self.is_task or not isinstance(data, dict):
            return
        try:
            values = {}
            for key in ("next_due", "last_completed"):
                raw = data[key]
                value = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
                if raw is not None and (value is None or value.tzinfo is None):
                    raise ValueError
                values[key] = dt_util.as_utc(value) if value else None
            by, missed, done = data["completed_by"], data["missed"], data["done"]
            signature, seed = data["signature"], data["seed"]
            if (
                (by is not None and not isinstance(by, str))
                or type(missed) is not int
                or missed < 0
                or type(done) is not bool
                or not isinstance(signature, list)
                or (seed is not None and not isinstance(seed, str))
            ):
                raise ValueError
        except KeyError, TypeError, ValueError, OverflowError:
            self.errors = {"restore": "invalid_task_snapshot"}
            return
        self.next_due = values["next_due"]
        self.last_completed = values["last_completed"]
        self.completed_by, self.missed, self.task_done = by, missed, done
        self._task_sig, self._task_seed = signature, seed


async def async_actor(hass, context: Context | None) -> str | None:
    """The display name of the user who triggered a context, if any."""
    if context is None or not context.user_id:
        return None
    user = await hass.auth.async_get_user(context.user_id)
    return user.name if user else None
