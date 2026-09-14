"""Safe legacy imports and independent configuration drafts."""

import pytest
from homeassistant import config_entries
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.modern_alerts.const import DOMAIN
from custom_components.modern_alerts.importer import parse_alerts


async def test_import_preserves_states_templates_and_payload(hass):
    imported = parse_alerts(
        """alert:
  garage:
    name: Garage
    entity_id: binary_sensor.garage
    state: on
    repeat: [1, 5]
    can_acknowledge: false
    skip_first: true
    notifiers: phone
    message: "{{ states('sensor.test') }}"
    data:
      ttl: 0
      sticky: true
""",
        hass,
    )["garage"]
    assert imported["state"] == "on"
    assert imported["can_acknowledge"] is False
    assert imported["skip_first"] is True
    assert imported["notifiers"] == ["phone"]
    assert imported["data"] == {"ttl": 0, "sticky": True}
    assert imported["message"] == "{{ states('sensor.test') }}"


@pytest.mark.parametrize(
    "text",
    [
        "alert: !include alerts.yaml",
        "alert: !secret alerts",
        "a: &a [*a]",
        "[]",
        "name: X\nentity_id: sensor.x\nrepeat: 1\nbogus: yes",
    ],
)
async def test_import_rejects_unsafe_or_unsupported_input(hass, text):
    with pytest.raises(ValueError):
        parse_alerts(text, hass)


async def test_duplicate_is_draft_with_independent_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Original",
        data={"name": "Original", "entity_id": "sensor.first", "repeat": [10]},
        options={"name": "Edited", "entity_id": "sensor.second", "repeat": [2, 4]},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"setup_action": "duplicate"}
    )
    assert flow["step_id"] == "duplicate"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"source": entry.entry_id}
    )
    assert flow["step_id"] == "user"
    suggestions = {
        key.schema: key.description.get("suggested_value")
        for key in flow["data_schema"].schema
        if key.description
    }
    assert suggestions["name"] == "Edited copy"
    assert suggestions["entity_id"] == "sensor.second"
    assert entry.options["repeat"] == [2, 4]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    hass.config_entries.flow.async_abort(flow["flow_id"])


async def test_import_selection_does_not_create_until_review(hass):
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"setup_action": "import_yaml"}
    )
    assert flow["step_id"] == "import_yaml"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "yaml": "garage:\n  name: Garage\n  entity_id: binary_sensor.garage\n  repeat: 5"
        },
    )
    assert flow["step_id"] == "import_select"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"source": "garage"}
    )
    assert flow["step_id"] == "user"
    assert not hass.config_entries.async_entries(DOMAIN)
    hass.config_entries.flow.async_abort(flow["flow_id"])
