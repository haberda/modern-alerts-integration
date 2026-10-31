# Repository guidance

## Project

Modern Alerts is a custom Home Assistant integration that reproduces the built-in
Alert lifecycle and adds acknowledgement, snooze, actionable notifications,
device outputs, escalation, routing policies, reusable profiles, compound
conditions, schedules, import, and an overview panel. The integration domain is
`modern_alerts`. Read `README.md` for user-facing behavior and installation.

## Code map

All runtime integration code lives in `custom_components/modern_alerts/`:

- `__init__.py`: config-entry lifecycle, services, platform setup, and panel startup.
- `runtime.py`: incident state, transitions, timers, restoration, delivery routing,
  acknowledgement, snooze, escalation, and history.
- `models.py`: validated, serializable configuration.
- `config_flow.py`, `output_flow.py`, `policy_flow.py`: native setup and options UI.
- `notifications.py`, `grouping.py`: notification rendering and grouped delivery.
- `outputs.py`, `output_config.py`: light, siren, speech, audio, and custom actions.
- `conditions.py`, `policies.py`, `profiles.py`: compound matching and reusable policy data.
- `frontend.py`, `frontend/overview.js`, `preview.py`: overview panel and read-only delivery explanations.
- `sensor.py`, `binary_sensor.py`, `button.py`, `select.py`: exposed entities and controls.
- `strings.json`, `translations/en.json`: UI text and translations.
- `brand/icon.png`, `brand/logo.png`: local Home Assistant brand assets.

`hacs.json` and `.github/workflows/hacs.yml` contain HACS metadata and validation.
`ALERT_AUTOMATION_PLAN.md` is an ignored planning file and should remain ignored.

## Behavior to preserve

- Keep acknowledgement, snooze, resolution, and incident reset semantics distinct.
- Never send stale notifications or outputs after acknowledgement, resolution,
  cancellation, configuration replacement, or an incident generation change.
- Preserve restored deadlines, incident identity, action-token validation, and
  bounded redacted history when changing runtime behavior.
- Keep notification delivery and physical-output delivery independently gated by
  quiet hours, weekly windows, presence, grouping, rate limits, and escalation.
- Clean up device effects on acknowledgement, snooze, suspension, resolution,
  reload, and unload. Respect observed manual device changes during restoration.
- Keep previews and explanations read-only. They must not send notifications,
  invoke device actions, mutate history, or alter incident state.
- Preserve stable entity unique IDs, config-entry identity, and existing saved
  configuration when adding or renaming UI fields.
- Keep generated Companion actions scoped to the alert entry and incident token.
- Treat provider success as dispatch completion, not proof that a person received
  or read a notification.

## Implementation

Use Home Assistant async APIs and current config-entry/entity patterns. Keep
blocking work off the event loop. Update both `strings.json` and
`translations/en.json` when changing UI text. Keep generated brand assets in
`custom_components/modern_alerts/brand/` and do not use Home Assistant official
branding in custom artwork.

Keep changes scoped to the requested behavior. Add focused regression coverage
for lifecycle, routing, persistence, UI flow, output cleanup, permissions, or
frontend changes. Avoid unrelated formatting changes.

## Validation

Run from the repository root:

```bash
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
.venv/bin/python -m pytest -q --timeout=20 --tb=short
git diff --check
```

The test environment targets Home Assistant 2026.9.2 and uses the pinned
dependencies in `requirements-test.txt`. Tests that exercise Home Assistant's
fixture event loop may require the normal project escalation settings. If the
frontend package is unavailable, install the version pinned in
`requirements-test.txt` before running the panel tests.

For documentation-only changes, check links, headings, image paths, and
whitespace; a full runtime test run is usually unnecessary. For brand changes,
verify both PNGs are transparent RGBA images and keep `icon.png` and `logo.png`
in sync unless a deliberate distinction is required.

## Git and documentation

Use small logical commits with clear imperative messages. Do not rewrite history,
force-push, or change remotes unless explicitly requested. Keep README content
focused on installation, configuration, behavior, migration, and HACS usage;
do not add internal test reports or development logs to it. Document meaningful
runtime boundaries and user-visible limitations.
