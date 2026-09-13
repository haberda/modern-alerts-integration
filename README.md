# Modern Alerts

A custom Home Assistant integration that reproduces the built-in Alert lifecycle with setup and editing through **Settings → Devices & services**. Each alert has a status sensor, a problem binary sensor, and buttons to acknowledge, resume, and test notifications.

**Version 0.1.0 targets Home Assistant 2026.9.2 (Python 3.14).** The test suite runs against that exact release. Earlier versions are not supported; newer releases require compatibility testing. This integration uses its own `modern_alerts` actions and standard entities, so existing `alert.*` references need migration.

## Install

1. Copy the repository's **`custom_components/modern_alerts` directory**, including its translations, into your Home Assistant configuration directory:

   ```text
   <config>/custom_components/modern_alerts/__init__.py
   <config>/custom_components/modern_alerts/manifest.json
   <config>/custom_components/modern_alerts/...
   ```

2. Restart Home Assistant to discover the custom integration.
3. Set up your notification provider if you want notifications. State-only alerts are also supported.
4. Open **Settings → Devices & services → Add integration → Modern Alerts**.

This is a custom integration, not an automation blueprint. After installation, creating and editing alerts requires no configuration-file changes. No HACS listing or automatic installation is assumed.

## Create an alert

The setup wizard walks through five steps:

1. **Condition:** name, source entity, and the exact problem state. Use `on` for a typical door binary sensor. Match the underlying state string, not a translated label such as “Open.” For thresholds or multiple conditions, first create a Template binary sensor helper and select it here.
2. **Timing:** add one interval for fixed repetition or several intervals in order. Values are minutes and may be fractional (minimum `0.016`). The final interval repeats indefinitely. Choose whether to delay the first notification and whether acknowledgement is allowed.
3. **Destinations:** choose legacy notify actions, notify entities, or leave both lists empty for a state-only alert. Missing legacy action names can be entered manually.
4. **Content:** optional message, title, resolution message, and provider data. Text fields support Home Assistant templates. An empty message uses the alert name; an empty resolution message disables completion notifications. The alert name is literal text. Extra data is an optional YAML mapping entered within the UI and is forwarded unchanged.
5. **Review:** inspect the source's current state, schedule, and destinations before saving.

Saving never sends a test notification. The optional **Evaluate the existing condition on startup** setting can send a real alert immediately if the source already matches when the integration loads. It defaults off for compatibility with built-in Alert.

For example, a garage-door alert can watch `binary_sensor.garage_door` for `on`, use intervals `15`, `30`, `60`, delay the first notification, and send “Garage is closed” on resolution. Starting at 14:00, its reminders occur at 14:15, 14:45, 15:45, 16:45, and so on. With immediate delivery, there is an additional notification at 14:00.

## Operate and edit

Open the alert's device under Modern Alerts to access its entities. You can also add them to a dashboard with standard entity or button cards.

| Entity | Meaning |
| --- | --- |
| Status sensor | Raw `idle` = no incident; `on` = active; `off` = acknowledged. The UI labels these Idle, Active, and Acknowledged. |
| Problem binary sensor | On while the incident is unresolved, including when acknowledged. |
| Acknowledge button | Silences reminders for this incident. Available when active and acknowledgement is permitted. |
| Resume reminders button | Clears acknowledgement. The next reminder follows the existing schedule; there is no immediate send. |
| Test notification button | Sends the current alert message without creating an incident or enabling a resolution notification. Available when destinations are configured. |

Acknowledge does not disable monitoring. When the source clears, the incident ends, any eligible resolution notification is sent, and the next incident starts unacknowledged. Timers continue advancing silently while acknowledged.

Use the configuration entry's **Configure/options** control to edit every setting. Name, timing, destination, and message edits retain acknowledgement when the watched entity and matching state stay the same. Saving changed options cancels pending delivery work and recalculates the next deadline from save time using the current interval cursor; it does not send an extra immediate reminder for an unchanged active condition. Changing the condition starts fresh without sending a resolution message for the replaced condition. Entity IDs remain stable when the alert is renamed.

Disabling the configuration entry stops the alert entirely. Deleting its entry removes its entities and listeners. Neither operation sends a resolution notification. These operations are different from acknowledgement.

## Notification compatibility

- **Legacy actions** such as `notify.mobile_app_my_phone` or `notify.persistent_notification` receive `message`, optional `title`, and optional `data`. In the destination list, select or enter the suffix such as `mobile_app_my_phone`. Provider-specific top-level arguments such as SMS `target` can be supplied by a preconfigured notify group, as with built-in Alert.
- **Notify entities** use `notify.send_message`. Home Assistant 2026.9.2 accepts message and title for that action; it does not accept arbitrary extra data. The wizard rejects extra data when notify entities are selected. A provider may not support titles even though the action accepts them.
- **Multiple destinations** are attempted independently. A missing or failing destination does not prevent the others from being called. Errors appear on the status sensor under `notification_errors` using exception categories rather than rendered payloads.
- **Slow providers** have a ten-second dispatch timeout per destination. Repeated reminders are coalesced while one is pending, so a slow provider cannot build an unbounded reminder backlog. Condition changes and acknowledgement remain responsive.

