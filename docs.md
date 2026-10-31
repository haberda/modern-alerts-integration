# Modern Alerts reference

This page covers the parts of Modern Alerts that are useful once the basic setup is working. Start with the [README](README.md) for installation and the first alert.

## HACS publishing

HACS requires a public GitHub repository. The current `git.haber.haus` remote cannot be added directly. Publish or mirror the repository to GitHub, then add it in HACS under **Custom repositories** with the **Integration** category.

The root `hacs.json` uses the README as the HACS description. The integration manifest names `@haberda` as code owner and keeps the current documentation and issue-tracker host. If support moves to GitHub, update those links and add a repository description with topics such as `home-assistant`, `hacs`, and `alerts`.

The HACS workflow runs on pushes, pull requests, releases, manual requests, and a weekly schedule. Home Assistant Brands registration is separate; add `modern_alerts` to the Brands repository before submitting the integration for broader distribution. Publish a GitHub release with a tag matching the manifest version, currently `0.5.0`, or HACS will use the default branch.

## Alert lifecycle

An alert watches a source entity and creates an incident when the configured condition matches. The last reminder interval repeats forever. The first notification can be immediate or delayed with **Skip first notification**.

Acknowledgement silences reminders for the current incident and does not disable monitoring. Snooze pauses delivery for a separate period. Resume clears acknowledgement and snooze, then continues from the existing schedule. When the source recovers, the incident ends and the next one starts unacknowledged. Resolution messages and recovery outputs are sent after acknowledgement unless **Send resolution notification after acknowledgement** is disabled.

Editing an alert keeps its incident when the watched condition is unchanged. Changes to the source or condition start a fresh incident. Message, destination, and name changes preserve the current deadline; interval changes restart the interval sequence. Configuration unload and deletion stop the alert without sending a resolution message.

## Conditions and timing

An exact-state condition compares the source state literally. For numeric alerts, choose either Problem below or Problem above and optionally set a recovery threshold. Values are compared in the source's units. An expected unit suspends evaluation when the source has no matching unit.

Compound conditions contain up to 20 state or numeric comparisons. **All** uses AND; **Any** uses OR. Every rule entity is watched. Known false rules can resolve an All group, and known true rules can activate an Any group. If the result remains uncertain, the unavailable-source policy decides whether to resolve or suspend the incident. Use a Template binary sensor for nested logic.

Activation and recovery debounce require the condition to remain continuously true. Returning to the previous state cancels the pending transition. Attribute changes do not extend the deadline.

## Restart recovery

Enable **Restore incidents after restart or reload** to persist acknowledgement, notification eligibility, interval position, incident and action identity, snooze expiry, pending transitions, the next deadline, stage position, and bounded history. Startup waits for Home Assistant and a usable source. A matching source resumes the saved incident; a clear source follows the configured recovery policy. At most one overdue reminder is attempted after recovery.

Saved state uses private Home Assistant storage. Disabling restoration removes saved incident data, and deleting the entry removes it as well. A crash near dispatch can still lose or repeat a notification; there is no exactly-once delivery guarantee.

## Notification delivery

Legacy notify actions receive `message`, optional `title`, and optional `data`. In the destination list, enter the action suffix, such as `mobile_app_my_phone`. Notify entities use `notify.send_message`; arbitrary provider data is rejected for those targets.

Destinations run independently. One missing or failing provider does not stop another. Errors are reported as exception categories under `notification_errors`. Each provider has a ten-second timeout, and reminders are coalesced while delivery is pending. Templates render for each notification. Provider data is passed literally, so Jinja inside the data mapping is not rendered.

For Companion notifications, a stable tag can be supplied in the provider data:

```yaml
tag: garage-door
```

Set the resolution message to `clear_notification` when the provider supports clearing a tagged notification. Actual replacement behavior depends on the provider and phone platform.

### Phone controls

Enable **Add Companion phone controls** and select a direct `mobile_app_*` action. Notifications can include Acknowledge, Snooze, and Open alert. Each action carries the alert entry and incident token, so callbacks from old or resolved incidents are ignored. Generated controls replace `data.actions` for those destinations. Notify groups and notify entities do not receive generated controls.

## Device outputs

Outputs let an alert control lights, sirens, TTS, audio players, and custom Home Assistant actions without an intermediary automation.

| Output | Behavior |
| --- | --- |
| Light | Blink or hold a light with optional color and brightness. Restoration can return it to its previous state. |
| Siren | Turn on a siren with optional tone and volume for a bounded duration, then clean it up. |
| Spoken announcement | Use a TTS provider and media players with optional language, message, and volume. |
| Chime or audio | Play a URL or `media-source://` URI with an optional content type and volume. |
| Custom action | Run Home Assistant actions for the alert, cleanup, and optional recovery sequence. |

