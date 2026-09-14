"""Read-only snapshots of current routing, timing, and notification templates."""

from copy import deepcopy

from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .policies import reasons
from .profiles import resolve


def explain(runtime, *, render=False):
    """Describe opportunities, never promise a future delivery or invoke providers."""
    config = runtime.delivery_config()
    now = dt_util.utcnow()
    blockers = []
    if runtime._stopped:
        blockers.append("not_running")
    if not runtime.firing:
        blockers.append("condition_inactive")
    if runtime.acknowledged:
        blockers.append("acknowledged")
    if runtime.snoozed_until:
        blockers.append("snoozed")
    if runtime.source_suspended:
        blockers.append("source_unavailable")
    hass_groups = runtime.hass.data.get("modern_alerts_groups")
    pending = (
        [
            batch["due"]
            for batch in hass_groups.pending.values()
            if (runtime.entry_id or str(id(runtime))) in batch["items"]
        ]
        if hass_groups
        else []
    )
    all_config, missing = resolve(runtime.hass, runtime.config)
    enabled = {item["id"] for item in config.outputs}
    output_rows = []
    for item in all_config.outputs:
        held = blockers + reasons(runtime.hass, item.get("delivery", {}), now)
        if item["id"] not in enabled:
            held.append("waiting_for_escalation")
        if not item.get("repeat", True) and item["id"] in runtime.outputs.seen:
            held.append("already_run_this_incident")
        if item["id"] in runtime.outputs.active:
            held.append("already_running")
        output_rows.append(
            {
                "id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "blockers": held,
            }
        )
    notification_blockers = blockers + reasons(runtime.hass, config.delivery, now)
    if pending:
        notification_blockers.append("group_or_rate_delay")
    if not config.notifiers and not config.notify_entities:
        notification_blockers.append("no_notification_destinations")
    result = {
        "name": config.name,
        "entity_id": runtime.status_entity_id,
        "active": runtime.firing,
        "can_acknowledge": config.can_acknowledge,
        "enable_snooze": config.enable_snooze,
        "acknowledged": runtime.acknowledged,
        "snoozed_until": runtime.snoozed_until,
        "source_suspended": runtime.source_suspended,
        "incident_started": runtime.started_at,
        "pending_transition": runtime.pending_active,
        "pending_deadline": runtime.pending_due,
        "next_repeat_opportunity": runtime.next_notification
        if runtime.firing and not runtime.acknowledged
        else None,
        "next_escalation_opportunity": runtime._stage_deadline()
        if runtime.firing
        else None,
        "buffered_notification_due": min(pending) if pending else None,
        "notification_held": runtime._pending_notification,
        "notification_blockers": notification_blockers,
        "destinations": [f"notify.{name}" for name in config.notifiers]
        + list(config.notify_entities),
        "outputs": output_rows,
        "escalation_stage": config.stages[runtime.stage_index - 1]["name"]
        if runtime.stage_index
        else "Initial",
        "missing_profiles": len(missing),
        "notification_errors": deepcopy(runtime.errors),
        "output_errors": deepcopy(runtime.outputs.errors),
        "policy_errors": {
            **({"profiles": "unavailable"} if missing else {}),
            **(
                {"stages": "missing_outputs"}
                if {key for stage in all_config.stages for key in stage["output_ids"]}
                - {item["id"] for item in all_config.outputs}
                else {}
            ),
            **(
                {"notify_entities": "extra_data_unsupported"}
                if all_config.data
                and (
                    all_config.notify_entities
                    or any(
                        stage["notify_entities"]
                        for stage in all_config.stages[: runtime.stage_index]
                    )
                )
                else {}
            ),
        },
        "history": deepcopy(runtime.history),
    }
    if render:
        result["preview"] = {}
        for key, source in (
            ("message", config.message if config.message is not None else config.name),
            ("title", config.title),
            ("resolution", config.done_message),
        ):
            if key == "message" and config.message is None:
                result["preview"][key] = config.name
                continue
            try:
                result["preview"][key] = (
                    Template(source, runtime.hass).async_render(parse_result=False)
                    if source is not None
                    else None
                )
            except Exception:
                result["preview"][key] = "Template could not be rendered"
    return result
