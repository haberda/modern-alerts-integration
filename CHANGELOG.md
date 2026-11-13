# Changelog

## 0.6.1

Requires Home Assistant 2026.9.2 or newer and Python 3.14. Home Assistant has required Python 3.14 since 2026.3; no older interpreter is supported.

### Fixed

- A restart or reload while a recurring task was due started a new incident: an acknowledged task went back to active, a notification was sent, and the task's history was lost. Tasks now always restore their incident, acknowledgement, snooze, reminder deadline, and history. Phone controls on notifications sent before the restart keep working.
- `modern_alerts.import_task` ignored `next_due` when `last_completed` was also given and computed the due date from the completion instead. The given due date is now used as the first due date.

### Changed

- **Restore incidents** and **Evaluate on startup** are no longer shown for recurring tasks. Tasks always restore, and always become due at startup if their time passed while Home Assistant was off.
- Documented that completing a fixed-schedule task early only skips the coming occurrence inside the task's lead time.

## 0.6.0

### Added

- Recurring tasks: alerts driven by a schedule instead of a watched entity, with after-completion, fixed-schedule, and one-off modes; completion by button, phone control, action, NFC tag, or legacy action ID; next-due and days-remaining sensors; and `modern_alerts.import_task`.

### Changed

- A reminder is sent when a snooze ends, and the repeat interval restarts from then.
- Phone controls stay valid for the whole incident on every recipient's phone.
- The status sensor's history and error attributes are no longer stored by the Recorder.

### Fixed

- Speaker volume and light state are restored after an output ends by itself or a device reports late.
- A run-once output is retried when every device was busy.
- Acknowledging or snoozing no longer cancels an output test or a recovery announcement.
