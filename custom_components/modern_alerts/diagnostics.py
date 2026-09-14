"""Bounded metadata only: no configuration, messages, or notification payloads."""

from copy import deepcopy


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    return {
        "kind": runtime.config.kind,
        "active": runtime.firing,
        "acknowledged": runtime.acknowledged,
        "stage_index": runtime.stage_index,
        "history": deepcopy(runtime.history),
        "output_errors": dict(runtime.outputs.errors),
        "notification_errors": dict(runtime.errors),
        "policy_errors": dict(runtime.policy_errors),
    }
