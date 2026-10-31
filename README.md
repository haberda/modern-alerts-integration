# Modern Alerts

Modern Alerts is a Home Assistant integration for repeating alerts. You set it up and edit it from **Settings → Devices & services**. Each alert provides a status sensor, a problem binary sensor, and controls for acknowledgement, snoozing, resuming, and notification tests.

**Version 0.5.0 targets Home Assistant 2026.9.2 (Python 3.14).** Earlier versions are not supported. This integration uses its own `modern_alerts` actions and standard entities, so existing `alert.*` references need migration.

## Install

1. Copy the repository's **`custom_components/modern_alerts` directory**, including its translations and frontend directory, into your Home Assistant configuration directory:

   ```text
   <config>/custom_components/modern_alerts/__init__.py
   <config>/custom_components/modern_alerts/manifest.json
   <config>/custom_components/modern_alerts/...
   ```

2. Restart Home Assistant to discover the custom integration.
3. Set up your notification provider if you want notifications. State-only alerts are also supported.
4. Open **Settings → Devices & services → Add integration → Modern Alerts**.

This is a custom integration. Once it is installed, you can create and edit alerts without changing configuration files. HACS installation is supported from a public GitHub copy of this repository.

## Install with HACS

HACS requires a **public GitHub repository**. The current `git.haber.haus` remote cannot be added directly. Publish or mirror this repository to GitHub, then:

1. Open **HACS → ⋮ → Custom repositories**.
2. Enter the public GitHub repository URL and choose **Integration**.
3. Find **Modern Alerts** in HACS and download it.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → Modern Alerts**.

Home Assistant **2026.9.2 or newer** is required by the HACS metadata. HACS downloads `custom_components/modern_alerts`, including its frontend and translations. No separate dashboard resource or release ZIP is needed. Existing manual installations can use the same integration directory and domain when adopting HACS.

The integration includes local Home Assistant brand assets in `custom_components/modern_alerts/brand/`. Home Assistant 2026.3 and newer use `icon.png` and `logo.png` from that directory for the integration tile and brand proxy; older versions may continue to show the generic custom-integration artwork until the integration is added to the central Home Assistant Brands repository.

For maintainers: `hacs.json` uses the README as the HACS description. The integration manifest identifies `@haberda` as code owner and retains the current documentation and issue-tracker host. Update those links if support moves to GitHub. Add a repository description and relevant topics (for example, `home-assistant`, `hacs`, and `alerts`) on the GitHub repository. `.github/workflows/hacs.yml` runs official HACS validation on pushes, pull requests, published releases, manual requests, and weekly; it needs no custom token and does not post PR comments. Remote validation cannot run against the self-hosted origin. HACS also documents Home Assistant Brands registration as a publishing requirement; add `modern_alerts` there before expecting all publishing checks to pass. These repository files do not create that registration or a default-store listing.

