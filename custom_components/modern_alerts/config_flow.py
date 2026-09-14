"""Native creation and options workflows, one config entry per alert."""

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, MIN_REPEAT
from .models import AlertConfig, InvalidConfig
from .output_flow import OutputFlowSteps
from .policy_flow import DeliveryFlowSteps
from .profiles import resolve


class AlertFlowSteps(OutputFlowSteps, DeliveryFlowSteps):
    """Shared create/edit forms; every field can be edited without YAML files."""

    _values: dict[str, Any]

    def _schema(self, fields: dict[str, Any], values: dict[str, Any]) -> vol.Schema:
        return self.add_suggested_values_to_schema(vol.Schema(fields), values)

    async def _basics(self, step_id, user_input=None):
        errors = {}
        numeric_fields = (
            "numeric_below",
            "numeric_above",
            "numeric_recover_above",
            "numeric_recover_below",
            "numeric_unit",
        )
        if user_input is not None:
            # Clearing optional numeric fields switches back to exact state matching.
            values = {key: None for key in numeric_fields} | user_input
            values["kind"] = (
                user_input.get("kind", "alert")
                if step_id == "user"
                else self._values.get("kind", "alert")
            )
            if values["kind"] == "profile":
                values["entity_id"] = "sensor.modern_alerts_profile"
            try:
                AlertConfig.from_dict(values, self.hass)
            except InvalidConfig as err:
                errors[err.field] = err.code
            else:
                self._values.update(values)
                if values["kind"] == "profile":
                    return await self.async_step_notifications()
                return await self.async_step_timing()
        fields = {
            vol.Required("name"): selector.TextSelector(),
            vol.Optional("entity_id"): selector.EntitySelector(),
            vol.Required("state", default="on"): selector.TextSelector(),
        }
        if step_id == "user":
            fields[vol.Required("kind", default="alert")] = selector.SelectSelector(
                {"options": ["alert", "profile"], "translation_key": "entry_kind"}
            )
        for key in numeric_fields[:-1]:
            fields[vol.Optional(key)] = selector.NumberSelector(
                {"step": "any", "mode": "box"}
            )
        fields[vol.Optional("numeric_unit")] = selector.TextSelector()
        if step_id == "init" and self._values.get("kind") == "profile":
            fields = {vol.Required("name"): selector.TextSelector()}
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema(
                fields,
                {
                    key: value
                    for key, value in (user_input or self._values).items()
                    if value is not None
                },
            ),
            errors=errors,
        )

    async def async_step_timing(self, user_input=None):
        errors = {}
        values = {
            "intervals": [{"minutes": n} for n in self._values.get("repeat", [30])],
            "skip_first": self._values.get("skip_first", False),
            "can_acknowledge": self._values.get("can_acknowledge", True),
            "resolution_after_ack": self._values.get("resolution_after_ack", True),
            "evaluate_on_start": self._values.get("evaluate_on_start", False),
            "restore_state": self._values.get("restore_state", False),
            "snooze_minutes": self._values.get("snooze_minutes", 30),
            "activation_delay": self._values.get("activation_delay", 0),
            "recovery_delay": self._values.get("recovery_delay", 0),
            "unavailable_policy": self._values.get("unavailable_policy", "resolve"),
            "enable_snooze": self._values.get("enable_snooze", True),
            "action_buttons": self._values.get("action_buttons", False),
        }
        if user_input is not None:
            values.update(user_input)
            self._configure_delivery = user_input.get("configure_delivery", False)
            values.pop("configure_delivery", None)
            try:
                repeat = [row["minutes"] for row in values["intervals"]]
                updated = {k: v for k, v in values.items() if k != "intervals"}
                updated["repeat"] = repeat
                AlertConfig.from_dict({**self._values, **updated}, self.hass)
            except KeyError, TypeError:
                errors["intervals"] = "invalid_repeat"
            except InvalidConfig as err:
                errors["intervals" if err.field == "repeat" else err.field] = err.code
            else:
                self._values.update(updated)
                return await self.async_step_notifications()
        interval_selector = selector.ObjectSelector(
            {
                "multiple": True,
                "label_field": "minutes",
                "fields": {
                    "minutes": {
                        "label": "Minutes",
                        "required": True,
                        "selector": selector.NumberSelector(
                            {
                                "min": MIN_REPEAT,
                                "step": "any",
                                "mode": "box",
                            }
                        ),
                    }
                },
            }
        )
        fields = {
            vol.Required("intervals"): interval_selector,
            vol.Required(
                "configure_delivery", default=False
            ): selector.BooleanSelector(),
            vol.Required("skip_first", default=False): selector.BooleanSelector(),
            vol.Required("can_acknowledge", default=True): selector.BooleanSelector(),
            vol.Required(
                "resolution_after_ack", default=True
            ): selector.BooleanSelector(),
            vol.Required(
                "evaluate_on_start", default=False
            ): selector.BooleanSelector(),
            vol.Required("activation_delay", default=0): selector.NumberSelector(
                {"min": 0, "step": "any", "unit_of_measurement": "seconds"}
            ),
            vol.Required("recovery_delay", default=0): selector.NumberSelector(
                {"min": 0, "step": "any", "unit_of_measurement": "seconds"}
            ),
            vol.Required(
                "unavailable_policy", default="resolve"
            ): selector.SelectSelector({"options": ["resolve", "suspend"]}),
            vol.Required("enable_snooze", default=True): selector.BooleanSelector(),
            vol.Required("restore_state", default=False): selector.BooleanSelector(),
            vol.Required("snooze_minutes", default=30): selector.NumberSelector(
                {
                    "min": 0.016,
                    "max": 10080,
                    "step": "any",
                    "mode": "box",
                    "unit_of_measurement": "minutes",
                }
            ),
            vol.Required("action_buttons", default=False): selector.BooleanSelector(),
        }
        return self.async_show_form(
            step_id="timing", data_schema=self._schema(fields, values), errors=errors
        )

    async def async_step_notifications(self, user_input=None):
        errors = {}
        values = {
            "notifiers": self._values.get("notifiers", []),
            "notify_entities": self._values.get("notify_entities", []),
            "configure_outputs": bool(self._values.get("outputs")),
            "profile_ids": self._values.get("profile_ids", []),
        }
        if user_input is not None:
            # An omitted optional list means clear it, including during editing.
            values = {
                "notifiers": [],
                "notify_entities": [],
                "profile_ids": [],
                **user_input,
            }
            try:
                config = AlertConfig.from_dict(
                    {**self._values, **values, "data": {}}, self.hass
                )
            except InvalidConfig as err:
                errors[err.field] = err.code
            else:
                self._values.update(
                    notifiers=list(config.notifiers),
                    notify_entities=list(config.notify_entities),
                    profile_ids=list(config.profile_ids),
                )
                if user_input.get("configure_outputs"):
                    return await self.async_step_outputs()
                return await self.async_step_messages()
        services = self.hass.services.async_services().get("notify", {})
        choices = sorted(
            (set(services) | set(values["notifiers"])) - {"notify", "send_message"}
        )
        fields = {
            vol.Optional("notifiers"): selector.SelectSelector(
                {
                    "options": choices,
                    "multiple": True,
                    "custom_value": True,
                    "mode": "dropdown",
                }
            ),
            vol.Optional("notify_entities"): selector.EntitySelector(
                {"filter": {"domain": "notify"}, "multiple": True}
            ),
            vol.Required(
                "configure_outputs", default=False
            ): selector.BooleanSelector(),
        }
        if self._values.get("kind") != "profile":
            profiles = {
                entry.entry_id: entry.title
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if (entry.options or entry.data).get("kind") == "profile"
            }
            for key in self._values.get("profile_ids", []):
                profiles.setdefault(key, "Unavailable profile")
            fields[vol.Optional("profile_ids")] = selector.SelectSelector(
                {
                    "options": [
                        {"value": key, "label": name} for key, name in profiles.items()
                    ],
                    "multiple": True,
                }
            )
        return self.async_show_form(
            step_id="notifications",
            data_schema=self._schema(fields, values),
            errors=errors,
        )

    async def async_step_messages(self, user_input=None):
        if self._values.get("kind") == "profile":
            return await self.async_step_profile_review()
        errors = {}
        values = {
            k: self._values.get(k) for k in ("message", "title", "done_message", "data")
        }
        if user_input is not None:
            values = {
                "message": None,
                "title": None,
                "done_message": None,
                "data": {},
                **user_input,
            }
            try:
                config = AlertConfig.from_dict({**self._values, **values}, self.hass)
            except InvalidConfig as err:
                errors[err.field] = err.code
            else:
                self._values = config.as_dict()
                if getattr(self, "_configure_delivery", False):
                    return await self.async_step_delivery()
                return await self.async_step_review()
        fields = {
            vol.Optional("message"): selector.TemplateSelector(),
            vol.Optional("title"): selector.TemplateSelector(),
            vol.Optional("done_message"): selector.TemplateSelector(),
            vol.Optional("data"): selector.ObjectSelector(),
        }
        return self.async_show_form(
            step_id="messages",
            data_schema=self._schema(
                fields, {k: v for k, v in values.items() if v is not None}
            ),
            errors=errors,
        )

    async def async_step_review(self, user_input=None):
        config = AlertConfig.from_dict(self._values, self.hass)
        if user_input is not None:
            return self._finish(config)
        effective, missing = resolve(self.hass, config)
        source = self.hass.states.get(config.entity_id)
        intervals = ", ".join(f"{n:g}" for n in config.repeat)
        targets = [f"notify.{n}" for n in effective.notifiers] + list(
            effective.notify_entities
        )
        targets.extend(f"{item['name']} ({item['type']})" for item in effective.outputs)
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": config.name,
                "entity": config.entity_id,
                "state": (
                    f"below {config.numeric_below:g}"
                    if config.numeric_below is not None
                    else f"above {config.numeric_above:g}"
                    if config.numeric_above is not None
                    else config.state
                ),
                "current": f"{source.state} {source.attributes.get('unit_of_measurement', '')}".strip()
                if source
                else "not currently available",
                "reliability": f"Activation/recovery delays: {config.activation_delay:g}/{config.recovery_delay:g} seconds. Restore state: {config.restore_state}. Unavailable source: {config.unavailable_policy}. Default snooze: {config.snooze_minutes:g} minutes. Phone controls: {config.action_buttons}.",
                "recovery": (
                    f"above {config.numeric_recover_above:g}"
                    if config.numeric_recover_above is not None
                    else f"below {config.numeric_recover_below:g}"
                    if config.numeric_recover_below is not None
                    else "when the problem condition clears"
                ),
                "schedule": intervals,
                "first": f"after {config.repeat[0]:g} minutes"
                if config.skip_first
                else "immediately",
                "destinations": ", ".join(targets) or "Status only (no notifications)",
                "completion": config.done_message or "No resolution notification",
                "delivery_summary": "Stages: "
                + (
                    ", ".join(
                        f"{stage['name']} at {stage['after']:g} minutes"
                        for stage in config.stages
                    )
                    or "none"
                )
                + f". Profiles: {len(config.profile_ids)} ({len(missing)} unavailable). Group: {config.delivery.get('group') or 'none'}. Minimum notification interval: {config.delivery.get('rate_limit', 0):g} minutes. History: {config.history_limit} entries.",
            },
        )


class ModernAlertsConfigFlow(AlertFlowSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Create an alert through Devices & services."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if not hasattr(self, "_values"):
            self._values = {}
        return await self._basics("user", user_input)

    @callback
    def _finish(self, config):
        return self.async_create_entry(
            title=f"Profile: {config.name}"
            if config.kind == "profile"
            else config.name,
            data=config.as_dict(),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ModernAlertsOptionsFlow()


class ModernAlertsOptionsFlow(AlertFlowSteps, config_entries.OptionsFlow):
    """Edit an alert while preserving its registered identity."""

    async def async_step_init(self, user_input=None):
        if not hasattr(self, "_values"):
            self._values = dict(self.config_entry.options or self.config_entry.data)
        return await self._basics("init", user_input)

    @callback
    def _finish(self, config):
        return self.async_create_entry(title="", data=config.as_dict())
