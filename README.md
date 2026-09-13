# Modern Alerts

A custom Home Assistant integration that reproduces the built-in Alert lifecycle with setup and editing through **Settings → Devices & services**. Each alert has a status sensor, a problem binary sensor, and buttons to acknowledge, snooze, resume, and test notifications.

**Version 0.2.0 targets Home Assistant 2026.9.2 (Python 3.14).** The test suite runs against that exact release. Earlier versions are not supported; newer releases require compatibility testing. This integration uses its own `modern_alerts` actions and standard entities, so existing `alert.*` references need migration.

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

1. **Condition:** name, source entity, and the exact problem state. Use `on` for a typical door binary sensor. Match the underlying state string, not a translated label such as “Open.” For numeric alerts, enter one problem threshold and an optional recovery threshold; the exact state field is then ignored. An optional expected unit protects against changes in source units. Use a Template binary sensor helper for compound conditions.
2. **Timing:** add one interval for fixed repetition or several intervals in order. Values are minutes and may be fractional (minimum `0.016`). The final interval repeats indefinitely. Choose whether to delay the first notification and whether acknowledgement is allowed. Set sustained activation/recovery delays, startup/restoration policy, unavailable-source policy, snooze defaults, and Companion phone controls here.
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
| Snooze button | Silences reminders for the configured duration without changing acknowledgement. |
| Cancel snooze button | Ends temporary silence without clearing acknowledgement. |
| Resume reminders button | Clears acknowledgement and snooze. The next reminder follows the existing schedule; there is no immediate send. |
| Test notification button | Sends the current alert message without creating an incident or enabling a resolution notification. Available when destinations are configured. |

Acknowledge does not disable monitoring. When the source clears, the incident ends, any eligible resolution notification is sent, and the next incident starts unacknowledged. Timers continue advancing silently while acknowledged.

Use the configuration entry's **Configure/options** control to edit every setting. Name, timing, destination, and message edits retain acknowledgement when the watched entity and condition (including numeric thresholds and expected unit) stay the same. Saving changed options cancels pending delivery work. Message, destination, and name edits preserve the existing deadline. Changing repeat intervals restarts the interval sequence from save time. Changing debounce delays restarts any pending transition. None sends an extra immediate reminder for an unchanged active condition. Changing the condition starts fresh without sending a resolution message for the replaced condition. Entity IDs remain stable when the alert is renamed.

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

Set the resolution message to `clear_notification` to request clearing that tagged notification. Actual replacement/clearing behavior depends on the provider and phone platform. With automatic phone controls disabled, arbitrary provider-supported buttons can be included in the same data mapping; their callback automation is configured separately, as with built-in Alert.

## Actions and migration

All actions target this integration's **status sensor**, not the watched source or an `alert.*` entity. Find the actual entity ID on the alert device; generated names can vary with existing entities and user renames.

| Built-in action | Modern Alerts action | Clearer alias |
| --- | --- | --- |
| `alert.turn_off` | `modern_alerts.turn_off` | `modern_alerts.acknowledge` |
| `alert.turn_on` | `modern_alerts.turn_on` | `modern_alerts.unacknowledge` |
| `alert.toggle` | `modern_alerts.toggle` | — |
| — | `modern_alerts.test_notification` | — |
| — | `modern_alerts.snooze` | Optional `minutes`; otherwise the alert default |
| — | `modern_alerts.cancel_snooze` | Keeps acknowledgement |

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

Completion eligibility means that an alert notification was **attempted**, not confirmed delivered or read. Resolving before the first attempt sends no completion message. Acknowledging after an attempt still allows completion. Under the default exact-state policy, missing source entities are ignored and a nonmatching `unknown` or `unavailable` state resolves the incident, matching built-in Alert. Choose those strings as the matching state if the alert should specifically detect that condition.

The main differences are:

- Entities and actions use the new namespace; native status and buttons replace the built-in Alert frontend toggle.
- Configuration is stored in config entries and managed in the UI.
- Empty/non-finite/unschedulable interval lists and malformed configuration are rejected. Blank optional message fields mean “not configured.”
- Notify entities, explicit testing, live edits, failure isolation, bounded dispatch, and stale-callback protection are additions.
- Startup evaluation is an explicit optional improvement. It is off by default.

## Reliability and additional controls

All six extensions are configurable in the existing Condition and Timing forms. Existing configurations keep exact-state behavior, zero debounce, legacy unavailable-source handling, and restoration/automatic phone controls off unless enabled.

