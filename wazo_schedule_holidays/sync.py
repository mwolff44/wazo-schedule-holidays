# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Core of the synchronisation. Pure logic: no I/O and no Wazo import.

wazo-agid evaluates a schedule period on hours, week days, month days and
months only: *there is no year*. A period for 6 April would match every year.
The plugin therefore only keeps the holidays that fall in
``[today, today + WINDOW_DAYS[`: each (month, day) occurs once in that window,
and past holidays are removed by the next (daily) synchronisation.

Periods created by the plugin cannot be identified by an id or a label (confd
recreates all periods on each write). They are recognised by their *signature*
(a full-day period on one month day of one month, every week day) **and** only
if their date is listed in the managed entries stored by the plugin. A manual
period with the same signature is therefore never modified or removed.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .holiday_calendar import Holiday, holidays_for

WINDOW_DAYS = 365
HOURS_START = "00:00"
HOURS_END = "23:59"
ALL_WEEK_DAYS = [1, 2, 3, 4, 5, 6, 7]

Provider = Callable[[str, "str | None", int], Iterable[Holiday]]


def _empty_to_none(value: str | None) -> str | None:
    return value or None


@dataclass(frozen=True)
class Destination:
    """A schedule destination, normalised so that '' and None are equal."""

    action: str | None
    arg1: str | None = None
    arg2: str | None = None

    def __post_init__(self) -> None:
        for name in ("action", "arg1", "arg2"):
            object.__setattr__(self, name, _empty_to_none(getattr(self, name)))

    @classmethod
    def from_period(cls, period) -> Destination:
        return cls(period.action, period.actionid, period.actionargs)

    @classmethod
    def from_schedule(cls, schedule) -> Destination:
        return cls(
            schedule.fallback_action,
            schedule.fallback_actionid,
            schedule.fallback_actionargs,
        )


@dataclass(frozen=True)
class Desired:
    date: datetime.date
    key: str

    @property
    def day(self) -> tuple[int, int]:
        return (self.date.month, self.date.day)


@dataclass
class SyncPlan:
    remove: list = field(default_factory=list)
    add: list[Desired] = field(default_factory=list)
    managed: list[Desired] = field(default_factory=list)
    _previous: frozenset[Desired] = frozenset()

    @property
    def changed(self) -> bool:
        return bool(self.remove or self.add or set(self.managed) != self._previous)


def local_today(
    timezone_name: str | None, now: datetime.datetime | None = None
) -> datetime.date:
    """Today's date in the schedule timezone (system timezone if not set)."""
    now = now or datetime.datetime.now(datetime.UTC)
    if not timezone_name:
        return now.astimezone().date()
    try:
        return now.astimezone(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc


def compute_desired(
    country: str,
    subdivision: str | None,
    excluded: Iterable[str],
    today: datetime.date,
    provider: Provider = holidays_for,
) -> list[Desired]:
    end = today + datetime.timedelta(days=WINDOW_DAYS)
    excluded_keys = set(excluded)
    by_date: dict[datetime.date, str] = {}
    for year in (today.year, today.year + 1):
        for holiday in provider(country, subdivision, year):
            if not today <= holiday.date < end or holiday.key in excluded_keys:
                continue
            by_date.setdefault(holiday.date, holiday.key)
    return [Desired(date=date, key=key) for date, key in sorted(by_date.items())]


def period_kwargs(desired: Desired, destination: Destination) -> dict:
    """Fields of the exceptional period closing the given holiday."""
    return {
        "hours_start": HOURS_START,
        "hours_end": HOURS_END,
        "week_days": list(ALL_WEEK_DAYS),
        "month_days": [desired.date.day],
        "months_list": [desired.date.month],
        "action": destination.action,
        "actionid": destination.arg1,
        "actionargs": destination.arg2,
    }


def _signature(period) -> tuple[int, int] | None:
    """(month, day) if the period looks like one created by the plugin."""
    if period.hours_start != HOURS_START or period.hours_end != HOURS_END:
        return None
    if sorted(period.week_days) != ALL_WEEK_DAYS:
        return None
    if len(period.month_days) != 1 or len(period.months_list) != 1:
        return None
    return (period.months_list[0], period.month_days[0])


def plan_sync(
    existing: Iterable,
    old_managed: Iterable[Desired],
    desired: Iterable[Desired],
    destination: Destination,
) -> SyncPlan:
    """Compute what to remove from / add to the exceptional periods.

    ``existing`` are the current exceptional periods of the schedule,
    ``old_managed`` the entries recorded by the previous synchronisation.
    """
    desired = list(desired)
    previous = frozenset(old_managed)
    old_days = {entry.day for entry in previous}
    wanted_days = {item.day for item in desired}

    remove = []
    kept_days: set[tuple[int, int]] = set()
    manual_days: set[tuple[int, int]] = set()
    for period in existing:
        day = _signature(period)
        if day is None:
            continue
        if day not in old_days:
            manual_days.add(day)
        elif (
            day in wanted_days
            and day not in kept_days
            and Destination.from_period(period) == destination
        ):
            kept_days.add(day)
        else:
            remove.append(period)

    add = []
    managed = []
    for item in desired:
        if item.day in kept_days:
            managed.append(item)
        elif item.day in manual_days:
            continue
        else:
            add.append(item)
            managed.append(item)

    return SyncPlan(remove=remove, add=add, managed=managed, _previous=previous)
