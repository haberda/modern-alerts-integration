"""Bounded metadata only: no configuration, messages, or notification payloads."""

from copy import deepcopy

from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {"by", "completed_by"}


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    data = {
        "kind": runtime.config.kind,
        "source": runtime.config.source,
        "active": runtime.firing,
        "acknowledged": runtime.acknowledged,
        "stage_index": runtime.stage_index,
        "history": deepcopy(runtime.history),
        "output_errors": dict(runtime.outputs.errors),
        "notification_errors": dict(runtime.errors),
        "policy_errors": dict(runtime.policy_errors),
    }
    if runtime.is_task:
        data["task"] = {
            "mode": runtime.config.task_mode,
            "next_due": runtime.next_due,
            "last_completed": runtime.last_completed,
            "completed_by": runtime.completed_by,
            "missed": runtime.missed,
            "done": runtime.task_done,
            "tags": len(runtime.config.task_tags),
            "legacy_actions": len(runtime.config.task_legacy_actions),
        }
    return async_redact_data(data, TO_REDACT)
