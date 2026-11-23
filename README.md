# wazo-schedule-holidays

`wazo-confd` plugin that closes a schedule on public holidays, computed for a country (and optional
subdivision) such as `NL`, `FR`, or `FR` / `6AE` (Alsace) and `57` (Moselle).

Wazo requires **26.10** or later. License: GPL-3.0+.

## How it works

`wazo-agid` evaluates a schedule on hours, week days, month days and months only: **a period has no
year**. A holiday that moves every year (Easter Monday...) therefore cannot be entered once and for
all. The plugin keeps a per-schedule configuration (country, subdivision, excluded holidays) and a
daily synchronisation creates one full-day *exceptional period* per holiday of the next 365 days,
and removes the past ones. Each period uses the **closed destination** of the schedule.

- Periods created by the plugin are recognised by their signature **and** the dates recorded in
  `/var/lib/wazo-schedule-holidays/<schedule_id>.json`. Other exceptional periods are never touched.
- A manual period identical to a holiday is left alone and not managed.
- Holidays are identified by their English name (`Easter Monday`), whatever the display language.

## Installation

```sh
wazo-plugind-cli install git https://github.com/mwolff44/wazo-schedule-holidays --ref main
```

This installs the package, enables the plugin (`/etc/wazo-confd/conf.d/schedule-holidays.yml`),
creates `/var/lib/wazo-schedule-holidays` (owner `www-data`), installs the daily cron job
(`/etc/cron.d/wazo-schedule-holidays`) and restarts `wazo-confd`. The `holidays` library is embedded.

## API

Base URL: `https://<host>/api/confd/1.1`. Authentication: `X-Auth-Token` (and `Wazo-Tenant`).
Full reference: `GET /api/confd/1.1/api/api.yml` once the plugin is loaded.

| Route | ACL |
|---|---|
| `GET /schedule-holidays/countries` | `confd.schedule_holidays.read` |
| `GET /schedule-holidays/countries/{code}?subdivision=&year=&language=` | `confd.schedule_holidays.read` |
| `GET /schedules/{id}/holidays` | `confd.schedules.{id}.holidays.read` |
| `PUT /schedules/{id}/holidays` | `confd.schedules.{id}.holidays.update` |
| `DELETE /schedules/{id}/holidays` | `confd.schedules.{id}.holidays.delete` |
| `POST /schedules/{id}/exceptional-periods/sync[?dry_run=true]` | `confd.schedules.{id}.exceptional_periods.sync.create` |
| `POST /schedules/{id}/holidays/sync[?dry_run=true]` (alias of the previous one) | `confd.schedules.{id}.holidays.sync.create` |
| `GET`, `POST /schedules/{id}/closures` | `confd.schedules.{id}.closures.read`, `.create` |
| `GET`, `PUT`, `DELETE /schedules/{id}/closures/{closure_id}` | `confd.schedules.{id}.closures.{closure_id}.read`, `.update`, `.delete` |

```sh
BASE=https://wazo.example.com/api/confd/1.1
H="X-Auth-Token: $TOKEN"

# What would be offered for Alsace in 2027, in French?
curl -sk -H "$H" "$BASE/schedule-holidays/countries/FR?subdivision=6AE&year=2027&language=fr"

# Select the country, deselect a holiday, preview then apply
curl -sk -X PUT -H "$H" -H 'Content-Type: application/json' \
  -d '{"country":"FR","subdivision":"6AE","excluded":["Good Friday"]}' "$BASE/schedules/1/holidays"
curl -sk -X POST -H "$H" "$BASE/schedules/1/holidays/sync?dry_run=true"
curl -sk -X POST -H "$H" "$BASE/schedules/1/holidays/sync"
```

### One-off closures

Closures are independent from the holidays: a schedule can have a country, closures, or both. A
closure is a date-time range in the schedule timezone, for instance a seminar from Thursday
19 November 2027 12:00 to Friday 20 November 2027 14:00 (or a single morning, 09:00 to 12:00):

```sh
curl -sk -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"start":"2027-11-19T12:00","end":"2027-11-20T14:00","label":"Seminar"}' \
  "$BASE/schedules/1/closures"              # 201, returns the closure with its id
curl -sk -H "$H" "$BASE/schedules/1/closures"
curl -sk -X PUT -H "$H" -H 'Content-Type: application/json' \
  -d '{"start":"2027-11-19T12:00","end":"2027-11-20T16:00"}' "$BASE/schedules/1/closures/$ID"
curl -sk -X DELETE -H "$H" "$BASE/schedules/1/closures/$ID"
```

Writing a closure **synchronises the schedule immediately**. As a period has no year, a closure
becomes one period per day (12:00-23:59, then 00:00-14:00, with full days in between) only when its
date is within the next 365 days (the daily cron adds the later ones as they come into range), and
is removed once past. `end` is inclusive to the minute. At most 100 closures, each spanning at most
366 days. Expired closures are ignored and can be deleted. `DELETE .../holidays` only removes the
holidays: the closures are kept.

`PUT .../holidays` only stores the configuration; `sync` (or the daily cron) applies it. `DELETE
.../holidays` also removes the periods created for the holidays. Errors: `400` invalid configuration, `404` unknown schedule (or other tenant) or no
configuration, `409` the schedule is being updated by another request.

## Daily synchronisation

`/etc/cron.d/wazo-schedule-holidays` runs `python3 -m wazo_schedule_holidays --quiet` every day at
04:07 as root. By hand: `python3 -m wazo_schedule_holidays [-n/--dry-run] [-s/--schedule-id ID]`.
It reuses the confd schedule service (database, validation, `schedule_edited` bus events) and does
not call the REST API. Exit status 1 if a schedule failed (the others are still synchronised).

## Limits

- The JSON configuration is not replicated in HA nor included in the database backups.
- If the synchronisation fails for more than a year, a stale holiday could fire again.
- Country names are the English ISO names; subdivision names come from the calendar library.
- The calendar library only provides public holidays (no school holidays or optional days).

## Development

```sh
python3 -m venv .venv-test && .venv-test/bin/pip install -r requirements-test.txt
.venv-test/bin/pytest tests/        # unit tests (no Wazo needed)
make contract                       # + contract tests on Python 3.11 against confd/xivo-dao sources
make vendor                         # rebuild vendor/ from requirements-vendor.txt
```

See `AGENTS.md` for the architecture and invariants, and `docs/PLAN.md` for the design decisions.

## Testing on a Wazo 26.10 stack

1. Install, then check: `systemctl status wazo-confd`, `/api/confd/1.1/api/api.yml` mentions
   `/schedules/{schedule_id}/holidays`, `ls -ld /var/lib/wazo-schedule-holidays` (owner `www-data`).
2. `python3 -c "import wazo_schedule_holidays"` as root, and
   `python3 -m wazo_schedule_holidays --dry-run` (exit 0, nothing to do).
3. On a schedule with a closed destination: `PUT` then `sync?dry_run=true`, then `sync`. In the UI
   the schedule shows the new exceptional periods; `GET /schedules/{id}` lists them.
4. Call the schedule's incall on a holiday date (or temporarily set the system date): the closed
   destination must be reached. This exercises `wazo-agid`.
5. Run `sync` again: `"changed": false`. Edit the closed destination, `sync`: periods follow it.
6. Add a manual exceptional period, `DELETE .../holidays`: only the plugin's periods disappear.
7. `/etc/cron.d/wazo-schedule-holidays` present; run the CLI as root and check the JSON files stay
   owned by `www-data` and that confd can still `PUT`.
8. Reinstall / upgrade the plugin over an existing install (not checked by the author).