Templates are rendered for each notification, including the title on resolution. Template failures do not stop future reminders. Provider data is literal: Jinja inside the data mapping is not rendered.

For compatible Companion notification actions, use a stable tag in extra data:

```yaml
tag: garage-door
```

Set the resolution message to `clear_notification` to request clearing that tagged notification. Actual replacement/clearing behavior depends on the provider and phone platform. Arbitrary provider-supported buttons can be included in the same data mapping; their callback automation is configured separately, as with built-in Alert.

## Actions and migration

All actions target this integration's **status sensor**, not the watched source or an `alert.*` entity. Find the actual entity ID on the alert device; generated names can vary with existing entities and user renames.

| Built-in action | Modern Alerts action | Clearer alias |
| --- | --- | --- |
| `alert.turn_off` | `modern_alerts.turn_off` | `modern_alerts.acknowledge` |
| `alert.turn_on` | `modern_alerts.turn_on` | `modern_alerts.unacknowledge` |
| `alert.toggle` | `modern_alerts.toggle` | — |
| — | `modern_alerts.test_notification` | — |

Example acknowledgement action, also available through the automation action editor:

```yaml
action: modern_alerts.acknowledge
target:
  entity_id: sensor.garage_is_open_status
```

Legacy YAML fields map to the wizard directly: `entity_id`/`state` to Condition; `repeat`, `skip_first`, and `can_acknowledge` to Timing; `notifiers` to legacy destinations; and `message`, `title`, `done_message`, and `data` to Content. There is no automatic YAML importer in this release.

Recreate and review the alert, disable the corresponding legacy alert, then activate/test the replacement to avoid duplicate reminders. Remove obsolete legacy YAML using Home Assistant's normal configuration/restart workflow. Update dashboard references and callback automations to the new status entity and action domain. The built-in Alert integration is not overridden.

## Parity and deliberate differences

The lifecycle, fixed/list/fractional intervals, immediate/delayed start, acknowledgement, rearming, templates, completion eligibility, multiple legacy notifiers, provider data, and state-only operation are implemented. Differential tests compare states and notification payloads directly with the installed `homeassistant.components.alert` implementation.

Completion eligibility means that an alert notification was **attempted**, not confirmed delivered or read. Resolving before the first attempt sends no completion message. Acknowledging after an attempt still allows completion. Missing source entities are ignored; a nonmatching `unknown` or `unavailable` state resolves the incident, matching built-in Alert. Choose those strings as the matching state if the alert should specifically detect that condition.

The main differences are:

- Entities and actions use the new namespace; native status and buttons replace the built-in Alert frontend toggle.
- Configuration is stored in config entries and managed in the UI.
- Empty/non-finite/unschedulable interval lists and malformed configuration are rejected. Blank optional message fields mean “not configured.”
- Notify entities, explicit testing, live edits, failure isolation, bounded dispatch, and stale-callback protection are additions.
- Startup evaluation is an explicit optional improvement. It is off by default.

**Incident state and acknowledgement are not persisted across restart, configuration-entry reload, or disable/enable.** With startup evaluation off, the integration starts idle and waits for a source event, matching built-in Alert. With it on, a currently matching source starts a new incident and resets the schedule. Ordinary options edits preserve acknowledgement without reloading the entry.

Snooze, escalation, durable recovery, quiet hours, automatic phone acknowledgement buttons, and native compound-condition builders are future features. They are not part of this release's parity claim.

## Development and validation

Use Python 3.14:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
.venv/bin/python -m pytest --timeout=20 --cov=custom_components.modern_alerts --cov-report=term-missing
```

The pinned test fixture package installs Home Assistant 2026.9.2. Tests cover lifecycle behavior, notification adapters, validation, real config/options managers, registry entities, action targeting, deletion/reload, slow-provider races, and creation/edit/deletion through the authenticated HTTP endpoints used by the UI. External notification delivery is mocked; the HTTP workflow test uses a local test server. These checks do not substitute for rendering the forms in an installed frontend or testing delivery on a real phone.

Before using on your installation, create an alert watching an Input boolean helper with a short interval, test immediate and delayed notifications, acknowledge/resume it, clear it, and edit/delete it through Devices & services. Verify the intended behavior with your actual notification provider.

## References

- [Built-in Alert documentation](https://www.home-assistant.io/integrations/alert/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant notification actions](https://www.home-assistant.io/integrations/notify/)
- [Companion notification behavior](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

MIT licensed; see [LICENSE](LICENSE).