Outputs can run at every reminder or once per incident. Durations range from 0.1 to 300 seconds; blink intervals range from 0.5 to 60 seconds. Up to 20 outputs and 20 devices per built-in output can be configured.

Acknowledgement, snooze, source suspension, resolution, edits, and unload cancel active effects. Lights restore their previous state when possible, sirens turn off, and speaker playback stops with optional volume restoration. Manual changes detected during an effect make the session yield control and skip restoration. Physical snapshots are not persisted across crashes.

Built-in outputs coordinate through a per-entity lock. A second alert waits for the first effect and cleanup, with a ten-second limit. Custom actions need explicit cleanup actions because Home Assistant cannot undo arbitrary actions that already ran.

## Delivery policies

Enable **Configure escalation, delivery rules, and history** to open the delivery editor.

Escalation stages have elapsed-minute thresholds. A stage can add recipients, enable outputs, and replace the reminder interval. Outputs assigned to a stage remain off until that stage. Acknowledgement stops stage advancement; snooze delays delivery without changing incident age.

Quiet hours use Home Assistant's local timezone and can cross midnight. Weekly windows use weekday, start, and end values; overnight windows belong to their starting weekday. Multiple windows use OR. Quiet hours, weekly windows, and presence rules are evaluated separately for notifications and outputs. Held delivery can resume when a window opens, while recovery delivery blocked by policy is dropped.

Presence rules can use people, trackers, occupancy sensors, or input booleans. Choose whether any source must be present or every source must be known and away. Unknown states do not establish absence. Presence changes re-evaluate held delivery immediately.

Reusable profiles hold shared destinations and outputs. Profiles do not watch conditions or create entities. Editing one updates alerts that reference it for later delivery; disabling or deleting one reports it as unavailable.

Grouping collects matching alerts for 1–300 seconds. Alerts must share destinations, title configuration, and provider data. Acknowledged or resolved incidents are removed before dispatch. A minimum notification interval can coalesce frequent reminders. Group and rate-limit state is held in memory and resets after restart.

## Overview and history

The **Modern Alerts** sidebar shows alert state, incident start, current stage, pending transitions, next delivery opportunities, recent history, and delivery blockers. **Explain delivery** is read-only and does not call notification or device services. Administrators can preview current message, title, and resolution templates.

The status sensor keeps bounded lifecycle, escalation, notification, and output history. It exposes `incident_started`, `escalation_stage`, and `policy_errors`. Set the history limit to 0 to disable it or choose up to 100 entries. History contains timestamps, identifiers, and error categories, not rendered messages or payloads. **Clear incident history** clears current integration history without changing the incident or Home Assistant Recorder data.

## Duplicate and import

Choose **Duplicate an existing entry** in the setup flow to create a copy of an alert or profile. Configuration, profiles, stages, outputs, and schedules are copied; incident state, acknowledgement, snooze, history, and entity identity are not.

Choose **Import legacy Alert YAML** to paste one definition, a named mapping, or an `alert:` section. Select one definition and review it through the normal wizard. Imports accept the built-in Alert schema, including list or scalar repeat intervals and `can_acknowledge`. Pasted YAML is limited to 64 KiB and 100 definitions. Aliases, includes, secret tags, non-JSON values, and unsupported fields are rejected. The importer never edits or disables the original YAML alert.

Disable the corresponding legacy alert before enabling the replacement to avoid duplicate reminders. Update dashboard references and automations to point to the new status sensor and `modern_alerts` actions.

## Actions

Actions target the alert's status sensor. Entity IDs can vary after naming changes, so use the ID shown on the alert device.

| Action | Use |
| --- | --- |
| `modern_alerts.acknowledge` | Silence the active incident. |
| `modern_alerts.unacknowledge` | Resume reminders. |
| `modern_alerts.toggle` | Switch acknowledgement state. |
| `modern_alerts.snooze` | Pause delivery, optionally with `minutes`. |
| `modern_alerts.cancel_snooze` | End snooze without clearing acknowledgement. |
| `modern_alerts.test_notification` | Send the current message without creating an incident. |
| `modern_alerts.test_output` | Run one configured output. |
| `modern_alerts.stop_outputs` | Stop active effects and clean them up. |
| `modern_alerts.clear_history` | Clear recent metadata without changing the incident. |

Example:

```yaml
action: modern_alerts.acknowledge
target:
  entity_id: sensor.garage_is_open_status
```

The built-in equivalents are `alert.turn_off`, `alert.turn_on`, and `alert.toggle`. Modern Alerts uses config entries and its own entity namespace, while adding notify entities, live editing, outputs, policies, import, and the overview.

## References

- [Built-in Alert documentation](https://www.home-assistant.io/integrations/alert/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant notification actions](https://www.home-assistant.io/integrations/notify/)
- [Companion notification behavior](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

MIT licensed; see [LICENSE](LICENSE).

This integration was created with the help of AI tools.
