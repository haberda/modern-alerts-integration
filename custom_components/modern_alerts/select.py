"""Select one configured output for an isolated device test."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError

from . import ModernAlertsEntry
from .entity import ModernAlertEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry: ModernAlertsEntry, async_add_entities):
    async_add_entities([OutputSelection(entry)])


class OutputSelection(ModernAlertEntity, SelectEntity):
    """Numbered output names distinguish duplicates without exposing IDs."""

    def __init__(self, entry):
        super().__init__(entry, "test_output")
        self._attr_icon = "mdi:test-tube"

    @property
    def options(self):
        return [
            f"{index}. {item['name']}"
            for index, item in enumerate(self.runtime.config.outputs, 1)
        ]

    @property
    def available(self):
        return bool(self.options)

    @property
    def current_option(self):
        for item, label in zip(self.runtime.config.outputs, self.options, strict=True):
            if item["id"] == self.runtime.test_output_id:
                return label
        return self.options[0] if self.options else None

    async def async_select_option(self, option):
        if option not in self.options:
            raise ServiceValidationError("Select a configured output")
        self.runtime.test_output_id = self.runtime.config.outputs[
            self.options.index(option)
        ]["id"]
        self.runtime._publish()
