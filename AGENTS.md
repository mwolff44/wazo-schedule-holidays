# AGENTS.md

Purpose: `wazo-confd` plugin that keeps the *exceptional periods* of Wazo schedules in sync with
public holidays computed for a country/subdivision (e.g. `NL`, `FR`/`6AE`).
License: GPL-3.0+. Version source: `wazo/plugin.yml`. Minimum Wazo version: 26.10.
Plan and verified findings: `docs/PLAN.md` (read it before changing behaviour).

## Why it works this way (verified against wazo-26.10)

- Schedules are evaluated by `wazo-agid`, not Asterisk. A period only knows hours, week days,
  month days and months: **there is no year**. A period for 6 April therefore fires every year.
- Hence the plugin keeps only dates in `[today, today + 365 days[` (each date occurs once) and
  removes past ones; the sync must run daily (cron).
- Exceptional periods (`closed`) are evaluated before opened periods and need a destination:
  we copy the schedule's `closed_destination`.
- confd stores no id/label for periods and recreates them on every write. Periods created by the
  plugin are recognised by **signature** (`closed`, `00:00-23:59`, all week days, one month day,
  one month) and only if listed in the `managed` entries of the schedule's JSON file.

## Layout

- `wazo_schedule_holidays/`: package. The core (`holiday_calendar`, `sync`, `storage`, `_vendor`)
  must **not import anything from Wazo** so it is testable in isolation. Only `plugin`, `resources`,
  `service` and the CLI may import `wazo_confd` / `xivo_dao`.
- `vendor/`: embedded `holidays`, `python-dateutil`, `six` (Debian bookworm only ships
  `python3-holidays` 0.10.1, which has a different API). Never edit by hand: `make vendor`.
- `wazo/`: `plugin.yml` and `rules` for wazo-plugind (`rules package` must copy `vendor/`).
- `tests/`: pytest unit tests.

## Invariants to preserve

- Holiday keys are the `en_US` names (stable across languages); duplicates within a year get a
  date-rank suffix (`Name #1`). Display language is a request parameter, never stored.
- The JSON config is written atomically (temp file + `os.replace`) under a `flock`; files must be
  owned by `www-data` (confd runs as `www-data`, the cron CLI runs as root).
- A manual period identical to a desired holiday is left alone and never managed.
- The sync is idempotent: no write when nothing changed.

## Conventions

- Python header: `# Copyright 2026 The Wazo Authors  (see the AUTHORS file)` and
  `# SPDX-License-Identifier: GPL-3.0+`.
- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- `pre-commit run --all-files` (black, isort, flake8, pyupgrade, mypy, copyright check);
  `vendor/` is excluded from all tools.
- Tests: `pip install -r requirements-test.txt && pytest tests/`.
