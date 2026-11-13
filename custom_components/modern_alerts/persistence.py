"""Incident snapshots: what is saved, and what is trusted when read back."""

from copy import deepcopy
from typing import Any

from homeassistant.util import dt as dt_util


class PersistenceMixin:
    """Serialization for AlertRuntime. Deadlines are saved, not durations."""

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
        data["task"] = self.task_snapshot()
        data["outputs_seen"] = sorted(self.outputs.seen)
        data["started_at"] = self.started_at.isoformat() if self.started_at else None
        data["stage_index"] = self.stage_index
        data["history"] = deepcopy(self.history)
        data["pending_notification"] = self._pending_notification
        data["pending_outputs"] = sorted(self._pending_outputs)
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
        """Reject incomplete/old snapshots instead of restoring half an incident.

        A task's schedule is restored separately and unconditionally: losing
        the next due date on restart would silently reset the task.
        """
        self.task_restore(data.get("task"))
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
            stage = data.get("stage_index", 0)
            if type(stage) is not int or not 0 <= stage <= len(self.config.stages):
                raise ValueError
            values["stage_index"] = stage
            start = data.get("started_at")
            started = dt_util.parse_datetime(start) if isinstance(start, str) else None
            if start is not None and (started is None or started.tzinfo is None):
                raise ValueError
            values["started_at"] = started or (
                dt_util.utcnow() if values["firing"] else None
            )
            history = data.get("history", [])
            if (
                not isinstance(history, list)
                or len(history) > 100
                or any(
                    not isinstance(item, dict)
                    or not set(item)
                    <= {
                        "at",
                        "incident_id",
                        "event",
                        "output_id",
                        "entity_id",
                        "error",
                        "errors",
                        "stage",
                        "count",
                        "acknowledged",
                        "via",
                        "by",
                    }
                    for item in history
                )
            ):
                raise ValueError
            values["history"] = (
                deepcopy(history[-self.config.history_limit :])
                if self.config.history_limit
                else []
            )
            pending_notification = data.get("pending_notification", False)
            pending_outputs = data.get("pending_outputs", [])
            if (
                type(pending_notification) is not bool
                or not isinstance(pending_outputs, list)
                or any(not isinstance(key, str) for key in pending_outputs)
            ):
                raise ValueError
            values["_pending_notification"] = pending_notification
            values["_pending_outputs"] = set(pending_outputs)
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
            seen = data.get("outputs_seen", [])
            if not isinstance(seen, list) or any(
                not isinstance(key, str) for key in seen
            ):
                raise ValueError
        except KeyError, TypeError, ValueError, OverflowError:
            self.errors = {"restore": "invalid_snapshot"}
            return
        for key, value in values.items():
            setattr(self, key, value)
        self._restored = True
        self.outputs.seen = set(seen)

    async def async_save(self, snapshot: dict[str, Any] | None = None) -> None:
        if self.store:
            if self.config.restore_state or self.is_task:
                await self.store.async_save(
                    snapshot if snapshot is not None else self.snapshot()
                )
            else:
                await self.store.async_remove()