For versioned downloads, publish a GitHub release with a tag matching the integration manifest version (currently `0.5.0`). HACS can otherwise install the default branch. See the [HACS publishing requirements](https://hacs.dev/docs/publish/start/) and [integration requirements](https://hacs.dev/docs/publish/integration/).

## Create an alert

The setup wizard has five steps. Output and delivery settings are optional.

1. Condition: choose the name, source entity, and problem state. A door binary sensor will usually use `on`. Enter the entity's state value rather than its translated label, such as “Open.” Numeric alerts use a problem threshold and, optionally, a recovery threshold. An expected unit can suspend an alert if the source changes units. You can also add compound rules and combine them with All (AND) or Any (OR).
2. Timing: enter one interval for a fixed reminder period, or several intervals in sequence. Values are minutes and can be fractional, down to `0.016`; the last interval repeats. This step also contains the first-notification, acknowledgement, debounce, restoration, unavailable-source, snooze, and Companion phone settings.
3. Destinations: choose legacy notify actions or notify entities. Leave both empty for a state-only alert. Legacy action names can be entered manually. You can also add lights, sirens, speakers, and custom outputs.
4. Content: enter an optional message, title, resolution message, and provider data. Message fields support Home Assistant templates. An empty message uses the alert name, while an empty resolution message disables completion notifications. The alert name is literal text, and provider data is forwarded unchanged.
5. Review: check the current source state, schedule, destinations, profiles, and stages. The optional delivery editor appears after Content when you enable escalation, delivery rules, and history in Timing.

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
| Output to test selector | Selects one configured device output for an isolated test. |
| Test selected output button | Starts that output only, without creating an incident or sending phone notifications. |
| Stop outputs button | Cancels active effects and tests with cleanup; does not acknowledge the incident. |
| Test notification button | Sends the current alert message without creating an incident or enabling a resolution notification. Available when destinations are configured. |

Acknowledgement silences the current incident; it does not disable monitoring. When the source clears, the incident ends and the next one starts unacknowledged. Timers continue while an incident is acknowledged. Resolution messages and recovery outputs are sent by default when an acknowledged incident clears. Disable **Send resolution notification after acknowledgement** to suppress them. Cleanup still runs, and snoozing alone does not suppress resolution.

Use the configuration entry's **Configure/options** control to edit every setting. Name, timing, destination, and message edits retain acknowledgement when the watched entity and condition (including numeric thresholds and expected unit) stay the same. Saving changed options cancels pending delivery work. Message, destination, and name edits preserve the existing deadline. Changing repeat intervals restarts the interval sequence from save time. Changing debounce delays restarts any pending transition. Ordinary edits send no extra immediate reminder for an unchanged active condition. Editing escalation stages re-evaluates their thresholds against the current incident age and can send a newly due stage immediately. Changing the condition starts fresh without sending a resolution message for the replaced condition. Entity IDs remain stable when the alert is renamed.

Disabling the configuration entry stops the alert entirely. Deleting its entry removes its entities and listeners. Neither operation sends a resolution notification. These operations are different from acknowledgement.

## Overview and delivery preview

Open **Modern Alerts** in the sidebar for a live overview. It refreshes every five seconds and can filter by name, active state, acknowledgement, snooze, suspension, idle state, or errors. Each card shows the incident start, current stage, pending transitions, next reminder opportunity, recent history, and the controls allowed for that user. Administrators can also see entries that failed to load.

Explain delivery shows the current destinations and the reason a notification or output is waiting. Reasons include acknowledgement, snooze, an unavailable source, quiet hours, weekly windows, presence rules, escalation, once-per-incident limits, and grouped or rate-limited delivery. It also shows the next reminder or escalation opportunity and any buffered notification deadline. These are opportunities, not guarantees; conditions and policies can change before dispatch. A grouped notification may include other alerts.

Administrators can preview the current message, title, and resolution templates. The preview is read-only: it does not call notification or device services, acknowledge an incident, change deadlines, or append history. It does not render payload data or device action templates. Other users see only alerts whose status entities they can read, and controls follow Home Assistant's normal entity permissions.

## Duplicate or import

In **Add integration → Modern Alerts**, choose **Duplicate an existing entry** or **Import legacy Alert YAML** in the draft-source selector, then continue. Leave the other fields empty for that first step.

- Duplicate an alert or profile to create a draft with “copy” added to its name. The draft includes its configuration, profiles, stages, outputs, and schedules, but not incident state, acknowledgement, snooze, history, or registered identity. Editing the copy does not edit the original; profile references remain shared.
- Import a single legacy definition, a mapping of named alerts, or an `alert:` section. Choose one definition and review it in the normal wizard. Templates and notification data are kept without rendering. The importer accepts the built-in Alert schema, including scalar or list repeat intervals and `can_acknowledge`. A paste can be up to 64 KiB and contain up to 100 definitions. YAML aliases, file includes, secret tags, non-JSON values, and unsupported fields are rejected.

Drafts create nothing until the final review. Disable the corresponding legacy alert before saving its replacement to avoid duplicate reminders. The importer does not edit configuration files, disable existing alerts, or redirect old `alert.*` entity/action references. Startup evaluation remains off unless enabled during review.

## Compound conditions

The condition form accepts up to 20 state-equals, state-not-equals, above, or below comparisons. Choose **All rules (AND)** or **Any rule (OR)**. Above and below comparisons are strict and use raw source values; they do not convert units or apply per-rule hysteresis. Leave the compound list empty to use the existing single-source numeric hysteresis. A Template binary sensor helper is better suited to nested or more elaborate expressions.

Every rule entity is watched. Activation/recovery debounce applies to the combined result. Missing, unknown, unavailable, or nonnumeric inputs are uncertain. Known false inputs decide an AND group; known true inputs decide an OR group. If the result is still uncertain, the unavailable-source policy resolves or suspends the incident. Restored incidents wait for a decidable result. Ordinary acknowledgement, snooze, escalation, and persistence apply to the combined condition.

The primary source is retained for existing output template variables; when omitted it defaults to the first rule's entity. Compound rules replace the primary source condition. Clearing the rules returns to single-source matching. Changing rules or AND/OR mode resets the incident and rebinds watched entities.

## Weekly delivery windows

The delivery editor and each device output editor accept weekly delivery windows. Add weekdays, start times, and end times. Use separate rows for weekday and weekend schedules. An empty list allows delivery all week. Multiple windows are combined with OR; quiet hours and presence rules still have to allow delivery.

Windows use Home Assistant's local timezone, include the start, and exclude the end. An overnight window belongs to its starting weekday: Monday 22:00–02:00 includes Tuesday before 02:00. Equal start/end is rejected; to allow a full day use adjacent windows. Overlaps are allowed. There are at most 28 windows per policy. Daylight-saving transitions follow local wall-clock time: repeated times match both occurrences, and skipped times do not occur.

Schedules gate delivery, not condition monitoring, incident age, or cleanup. Notification and output schedules are independent. An eligible held delivery can resume when its window opens; a running output is stopped when its policy closes. Time changes are checked within one minute. Resolution deliveries blocked by policy are dropped rather than queued, matching daily quiet-hour behavior. Explicit notification/output tests bypass delivery policies.

## Notification compatibility

Legacy actions such as `notify.mobile_app_my_phone` and `notify.persistent_notification` receive `message`, optional `title`, and optional `data`. In the destination list, select or enter the suffix, such as `mobile_app_my_phone`. Provider-specific arguments, such as an SMS `target`, can be supplied by a preconfigured notify group.

Notify entities use `notify.send_message`. In Home Assistant 2026.9.2 that action accepts message and title, but not arbitrary extra data. The wizard rejects extra data when a notify entity is selected. A provider may still ignore titles.

Destinations are attempted independently, so one missing or failing provider does not stop the others. Errors appear under `notification_errors` as exception categories rather than rendered payloads. Each provider has a ten-second timeout, and pending reminders are coalesced while a slow provider is running.

Templates are rendered for each notification, including the title on resolution. Template failures do not stop future reminders. Provider data is literal: Jinja inside the data mapping is not rendered.

For compatible Companion notification actions, use a stable tag in extra data:

```yaml
tag: garage-door
```

Set the resolution message to `clear_notification` to request clearing that tagged notification. Actual replacement/clearing behavior depends on the provider and phone platform. With automatic phone controls disabled, arbitrary provider-supported buttons can be included in the same data mapping; their callback automation is configured separately, as with built-in Alert.

## Device outputs

Alerts can notify through lights, sirens, TTS, audio, and custom actions as well as phone notifications. Configure these outputs inside the alert; no intermediary automation is required. In Destinations, enable **Add or edit light, siren, speaker, and custom outputs**, then use the output menu to add, edit, or remove them. Changes take effect when the alert is saved.

| Output | Configuration and behavior |
| --- | --- |
| Light | Select lights, Blink or Steady, optional color/brightness, duration, and restoration. Blink alternates on/off at the chosen interval; it does not require a device-native flash effect. |
| Siren | Select sirens, optional tone/volume, and maximum sounding duration. Both a device duration request and integration-owned cleanup are used; device capabilities vary. |
| Spoken announcement | Select a TTS provider entity and media players, optional language/message/volume, and maximum playback duration. The message defaults to the alert message. An optional recovery message runs on resolution. |
| Chime or audio | Select media players, an audio URL or `media-source://` URI, optional content type (defaults to `music`), volume, and maximum playback duration. The player must support the source/content type and be able to reach the media. |
| Custom actions | Use Home Assistant's native action editor for alert, cleanup, and optional recovery sequences. Actions can use `alert_name`, `entity_id` (watched source), `incident_id`, and the rendered `message`. No separate automation or script entity is necessary. |

Each output has **Run at every reminder**. On follows the existing alert schedule; off attempts the output once per incident at its first notification opportunity, respecting delayed-first delivery, acknowledgement, and snooze. Already-running effects are not duplicated. Once-per-incident attempt markers are saved when incident restoration is enabled. After a reload, interrupted effects are not replayed immediately; repeat-enabled outputs resume at the next eligible reminder.

Durations range from 0.1 to 300 seconds; blink intervals range from 0.5 to 60 seconds. The duration is an effect/playback limit, so a long spoken message or audio clip may be cut short. Providers have a separate ten-second action timeout. Up to 20 outputs may be configured per alert, with up to 20 devices per built-in output.

Acknowledgement, snooze, source suspension, resolution, edits, and unload cancel active effects. Light cleanup restores the prior on/off state and supported color/brightness settings when enabled, otherwise turns the light off. Siren cleanup turns it off. Speaker cleanup stops owned playback and optionally restores its previous volume. Resuming previous music is device-specific and is not implemented. Observed external changes cause that device session to yield control and skip restoration/stop commands, preserving the newer choice. Devices that report state without action context may conservatively be treated as externally changed; restoration is best effort.

Built-in outputs share a lock per target entity across alerts. A second effect waits for the current owner's effect and cleanup to finish; cancelling the waiter does not stop the owner. Waiting is bounded to ten seconds, after which a busy target is reported as a timeout. A repeat-enabled output can retry at its next reminder. This coordinates entity IDs; overlapping group entities or device aliases cannot be inferred as the same physical device.

**Custom actions require explicit cleanup.** Cancelling a sequence stops its remaining steps but cannot undo arbitrary actions already performed. Configure Stop / cleanup actions to turn off what the sequence enables. Cleanup runs on cancellation, completion, errors, or duration expiry and has a ten-second budget. Optional recovery actions are a separate, bounded sequence and should be self-contained; they do not run the stop sequence afterward. Detached work, such as a script started with `script.turn_on`, belongs to that script and requires an explicit stop action. Custom sequences are serialized within their own output but do not participate in built-in device locks.

Physical effects and pre-effect device snapshots are not persisted. Clean reload/shutdown attempts cleanup; a crash or loss of connectivity cannot guarantee device restoration. Prefer a siren with hardware duration support when that matters. Home Assistant may reject new custom script runs during shutdown, including custom cleanup; built-in device cleanup is still attempted.

To test, save the alert, open its device, choose **Output to test**, and press **Test selected output**. This runs only that bounded effect and its cleanup. **Stop outputs** ends it early. Tests do not create an incident or enable a resolution message. The existing **Test notification** button still tests phone/notify destinations only.

The status sensor exposes `configured_outputs` (IDs mapped to names), `active_outputs` (running or waiting output IDs), and `output_errors` (redacted categories per output/target). Output failures are independent of phone delivery and other devices. Tests start asynchronously; inspect these attributes for results. The `test_output` action can optionally select an ID explicitly, while the device picker provides the normal UI workflow.

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
| — | `modern_alerts.test_output` | Optional `output_id`; otherwise the device selector |
| — | `modern_alerts.stop_outputs` | Stop active effects and tests with cleanup |
| — | `modern_alerts.clear_history` | Clear recent metadata without changing the incident |

Example acknowledgement action, also available through the automation action editor:

```yaml
action: modern_alerts.acknowledge
target:
  entity_id: sensor.garage_is_open_status
```

Legacy YAML fields map directly to the wizard: `entity_id` and `state` go to Condition; `repeat`, `skip_first`, and `can_acknowledge` go to Timing; `notifiers` go to legacy destinations; and `message`, `title`, `done_message`, and `data` go to Content. The import workflow accepts these fields and lets you review them before creating an entry.

Recreate and review the alert, disable the corresponding legacy alert, then activate/test the replacement to avoid duplicate reminders. Remove obsolete legacy YAML using Home Assistant's normal configuration/restart workflow. Update dashboard references and callback automations to the new status entity and action domain. The built-in Alert integration is not overridden.

## Parity and deliberate differences

The integration keeps the built-in lifecycle: fixed, list, and fractional intervals; immediate or delayed starts; acknowledgement; rearming; templates; completion eligibility; multiple legacy notifiers; provider data; and state-only operation.

Completion eligibility means that an alert notification was **attempted**, not confirmed delivered or read. Resolving before the first attempt sends no completion message. Acknowledging after an attempt still allows completion. Under the default exact-state policy, missing source entities are ignored and a nonmatching `unknown` or `unavailable` state resolves the incident, matching built-in Alert. Choose those strings as the matching state if the alert should specifically detect that condition.

The main differences are:

- Entities and actions use the new namespace; native status and buttons replace the built-in Alert frontend toggle.
- Configuration is stored in config entries and managed in the UI.
- Empty/non-finite/unschedulable interval lists and malformed configuration are rejected. Blank optional message fields mean “not configured.”
- Notify entities, explicit testing, live edits, failure isolation, bounded dispatch, and stale-callback protection are additions.
- Startup evaluation is an explicit optional improvement. It is off by default.

## Reliability and additional controls

The reliability options live in the existing Condition and Timing forms. Existing configurations keep exact-state behavior, zero debounce, legacy unavailable-source handling, and restoration and automatic phone controls disabled unless you enable them.

Restart recovery saves acknowledgement, notification eligibility, the interval cursor, incident and action identity, snooze expiry, pending transitions, and the next reminder deadline when **Restore incidents after restart or reload** is enabled. Startup waits for Home Assistant and a usable source. A still-matching source resumes the incident; a clear source follows the recovery policy. One overdue reminder is attempted at most, then the next interval starts from recovery time.

Companion controls are available when **Add Companion phone controls** is enabled with a direct `mobile_app_*` notify action. Notifications can include Acknowledge, Snooze, and Open alert. Actions are tied to one incident and stale callbacks are ignored. Generated controls replace `data.actions` for those destinations; notify groups and notify entities do not receive them.

Activation and recovery delays require the matching condition to remain true. Returning to the prior state cancels the pending transition, while attribute updates do not extend its deadline. Snoozing advances the schedule silently and resumes it when the snooze expires. Acknowledge and Resume clear snooze; Cancel snooze preserves acknowledgement.

Choose **suspend** for missing, unknown, or unavailable sources when you want to keep the incident and pause dispatch. The deadline and interval cursor remain in place, and a usable source can produce one overdue reminder. Exact-state alerts can still watch the literal states `unknown` or `unavailable`. Invalid numeric readings and unit mismatches always suspend.

For low-battery hysteresis, set Problem below to `15`, Recover above to `20`, and expected unit to `%`. The alert starts below 15, stays active through 20, and clears above 20. High-temperature alerts use the opposite direction. Values are compared in the source's units without conversion.

The status sensor exposes `snoozed_until`, `source_suspended`, `pending_transition` (true for activation, false for recovery, null for none), `pending_deadline`, `next_notification`, `last_attempt`, and redacted `notification_errors`. A suspended alert may display a past reminder deadline; it does not dispatch until source recovery.

Restoration uses private, atomic Home Assistant storage with coalesced writes and an explicit save on unload/shutdown. Disabling restoration clears saved incident data; deleting the entry removes it. Old incomplete snapshots from the initial experimental persistence implementation are ignored; corrupt snapshots start fresh and expose `restore: invalid_snapshot`. There is no exactly-once delivery guarantee across a crash: a crash near dispatch or before the coalesced write can lose recent state or repeat a notification. A currently matching source is treated as continuation of the saved incident because transitions during downtime cannot be known.

Migration import, native compound-condition builders, weekly schedules, and the overview dashboard are included in 0.5.0. The remaining publishing work is external: publish the repository on GitHub, register the integration with Home Assistant Brands, publish a tagged release, and run the HACS validation workflow.

## Escalation, delivery rules, profiles, and history

Enable **Configure escalation, delivery rules, and history** in Timing to open the delivery editor after Content. Existing alerts retain their behavior until you configure these options.

Escalation stages have distinct elapsed-minute thresholds and names. You might notify a phone initially, enable a flashing light at five minutes, then enable a siren and add another recipient at fifteen minutes. An output assigned to a stage stays off until that stage; unassigned outputs are available immediately. Recipients and outputs accumulate, and a stage can replace the reminder interval. Reaching a stage creates its own notification opportunity.

Acknowledgement stops stage advancement. Resume re-evaluates elapsed incident time; snooze suppresses delivery without resetting that age. Restart restoration saves the incident start and stage position. After downtime, the integration advances to the latest due stage, attempts at most one catch-up notification, and resumes the schedule. Changing stage definitions during an incident re-evaluates them from the original start. Deleted stage outputs are reported through `policy_errors`.

Quiet hours use the Home Assistant timezone. Set them for phone notifications in the delivery editor or separately for each output. They can cross midnight and handle daylight-saving changes. Held messages and outputs resume within one minute after quiet hours end when the incident is still eligible. Acknowledgement prevents catch-up, while snooze delays it. Device effects stop when their policy becomes quiet, and cleanup still runs. Recovery messages and actions that fall in quiet hours are skipped.

Presence routing can use people, trackers, occupancy binary sensors, or input booleans. Choose whether any source must be `home`/`on`, or every source must be known and away. Unknown and missing states do not establish absence. Presence changes re-evaluate held delivery immediately. Each speaker output can use its own occupancy entities. Test buttons ignore these rules so you can check a device regardless of occupancy or quiet hours.

Reusable profiles hold shared destinations and outputs. Add a Modern Alerts entry, choose **Reusable output profile**, configure it, and select it from an alert's Destinations step. Profiles do not monitor conditions or create control entities. Editing a profile stops its old effects and updates alerts that reference it for later delivery. Disabling or deleting one reports it as unavailable while other destinations continue. Profiles cannot reference other profiles, and their output IDs are scoped to the profile.

Grouping collects related alerts for a 1–300 second window. Alerts join a group only when their destination sets, title configuration, and provider data match. Acknowledged, resolved, removed, and otherwise ineligible incidents are dropped before dispatch. A single-alert group keeps its phone controls; a multi-alert summary leaves them out because one button cannot identify which incident to acknowledge.

Set a minimum notification interval to coalesce frequent repeats. With no group, this applies to the alert's destination set; with a group, matching members share the limit. These policies apply to alert notifications, not physical outputs or resolution messages. Device outputs retain their duration, coalescing, and shared-device ownership behavior. Group buffers and rate-limit timestamps are in memory and reset after Home Assistant restarts. With incident restoration enabled, a held notification is remembered and can be queued again after recovery. No exactly-once delivery guarantee is made across crashes or provider failures.

The status sensor keeps recent lifecycle, escalation, notification, and output events. It also exposes `incident_started`, `escalation_stage`, and `policy_errors`. The history limit defaults to 50 entries and can be set to 0 or any value up to 100. Records contain timestamps, identifiers, and error categories; rendered messages and payloads are excluded. Enable incident restoration to keep history across reloads. Diagnostics contain the same bounded metadata, and **Clear incident history** removes the current integration history without changing the incident or Recorder data.

## References

- [Built-in Alert documentation](https://www.home-assistant.io/integrations/alert/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant notification actions](https://www.home-assistant.io/integrations/notify/)
- [Companion notification behavior](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

MIT licensed; see [LICENSE](LICENSE).

This integration was created with the help of AI tools.
