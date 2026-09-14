"""Opt-in grouped notifications with bounded, stale-checked pending work."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .notifications import async_notify


class NotificationGroups:
    def __init__(self, hass):
        self.hass = hass
        self.pending = {}
        self.last_sent = {}
        self.tasks = set()

    @callback
    def cancel(self, owner):
        for key, batch in tuple(self.pending.items()):
            batch["items"].pop(owner, None)
            if not batch["items"]:
                batch["cancel"]()
                self.pending.pop(key)

    @callback
    def submit(self, owner, config, valid, actions, result):
        policy = config.delivery
        key = (
            ("group", policy["group"]) if policy.get("group") else ("alert", owner),
            config.notifiers,
            config.notify_entities,
            config.title,
            json.dumps(config.data, sort_keys=True),
        )
        now = dt_util.utcnow()
        due = now + timedelta(
            seconds=policy["group_window"] if policy.get("group") else 0
        )
        if key in self.last_sent:
            due = max(
                due, self.last_sent[key] + timedelta(minutes=policy["rate_limit"])
            )
        item = (config, valid, actions, result)
        if batch := self.pending.get(key):
            batch["items"][owner] = item
            # A later member may request a longer minimum interval.
            if key in self.last_sent:
                later = self.last_sent[key] + timedelta(minutes=policy["rate_limit"])
                if later > batch["due"]:
                    batch["cancel"]()
                    batch["due"] = later
                    batch["cancel"] = self._timer(key, later)
            return
        self.pending[key] = {
            "items": {owner: item},
            "due": due,
            "cancel": self._timer(key, due),
        }

    def _timer(self, key, when):
        @callback
        def flush(now):
            batch = self.pending.pop(key, None)
            if batch:
                task = self.hass.async_create_background_task(
                    self._flush(key, batch["items"]), "Modern Alerts grouped delivery"
                )
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

        return async_track_point_in_utc_time(self.hass, flush, when)

    async def _flush(self, key, items):
        members = [item for item in items.values() if item[1]()]
        if not members:
            return
        config = members[0][0]
        lines, rendered, usable = [], [], []
        for item in members:
            try:
                message = (
                    Template(item[0].message, self.hass).async_render(
                        parse_result=False
                    )
                    if item[0].message is not None
                    else item[0].name
                )
            except Exception:
                item[3]({"template": "render_failed"}, "result")
                continue
            rendered.append(message)
            lines.append(f"{item[0].name}: {message}")
            usable.append(item)
        if not usable:
            return
        # Render once and wrap as a literal template to avoid interpreting braces
        # from the rendered text on a second pass through the notify adapter.
        text = "\n".join(lines) if len(usable) > 1 else rendered[0]
        config = replace(config, message="{{ " + json.dumps(text) + " }}")

        def valid():
            return all(item[1]() for item in usable)

        # Suppress a batch if a member changes during provider I/O; never send a
        # stale summary to subsequent providers. Other alerts retry normally.
        self.last_sent[key] = dt_util.utcnow()
        if len(self.last_sent) > 1024:
            self.last_sent.pop(next(iter(self.last_sent)))
        for item in usable:
            item[3]({}, "attempt")
        errors = await async_notify(
            self.hass,
            config,
            valid=valid,
            actions=usable[0][2] if len(usable) == 1 else None,
        )
        for item in usable:
            item[3](errors, "result")

    async def async_stop(self):
        for batch in self.pending.values():
            batch["cancel"]()
        self.pending.clear()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


def groups(hass):
    return hass.data.setdefault("modern_alerts_groups", NotificationGroups(hass))
