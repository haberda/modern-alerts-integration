"""Delivery and escalation forms using standard HA selectors."""

import voluptuous as vol
from homeassistant.helpers import selector

from .models import AlertConfig, InvalidConfig
from .policies import WEEKDAYS
from .profiles import resolve

POLICY_FIELDS = (
    "quiet_start",
    "quiet_end",
    "presence_entities",
    "presence_mode",
    "weekly_windows",
)


def routing_fields():
    return {
        vol.Optional("weekly_windows"): selector.ObjectSelector(
            {
                "multiple": True,
                "fields": {
                    "days": {
                        "label": "Starting weekdays",
                        "required": True,
                        "selector": selector.SelectSelector(
                            {"options": list(WEEKDAYS), "multiple": True}
                        ),
                    },
                    "start": {
                        "label": "Delivery window start",
                        "required": True,
                        "selector": selector.TimeSelector(),
                    },
                    "end": {
                        "label": "Delivery window end",
                        "required": True,
                        "selector": selector.TimeSelector(),
                    },
                },
            }
        ),
        vol.Optional("quiet_start"): selector.TimeSelector(),
        vol.Optional("quiet_end"): selector.TimeSelector(),
        vol.Optional("presence_entities"): selector.EntitySelector(
            {
                "multiple": True,
                "filter": {
                    "domain": [
                        "person",
                        "device_tracker",
                        "binary_sensor",
                        "input_boolean",
                    ]
                },
            }
        ),
        vol.Required("presence_mode", default="any_home"): selector.SelectSelector(
            {"options": ["any_home", "all_away"], "translation_key": "presence_mode"}
        ),
    }


class DeliveryFlowSteps:
    async def async_step_delivery(self, user_input=None):
        errors = {}
        values = {
            **self._values.get("delivery", {}),
            "stages": self._values.get("stages", []),
            "history_limit": self._values.get("history_limit", 50),
        }
        if user_input is not None:
            values = user_input
            try:
                updated = {
                    **self._values,
                    "delivery": {
                        key: value
                        for key, value in user_input.items()
                        if key not in ("stages", "history_limit")
                    },
                    "stages": user_input.get("stages", []),
                    "history_limit": int(user_input.get("history_limit", 50)),
                }
                config = AlertConfig.from_dict(updated, self.hass)
            except InvalidConfig as err:
                errors[
                    err.field
                    if err.field
                    in (
                        *POLICY_FIELDS,
                        "group",
                        "group_window",
                        "rate_limit",
                        "history_limit",
                    )
                    else "stages"
                ] = err.code
            else:
                self._values = config.as_dict()
                return await self.async_step_review()
        config = AlertConfig.from_dict(self._values, self.hass)
        effective, missing = resolve(self.hass, config)
        outputs = {item["id"]: item["name"] for item in effective.outputs}
        for stage in config.stages:
            for key in stage["output_ids"]:
                outputs.setdefault(key, f"Missing output ({key})")
        stage_selector = selector.ObjectSelector(
            {
                "multiple": True,
                "label_field": "name",
                "fields": {
                    "name": {
                        "label": "Stage name",
                        "required": True,
                        "selector": selector.TextSelector(),
                    },
                    "after": {
                        "label": "Minutes after incident starts",
                        "required": True,
                        "selector": selector.NumberSelector(
                            {"min": 0.016, "max": 10080, "step": "any", "mode": "box"}
                        ),
                    },
                    "interval": {
                        "label": "New repeat interval (minutes, optional)",
                        "selector": selector.NumberSelector(
                            {"min": 0.016, "max": 10080, "step": "any", "mode": "box"}
                        ),
                    },
                    "notifiers": {
                        "label": "Additional legacy notify actions",
                        "selector": selector.SelectSelector(
                            {
                                "options": sorted(
                                    set(
                                        self.hass.services.async_services().get(
                                            "notify", {}
                                        )
                                    )
                                    - {"notify", "send_message"}
                                ),
                                "multiple": True,
                                "custom_value": True,
                            }
                        ),
                    },
                    "notify_entities": {
                        "label": "Additional notify entities",
                        "selector": selector.EntitySelector(
                            {"filter": {"domain": "notify"}, "multiple": True}
                        ),
                    },
                    "output_ids": {
                        "label": "Outputs enabled at this stage",
                        "selector": selector.SelectSelector(
                            {
                                "options": [
                                    {"value": key, "label": name}
                                    for key, name in outputs.items()
                                ],
                                "multiple": True,
                            }
                        ),
                    },
                },
            }
        )
        fields = {
            **routing_fields(),
            vol.Optional("group"): selector.TextSelector(),
            vol.Required("group_window", default=30): selector.NumberSelector(
                {
                    "min": 1,
                    "max": 300,
                    "step": "any",
                    "mode": "box",
                    "unit_of_measurement": "seconds",
                }
            ),
            vol.Required("rate_limit", default=0): selector.NumberSelector(
                {
                    "min": 0,
                    "max": 10080,
                    "step": "any",
                    "mode": "box",
                    "unit_of_measurement": "minutes",
                }
            ),
            vol.Required("history_limit", default=50): selector.NumberSelector(
                {"min": 0, "max": 100, "step": 1, "mode": "box"}
            ),
            vol.Optional("stages"): stage_selector,
        }
        return self.async_show_form(
            step_id="delivery",
            data_schema=self._schema(
                fields,
                {key: value for key, value in values.items() if value is not None},
            ),
            errors=errors,
        )

    async def async_step_profile_review(self, user_input=None):
        config = AlertConfig.from_dict(self._values, self.hass)
        if user_input is not None:
            return self._finish(config)
        return self.async_show_form(
            step_id="profile_review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": config.name,
                "destinations": ", ".join(config.notifiers + config.notify_entities)
                or "None",
                "outputs": ", ".join(item["name"] for item in config.outputs) or "None",
            },
        )
