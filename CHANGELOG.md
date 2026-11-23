# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-23

First stable release. Validated end to end on a Wazo 26.10 stack (public holidays, closures, cron,
uninstall, Alsace and Moselle, several tenants, country change on an already synchronised schedule).

### Added
- `wazo-confd` plugin keeping the exceptional periods of a schedule in sync with the public holidays
  of a country and optional subdivision (e.g. `NL`, `FR`, `FR` / `6AE`, `57`), using the vendored
  `holidays` library.
- Holidays API (`/schedules/{id}/holidays`): choose the country, exclude holidays, display names in
  the requested language.
- Closures API (`/schedules/{id}/closures`): one-off date-time ranges, split into one period per day.
  Holidays and closures are independent and share one JSON file per schedule.
- `POST /schedules/{id}/exceptional-periods/sync` applies holidays and closures
  (`.../holidays/sync` is kept as an alias).
- Daily synchronisation CLI (`python3 -m wazo_schedule_holidays [-q] [-n] [-s ID]`) and
  `/etc/cron.d/wazo-schedule-holidays`, without any REST call.
- Per-schedule JSON storage with atomic writes and `flock`; contract tests against the real
  `wazo-confd` and `xivo-dao` sources of Wazo 26.10.

### Fixed
- The cron file is owned by `root` after installation (cron ignores any other owner).
- The CLI keeps the confd default configuration (`KeyError: 'user'`).
- Holiday names no longer depend on the process locale and fall back to English for unsupported
  languages.
- The bdist tarball is packaged whatever the setuptools name normalisation.
- The JSON configuration is written as UTF-8 instead of ASCII escapes.

### Notes
- A period has no year: only dates in `[today, today + 365 days[` are applied. A closure further
  ahead is stored and applied by the cron when it enters the window.
- The JSON configuration is not replicated in HA nor backed up with the database.

[1.0.0]: https://github.com/mwolff44/wazo-schedule-holidays/releases/tag/v1.0.0
