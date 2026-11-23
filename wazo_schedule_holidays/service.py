# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Application service: applies the synchronisation plan to a schedule.

Two independent features share one JSON file, one lock and one synchronisation
(a schedule has a single set of exceptional periods): the *holidays* of a country
and the one-off *closures*. Writing a closure synchronises the schedule at once;
writing the holidays configuration only stores it (``sync`` or the daily cron
applies it).

Nothing is imported from Wazo: the confd schedule service, the period class
(``xivo_dao`` ``ScheduleTime``) and the default timezone are injected by
``plugin.py``, which keeps this module testable in isolation.

Crash safety: the JSON file is written *before* the schedule is modified with
the union of the old and new managed dates, then rewritten once the schedule is
saved *and committed*, so the final file only reflects what is really in the
database (confd normally commits after the request, hence the injected
``commit``). Whatever fails in between, every period created by the plugin is
listed in the file, so none can be left orphaned.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from . import holiday_calendar
from .exceptions import (
    ClosureNotFound,
    ConfigNotFound,
    InvalidConfig,
    SyncInProgress,
)
from .holiday_calendar import holidays_for
from .storage import ConfigStorage, HolidayConfig, LockedError
from .sync import (
    Closure,
    Desired,
    Destination,
    Provider,
    Slot,
    compute_desired,
    local_today,
    period_kwargs,
    plan_sync,
    signature,
)

MAX_CLOSURES = 100
MAX_CLOSURE_DAYS = 366
KEY_YEARS = 6  # some holidays only exist every few years (e.g. Liberation Day, NL)
PERIOD_COLUMNS = (
    "hours",
    "weekdays",
    "monthdays",
    "months",
    "action",
    "actionid",
    "actionargs",
    "commented",
)


@dataclass
class SyncResult:
    dry_run: bool
    today: datetime.date
    changed: bool
    added: list[Desired]
    removed: list[Slot]  # (month, day, hours_start, hours_end) of removed periods
    managed: list[Desired]


