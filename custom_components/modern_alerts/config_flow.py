"""Native creation and options workflows, one config entry per alert."""

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, MIN_REPEAT
from .models import AlertConfig, InvalidConfig


class AlertFlowSteps:
    """Shared create/edit forms; every field can be edited without YAML files."""

    _values: dict[str, Any]

    def _schema(self, fields: dict[str, Any], values: dict[str, Any]) -> vol.Schema:
        return self.add_suggested_values_to_schema(vol.Schema(fields), values)

    async def _basics(self, step_id, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                AlertConfig.from_dict(user_input, self.hass)
            except InvalidConfig as err:
                errors[err.field] = err.code
            else:
                self._values.update(user_input)
                return await self.async_step_timing()
        fields = {
            vol.Required("name"): selector.TextSelector(),
            vol.Required("entity_id"): selector.EntitySelector(),
            vol.Required("state", default="on"): selector.TextSelector(),
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema(fields, user_input or self._values),
            errors=errors,
        )

    async def async_step_timing(self, user_input=None):
        errors = {}
        values = {
            "intervals": [{"minutes": n} for n in self._values.get("repeat", [30])],
            "skip_first": self._values.get("skip_first", False),
            "can_acknowledge": self._values.get("can_acknowledge", True),
            "evaluate_on_start": self._values.get("evaluate_on_start", False),
        }
        if user_input is not None:
            values.update(user_input)
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
            vol.Required("skip_first", default=False): selector.BooleanSelector(),
            vol.Required("can_acknowledge", default=True): selector.BooleanSelector(),
            vol.Required(
                "evaluate_on_start", default=False
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(
            step_id="timing", data_schema=self._schema(fields, values), errors=errors
        )

    async def async_step_notifications(self, user_input=None):
        errors = {}
        values = {
            "notifiers": self._values.get("notifiers", []),
            "notify_entities": self._values.get("notify_entities", []),
        }
        if user_input is not None:
            # An omitted optional list means clear it, including during editing.
            values = {"notifiers": [], "notify_entities": [], **user_input}
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
                )
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
        }
        return self.async_show_form(
            step_id="notifications",
            data_schema=self._schema(fields, values),
            errors=errors,
        )

    async def async_step_messages(self, user_input=None):
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
        source = self.hass.states.get(config.entity_id)
        intervals = ", ".join(f"{n:g}" for n in config.repeat)
        targets = [f"notify.{n}" for n in config.notifiers] + list(
            config.notify_entities
        )
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": config.name,
                "entity": config.entity_id,
                "state": config.state,
                "current": source.state if source else "not currently available",
                "schedule": intervals,
                "first": f"after {config.repeat[0]:g} minutes"
                if config.skip_first
                else "immediately",
                "destinations": ", ".join(targets) or "Status only (no notifications)",
                "completion": config.done_message or "No resolution notification",
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
        return self.async_create_entry(title=config.name, data=config.as_dict())

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
