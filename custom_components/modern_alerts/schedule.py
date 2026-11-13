"""Recurring-task schedule maths: pure functions over validated configuration.

All wall-clock reasoning happens in Home Assistant's timezone, so a 17:00
task stays at 17:00 local time across daylight-saving transitions. Results
are UTC datetimes.
"""

import re
from datetime import datetime, time, timedelta
from itertools import islice

from dateutil.rrule import rrulestr
from homeassistant.util import dt as dt_util

TASK_ENTITY = "sensor.modern_alerts_task"
MODES = ("after_completion", "fixed", "once")
DEFAULT_TIME = "12:00:00"
# Without a first-due date, recurrence counting starts from a fixed Monday so
# that "every second week" does not drift when Home Assistant restarts.
ANCHOR = datetime(2024, 1, 1)
_FREQ = re.compile(r"(?:^|;)FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)(?:;|$)")
MAX_MISSED_SCAN = 400


def parse_local(text: str | None) -> datetime | None:
    """Parse a configured datetime; naive values are local wall-clock time."""
    if not isinstance(text, str) or not text.strip():
        return None
    value = dt_util.parse_datetime(text.strip())
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


def local_text(value: datetime) -> str:
    """Store datetimes the way Home Assistant's datetime selector presents them."""
    return dt_util.as_local(value).strftime("%Y-%m-%d %H:%M:%S")


def _naive_local(value: datetime) -> datetime:
    return dt_util.as_local(value).replace(tzinfo=None)


def _from_naive(value: datetime) -> datetime:
    return dt_util.as_utc(value.replace(tzinfo=dt_util.get_default_time_zone()))


def _time(config) -> time:
    return dt_util.parse_time(config.task_time or DEFAULT_TIME) or time(12)


def normalize_rrule(text: str) -> str:
    """Validate a single iCalendar RRULE line; raise ValueError when unusable."""
    if not isinstance(text, str):
        raise ValueError("RRULE must be text")
    rule = text.strip().upper().removeprefix("RRULE:")
    if not rule or "\n" in rule or ":" in rule or len(rule) > 500:
        raise ValueError("Expected one RRULE line")
    if not _FREQ.search(rule):
        raise ValueError("Use a daily, weekly, monthly, or yearly rule")
    probe = rrulestr(rule, dtstart=ANCHOR)
    if next(iter(probe), None) is None:
        raise ValueError("The rule has no occurrences")
    return rule


def _rule(config):
    first = parse_local(config.task_first_due)
    start = (
        _naive_local(first)
        if first is not None
        else datetime.combine(ANCHOR.date(), _time(config))
    )
    return rrulestr(config.task_rrule, dtstart=start)


def occurrence_after(
    config, moment: datetime, *, inclusive: bool = False
) -> datetime | None:
    """The first fixed-schedule occurrence after a moment, or None if exhausted."""
    found = _rule(config).after(_naive_local(moment), inc=inclusive)
    return _from_naive(found) if found is not None else None


def window(config) -> timedelta | None:
    if config.task_mode == "fixed" and config.task_window:
        return timedelta(minutes=config.task_window)
    return None


def open_or_next(config, now: datetime) -> datetime | None:
    """The occurrence whose window is open now, else the next one to start."""
    length = window(config)
    if length is None:
        return occurrence_after(config, now, inclusive=True)
    return occurrence_after(config, now - length)


def missed_between(config, due: datetime, now: datetime) -> tuple[int, datetime | None]:
    """Count windows that closed unanswered from `due`, and where to resume."""
    length = window(config)
    if length is None:
        return 0, due
    rule = _rule(config)
    count = 0
    current = due
    for found in islice(rule.xafter(_naive_local(due), inc=True), MAX_MISSED_SCAN):
        current = _from_naive(found)
        if current + length > now:
            return count, current
        count += 1
    # A very long outage: stop counting and jump straight to the present.
    return count, open_or_next(config, now)


def due_after_completion(config, completed: datetime) -> datetime:
    """Next due for an interval task.

    Intervals of a day or longer land on the configured time of day, so a
    180-day task is due at noon rather than at the minute it was completed.
    """
    interval = timedelta(minutes=config.task_interval)
    if config.task_interval < 1440:
        return dt_util.as_utc(completed) + interval
    local = dt_util.as_local(completed)
    day = (local.replace(tzinfo=None) + interval).date()
    return _from_naive(datetime.combine(day, _time(config)))


def initial_due(config, now: datetime, last_completed: datetime | None):
    """Seed the schedule for a new task or after its rule was edited."""
    first = parse_local(config.task_first_due)
    if config.task_mode == "once":
        return first
    if config.task_mode == "after_completion":
        if last_completed is not None:
            return due_after_completion(config, last_completed)
        return first or due_after_completion(config, now)
    return open_or_next(config, now)


def days_remaining(due: datetime | None, now: datetime) -> int | None:
    """Whole local calendar days until due; negative once overdue."""
    if due is None:
        return None
    return (dt_util.as_local(due).date() - dt_util.as_local(now).date()).days