- **Restart recovery:** enable **Restore incidents after restart or reload** to save acknowledgement, notification-attempt eligibility, interval cursor, incident/action identity, snooze expiry, pending transitions, and the next reminder deadline. Startup waits until Home Assistant has started and the watched source is usable. A still-matching source resumes the saved incident; a clear source follows the recovery policy. At most one overdue reminder is attempted, then the next interval runs from recovery time. With restoration off, startup evaluation either starts a new incident or, when also off, waits for a source event.
- **Phone controls:** enable **Add Companion phone controls** and select a direct `mobile_app_*` legacy notify action. Notifications include Acknowledge when allowed, Snooze when enabled, and Open alert. Controls identify one alert and incident; callbacks from resolved incidents and repeated actions are ignored. Handling a control invalidates the other controls from that batch of notifications; subsequent reminders carry a new token. Resolution/test messages do not include generated controls. Automatic controls replace `data.actions` for these destinations. Notify groups and notify entities do not receive automatic controls.
- **Debounce:** activation and recovery delays require a continuously observed matching condition. Returning to the prior state cancels the pending transition; attribute updates do not extend the deadline. Reminder timing begins after activation. Reminders continue during pending recovery. With restoration enabled, pending deadlines survive reload; state changes during downtime cannot be reconstructed.
- **Snooze:** use the device button, phone control, or `modern_alerts.snooze` with optional `minutes` (greater than zero, at most 10080). Repeating snooze replaces its expiry. The reminder schedule advances silently during snooze, and expiry resumes that schedule without an extra immediate notification. Snooze never clears a manual acknowledgement. Acknowledge and Resume clear snooze; Cancel snooze preserves acknowledgement. Resolution clears snooze so a new incident is never silenced by an old expiry. Status remains Active while snoozed unless also acknowledged.
- **Unavailable sources:** choose **suspend** to retain the incident and pause dispatch while the source is missing, unknown, or unavailable. The reminder deadline/cursor are retained; a usable source can cause at most one overdue reminder. A gap cancels a pending debounce, except while initially reconciling a saved deadline. Exact-state alerts explicitly watching `unknown` or `unavailable` can still match those values. Invalid/nonfinite numeric readings and expected-unit mismatches always suspend, regardless of this setting.
- **Numeric hysteresis:** for a low battery, set Problem below to `15`, Recover above to `20`, and expected unit to `%`. It activates strictly below 15 and remains active through 20, recovering strictly above 20. For high temperatures, use Problem above and Recover below instead. Only one problem direction is allowed; recovery must lie in the recovery direction. Without a recovery threshold, the incident clears as soon as the problem comparison is false. Values use the source's units without conversion; an optional expected unit suspends evaluation if the unit is absent or changes.

The status sensor exposes `snoozed_until`, `source_suspended`, `pending_transition` (true for activation, false for recovery, null for none), `pending_deadline`, `next_notification`, `last_attempt`, and redacted `notification_errors`. A suspended alert may display a past reminder deadline; it does not dispatch until source recovery.

Restoration uses private, atomic Home Assistant storage with coalesced writes and an explicit save on unload/shutdown. Disabling restoration clears saved incident data; deleting the entry removes it. Old incomplete snapshots from the initial experimental persistence implementation are ignored; corrupt snapshots start fresh and expose `restore: invalid_snapshot`. There is no exactly-once delivery guarantee across a crash: a crash near dispatch or before the coalesced write can lose recent state or repeat a notification. A currently matching source is treated as continuation of the saved incident because transitions during downtime cannot be known.

Escalation, quiet hours, grouping/rate limits, migration import, and native compound-condition builders remain future work.

## Development and validation

Use Python 3.14:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
.venv/bin/python -m pytest --timeout=20 --cov=custom_components.modern_alerts --cov-report=term-missing
```

The pinned test fixture package installs Home Assistant 2026.9.2. Tests cover lifecycle behavior, notification adapters, validation, real config/options managers, registry entities, action targeting, deletion/reload, restored deadlines, debounce cancellation, hysteresis/unit checks, snooze, stale phone actions, slow-provider races, and creation/edit/deletion through the authenticated HTTP endpoints used by the UI. External notification delivery is mocked; the HTTP workflow test uses a local test server. These checks do not substitute for rendering the forms in an installed frontend or testing delivery on a real phone.

Before using on your installation, create an alert watching an Input boolean helper with a short interval, test immediate and delayed notifications, acknowledge/resume it, clear it, and edit/delete it through Devices & services. Verify the intended behavior with your actual notification provider.

## References

- [Built-in Alert documentation](https://www.home-assistant.io/integrations/alert/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant notification actions](https://www.home-assistant.io/integrations/notify/)
- [Companion notification behavior](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

MIT licensed; see [LICENSE](LICENSE).
