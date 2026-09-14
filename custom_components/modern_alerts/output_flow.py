"""Native output creation, editing and removal within either alert flow."""

from copy import deepcopy
from uuid import uuid4

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.script import async_validate_actions_config

from .output_config import OUTPUT_TYPES, InvalidOutput, validate_output
from .policy_flow import POLICY_FIELDS, routing_fields


def number(minimum, maximum, unit=None):
    config = {"min": minimum, "max": maximum, "step": "any", "mode": "box"}
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class OutputFlowSteps:
    """Embedded forms, with no external automation/script required."""

    async def async_step_outputs(self, user_input=None):
        outputs = self._values.get("outputs", [])
        choices = ["output_add"] if len(outputs) < 20 else []
        if outputs:
            choices.extend(("output_edit", "output_remove"))
        choices.append("messages")
        return self.async_show_menu(
            step_id="outputs",
            menu_options=choices,
            description_placeholders={
                "outputs": ", ".join(item["name"] for item in outputs) or "None"
            },
        )

    async def async_step_output_add(self, user_input=None):
        if user_input is not None:
            self._output_draft = {
                "id": uuid4().hex,
                "type": user_input["type"],
                "name": user_input["type"].title(),
            }
            return await self.async_step_output_settings()
        return self.async_show_form(
            step_id="output_add",
            data_schema=vol.Schema(
                {
                    vol.Required("type"): selector.SelectSelector(
                        {
                            "options": list(OUTPUT_TYPES),
                            "translation_key": "output_type",
                        }
                    )
                }
            ),
        )

    def _output_choices(self):
        return selector.SelectSelector(
            {
                "options": [
                    {
                        "value": item["id"],
                        "label": f"{index}. {item['name']} ({item['type']})",
                    }
                    for index, item in enumerate(self._values.get("outputs", []), 1)
                ]
            }
        )

    async def async_step_output_edit(self, user_input=None):
        if user_input is not None:
            self._output_draft = deepcopy(
                next(
                    item
                    for item in self._values["outputs"]
                    if item["id"] == user_input["output_id"]
                )
            )
            return await self.async_step_output_settings()
        return self.async_show_form(
            step_id="output_edit",
            data_schema=vol.Schema({vol.Required("output_id"): self._output_choices()}),
        )

    async def async_step_output_remove(self, user_input=None):
        if user_input is not None:
            self._values["outputs"] = [
                item
                for item in self._values["outputs"]
                if item["id"] != user_input["output_id"]
            ]
            return await self.async_step_outputs()
        return self.async_show_form(
            step_id="output_remove",
            data_schema=vol.Schema({vol.Required("output_id"): self._output_choices()}),
        )

    async def async_step_output_settings(self, user_input=None):
        draft = self._output_draft
        kind = draft["type"]
        errors = {}
        values = {**draft, **draft.get("delivery", {})}
        if user_input is not None:
            # Optional fields omitted on edit are intentionally cleared.
            values = {"id": draft["id"], "type": kind, **user_input}
            values["delivery"] = {
                key: user_input[key] for key in POLICY_FIELDS if key in user_input
            }
            for key in POLICY_FIELDS:
                values.pop(key, None)
            try:
                output = validate_output(values, self.hass)
                if kind == "custom":
                    for key in ("actions", "stop_actions", "recovery_actions"):
                        try:
                            await async_validate_actions_config(
                                self.hass, cv.SCRIPT_SCHEMA(deepcopy(output[key]))
                            )
                        except (vol.Invalid, HomeAssistantError, ValueError) as err:
                            raise InvalidOutput(key, "invalid_actions") from err
            except InvalidOutput as err:
                errors[err.field] = err.code
            else:
                outputs = [deepcopy(item) for item in self._values.get("outputs", [])]
                for index, item in enumerate(outputs):
                    if item["id"] == output["id"]:
                        outputs[index] = output
                        break
                else:
                    outputs.append(output)
                self._values["outputs"] = outputs
                return await self.async_step_outputs()
        fields = {
            vol.Required("name"): selector.TextSelector(),
            vol.Required("repeat", default=True): selector.BooleanSelector(),
            vol.Required("duration", default=10 if kind == "light" else 30): number(
                0.1, 300, "seconds"
            ),
        }
        if kind != "custom":
            domain = {
                "light": "light",
                "siren": "siren",
                "tts": "media_player",
                "audio": "media_player",
            }[kind]
            fields[vol.Required("entities")] = selector.EntitySelector(
                {"filter": {"domain": domain}, "multiple": True}
            )
        if kind == "light":
            fields.update(
                {
                    vol.Required("pattern", default="blink"): selector.SelectSelector(
                        {
                            "options": ["blink", "steady"],
                            "translation_key": "light_pattern",
                        }
                    ),
                    vol.Required("interval", default=1): number(0.5, 60, "seconds"),
                    vol.Optional("color"): selector.ColorRGBSelector(),
                    vol.Optional("brightness"): number(1, 100, "%"),
                    vol.Required("restore", default=True): selector.BooleanSelector(),
                }
            )
        if kind in ("tts", "audio", "siren"):
            fields[vol.Optional("volume")] = number(0, 1)
        if kind == "siren":
            fields[vol.Optional("tone")] = selector.TextSelector()
        if kind in ("tts", "audio"):
            fields[vol.Required("restore", default=True)] = selector.BooleanSelector()
        if kind == "tts":
            fields.update(
                {
                    vol.Required("tts_entity"): selector.EntitySelector(
                        {"filter": {"domain": "tts"}}
                    ),
                    vol.Optional("message"): selector.TemplateSelector(),
                    vol.Optional("recovery_message"): selector.TemplateSelector(),
                    vol.Optional("language"): selector.TextSelector(),
                }
            )
        if kind == "audio":
            fields.update(
                {
                    vol.Required("media_url"): selector.TextSelector(),
                    vol.Optional("media_type"): selector.TextSelector(),
                }
            )
        if kind == "custom":
            fields.update(
                {
                    vol.Required("actions"): selector.ActionSelector(),
                    vol.Optional("stop_actions"): selector.ActionSelector(),
                    vol.Optional("recovery_actions"): selector.ActionSelector(),
                }
            )
        fields.update(routing_fields())
        return self.async_show_form(
            step_id="output_settings",
            data_schema=self._schema(
                fields,
                {key: value for key, value in values.items() if value is not None},
            ),
            errors=errors,
            description_placeholders={"type": kind},
        )
