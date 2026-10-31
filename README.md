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

## Create an alert

The setup wizard has five steps. Output and delivery settings are optional.

1. Condition: choose the name, source entity, and problem state. A door binary sensor will usually use `on`. Enter the entity's state value rather than its translated label, such as “Open.” Numeric alerts use a problem threshold and, optionally, a recovery threshold. An expected unit can suspend an alert if the source changes units. You can also add compound rules and combine them with All (AND) or Any (OR).
2. Timing: enter one interval for a fixed reminder period, or several intervals in sequence. Values are minutes and can be fractional, down to `0.016`; the last interval repeats. This step also contains the first-notification, acknowledgement, debounce, restoration, unavailable-source, snooze, and Companion phone settings.
3. Destinations: choose legacy notify actions or notify entities. Leave both empty for a state-only alert. Legacy action names can be entered manually. You can also add lights, sirens, speakers, and custom outputs.
4. Content: enter an optional message, title, resolution message, and provider data. Message fields support Home Assistant templates. An empty message uses the alert name, while an empty resolution message disables completion notifications. The alert name is literal text, and provider data is forwarded unchanged.
5. Review: check the current source state, schedule, destinations, profiles, and stages. The optional delivery editor appears after Content when you enable escalation, delivery rules, and history in Timing.

Saving never sends a test notification. The optional **Evaluate the existing condition on startup** setting can send a real alert immediately if the source already matches when the integration loads. It defaults off for compatibility with built-in Alert.

For example, a garage-door alert can watch `binary_sensor.garage_door` for `on`, use intervals `15`, `30`, `60`, delay the first notification, and send “Garage is closed” on resolution. Starting at 14:00, its reminders occur at 14:15, 14:45, 15:45, 16:45, and so on. With immediate delivery, there is an additional notification at 14:00.

## Use and configure alerts

Open an alert's device under Modern Alerts to find its status sensor, problem sensor, buttons, output selector, and test controls. The status sensor is `idle` when there is no incident, `on` while an incident is active, and `off` after acknowledgement. Add these entities to dashboards with the standard Home Assistant cards.

Acknowledgement silences the current incident. Snooze pauses delivery for a chosen period, while Resume clears acknowledgement and snooze and follows the existing schedule. The alert continues monitoring its source until you disable or delete the configuration entry.

The sidebar overview shows active alerts, recent history, current stages, pending transitions, and delivery blockers. Administrators can use **Explain delivery** to see why a notification or output is waiting. See [docs.md](docs.md) for the complete behavior reference, migration guide, outputs, policies, and actions.

## References

- [Built-in Alert documentation](https://www.home-assistant.io/integrations/alert/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant notification actions](https://www.home-assistant.io/integrations/notify/)
- [Companion notification behavior](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

MIT licensed; see [LICENSE](LICENSE).

This integration was created with the help of AI tools.