class HolidaysService:
    def __init__(
        self,
        schedule_service: Any,
        storage: ConfigStorage,
        period_factory: Callable[..., Any],
        default_timezone: Callable[[], str | None],
        today: Callable[[str | None], datetime.date] = local_today,
        provider: Provider = holidays_for,
        commit: Callable[[], None] = lambda: None,
    ) -> None:
        self._schedules = schedule_service
        self._storage = storage
        self._factory = period_factory
        self._default_timezone = default_timezone
        self._today = today
        self._provider = provider
        self._commit = commit

    def get_config(
        self, schedule_id: int, tenant_uuids: list[str] | None
    ) -> HolidayConfig:
        schedule = self._schedule(schedule_id, tenant_uuids)
        return self._holidays_config(schedule)

    def update_config(
        self,
        schedule_id: int,
        tenant_uuids: list[str] | None,
        country: str,
        subdivision: str | None,
        excluded: Iterable[str],
    ) -> HolidayConfig:
        schedule = self._schedule(schedule_id, tenant_uuids)
        excluded = list(dict.fromkeys(excluded))
        self._validate(schedule, country, subdivision, excluded)
        with self._locked(schedule_id):
            previous = self._storage.load(schedule_id, schedule.tenant_uuid)
            config = dataclasses.replace(
                previous or HolidayConfig(),
                country=country,
                subdivision=subdivision,
                excluded=excluded,
            )
            self._storage.save(schedule_id, schedule.tenant_uuid, config)
            return config

    def delete_config(self, schedule_id: int, tenant_uuids: list[str] | None) -> None:
        """Remove the holidays (and their periods); the closures are kept."""
        schedule = self._schedule(schedule_id, tenant_uuids)
        with self._locked(schedule_id):
            config = dataclasses.replace(
                self._holidays_config(schedule),
                country=None,
                subdivision=None,
                excluded=[],
            )
            tenant_uuid = schedule.tenant_uuid
            self._storage.save(schedule_id, tenant_uuid, config)
            self._sync(schedule, config, dry_run=False)
            self._drop_if_empty(schedule_id, config)

    def list_closures(
        self, schedule_id: int, tenant_uuids: list[str] | None
    ) -> list[Closure]:
        schedule = self._schedule(schedule_id, tenant_uuids)
        config = self._storage.load(schedule_id, schedule.tenant_uuid)
        return list(config.closures) if config else []

    def get_closure(
        self, schedule_id: int, tenant_uuids: list[str] | None, closure_id: str
    ) -> Closure:
        for closure in self.list_closures(schedule_id, tenant_uuids):
            if closure.id == closure_id:
                return closure
        raise ClosureNotFound(schedule_id, closure_id)

    def create_closure(
        self,
        schedule_id: int,
        tenant_uuids: list[str] | None,
        start: datetime.datetime,
        end: datetime.datetime,
        label: str | None = None,
    ) -> Closure:
        closure = Closure(start, end, label, id=str(uuid.uuid4()))
        self._write_closures(
            schedule_id, tenant_uuids, lambda closures: [*closures, closure]
        )
        return closure

    def update_closure(
        self,
        schedule_id: int,
        tenant_uuids: list[str] | None,
        closure_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
        label: str | None = None,
    ) -> Closure:
        closure = Closure(start, end, label, id=closure_id)

        def replace(closures: list[Closure]) -> list[Closure]:
            self._find(schedule_id, closures, closure_id)
            return [closure if c.id == closure_id else c for c in closures]

        self._write_closures(schedule_id, tenant_uuids, replace)
        return closure

    def delete_closure(
        self, schedule_id: int, tenant_uuids: list[str] | None, closure_id: str
    ) -> None:
        def remove(closures: list[Closure]) -> list[Closure]:
            self._find(schedule_id, closures, closure_id)
            return [c for c in closures if c.id != closure_id]

        self._write_closures(schedule_id, tenant_uuids, remove)

    def _write_closures(
        self,
        schedule_id: int,
        tenant_uuids: list[str] | None,
        change: Callable[[list[Closure]], list[Closure]],
    ) -> None:
        """Store the new list of closures, then synchronise the schedule.

        If the synchronisation fails the closures stay stored: the daily cron
        retries (the managed periods are tracked as for any other sync).
        """
        schedule = self._schedule(schedule_id, tenant_uuids)
        with self._locked(schedule_id):
            config = self._storage.load(schedule_id, schedule.tenant_uuid)
            config = config or HolidayConfig()
            closures = change(list(config.closures))
            self._validate_closures(closures)
            config = dataclasses.replace(config, closures=closures)
            tenant_uuid = schedule.tenant_uuid
            self._storage.save(schedule_id, tenant_uuid, config)
            self._sync(schedule, config, dry_run=False)
            self._drop_if_empty(schedule_id, config)

    @staticmethod
    def _find(schedule_id: int, closures: list[Closure], closure_id: str) -> Closure:
        for closure in closures:
            if closure.id == closure_id:
                return closure
        raise ClosureNotFound(schedule_id, closure_id)

    def _drop_if_empty(self, schedule_id: int, config: HolidayConfig) -> None:
        if config.country is None and not config.closures:
            self._storage.delete(schedule_id)

    def sync(
        self,
        schedule_id: int,
        tenant_uuids: list[str] | None,
        dry_run: bool = False,
    ) -> SyncResult:
        schedule = self._schedule(schedule_id, tenant_uuids)
        with self._locked(schedule_id):
            return self._sync(schedule, self._config(schedule), dry_run)

    def _sync(self, schedule: Any, config: HolidayConfig, dry_run: bool) -> SyncResult:
        today = self._today(self._timezone(schedule))
        destination = Destination.from_schedule(schedule)
        desired = compute_desired(
            config.country,
            config.subdivision,
            config.excluded,
            today,
            self._provider,
            config.closures,
        )
        existing = list(schedule.exceptional_periods)
        plan = plan_sync(existing, config.managed, desired, destination)
        result = SyncResult(
            dry_run=dry_run,
            today=today,
            changed=plan.changed,
            added=list(plan.add),
            removed=[day for p in plan.remove if (day := signature(p)) is not None],
            managed=list(plan.managed),
        )
        if dry_run or not plan.changed:
            return result

        # The ORM objects expire on commit: read what is needed beforehand.
        schedule_id, tenant_uuid = schedule.id, schedule.tenant_uuid
        intent = list(dict.fromkeys([*config.managed, *plan.managed]))
        self._storage.save(
            schedule_id, tenant_uuid, dataclasses.replace(config, managed=intent)
        )
        if plan.remove or plan.add:
            self._write_periods(schedule, existing, plan, destination)
            self._commit()
        self._storage.save(
            schedule_id, tenant_uuid, dataclasses.replace(config, managed=plan.managed)
        )
        return result

    def _write_periods(
        self, schedule: Any, existing: list, plan: Any, destination: Destination
    ) -> None:
        # confd recreates all the periods of a mode on every write: kept periods
        # are copied column by column, exactly like a PUT with the full list.
        removed = {id(period) for period in plan.remove}
        kept = [self._copy(p) for p in existing if id(p) not in removed]
        added = [
            self._factory(**period_kwargs(desired, destination)) for desired in plan.add
        ]
        schedule.exceptional_periods = kept + added
        self._schedules.edit(schedule, updated_fields=["exceptional_periods"])

    def _copy(self, period: Any) -> Any:
        return self._factory(**{name: getattr(period, name) for name in PERIOD_COLUMNS})

    def _schedule(self, schedule_id: int, tenant_uuids: list[str] | None) -> Any:
        if tenant_uuids is None:
            return self._schedules.get(schedule_id)
        return self._schedules.get(schedule_id, tenant_uuids=tenant_uuids)

    def _config(self, schedule: Any) -> HolidayConfig:
        config = self._storage.load(schedule.id, schedule.tenant_uuid)
        if config is None:
            raise ConfigNotFound(schedule.id)
        return config

    def _holidays_config(self, schedule: Any) -> HolidayConfig:
        config = self._config(schedule)
        if config.country is None:
            raise ConfigNotFound(schedule.id)
        return config

    def _timezone(self, schedule: Any) -> str | None:
        return schedule.timezone or self._default_timezone()

    def _validate(
        self,
        schedule: Any,
        country: str,
        subdivision: str | None,
        excluded: list[str],
    ) -> None:
        try:
            holiday_calendar.list_subdivisions(country)
        except holiday_calendar.UnknownCountry as exc:
            raise InvalidConfig("country", f"unknown country '{country}'") from exc
        if subdivision is not None and (
            subdivision not in holiday_calendar.list_subdivisions(country)
        ):
            raise InvalidConfig(
                "subdivision", f"unknown subdivision '{subdivision}' for {country}"
            )
        if not excluded:
            return
        first_year = self._today(self._timezone(schedule)).year
        known = {
            holiday.key
            for year in range(first_year, first_year + KEY_YEARS)
            for holiday in self._provider(country, subdivision, year)
        }
        unknown = [key for key in excluded if key not in known]
        if unknown:
            raise InvalidConfig("excluded", f"unknown holidays: {', '.join(unknown)}")

    @staticmethod
    def _validate_closures(closures: list[Closure]) -> None:
        if len(closures) > MAX_CLOSURES:
            raise InvalidConfig(
                "closures", f"at most {MAX_CLOSURES} closures are allowed"
            )
        for closure in closures:
            if closure.start.second or closure.start.microsecond:
                raise InvalidConfig("closures", "start must be a whole minute")
            if closure.end.second or closure.end.microsecond:
                raise InvalidConfig("closures", "end must be a whole minute")
            if closure.start.tzinfo or closure.end.tzinfo:
                raise InvalidConfig(
                    "closures", "times are local to the schedule: no UTC offset"
                )
            if closure.start >= closure.end:
                raise InvalidConfig("closures", "start must be before end")
            if (closure.end.date() - closure.start.date()).days >= MAX_CLOSURE_DAYS:
                raise InvalidConfig(
                    "closures", f"a closure spans at most {MAX_CLOSURE_DAYS} days"
                )
        contents = {(c.start, c.end, c.label) for c in closures}
        if len(contents) != len(closures):
            raise InvalidConfig("closures", "duplicate closure")

    @contextlib.contextmanager
    def _locked(self, schedule_id: int) -> Iterator[None]:
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(self._storage.lock(schedule_id, blocking=False))
            except LockedError as exc:
                raise SyncInProgress(schedule_id) from exc
            yield
