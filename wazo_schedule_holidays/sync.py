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
(one month day of one month, every week day, given hours: a *slot*) **and** only
if that slot is listed in the managed entries stored by the plugin. A manual
period with the same signature is therefore never modified or removed.

Holidays are full-day slots. A one-off *closure* (a date-time range such as
Thursday 12:00 -> Friday 14:00) is split into one slot per day, since a period
holds a single set of hours: 12:00-23:59 the first day, 00:00-23:59 for the days
in between, 00:00-14:00 the last day (hours are inclusive to the minute, like
the full-day 00:00-23:59). The same window applies to those slots.
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
CLOSURE_KEY = "closure"

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


Slot = tuple[int, int, str, str]  # (month, day, hours_start, hours_end)


@dataclass(frozen=True)
class Closure:
    """A one-off closure: naive local date-times (schedule timezone), minutes."""

    start: datetime.datetime
    end: datetime.datetime
    label: str | None = None


@dataclass(frozen=True)
class Desired:
    date: datetime.date
    key: str
    hours_start: str = HOURS_START
    hours_end: str = HOURS_END

    @property
    def slot(self) -> Slot:
        return (self.date.month, self.date.day, self.hours_start, self.hours_end)


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


def closure_slots(closure: Closure) -> list[Desired]:
    """One entry per day of the closure (hours of the first/last day cut)."""
    first, last = closure.start.date(), closure.end.date()
    key = closure.label or CLOSURE_KEY
    entries = []
    for offset in range((last - first).days + 1):
        date = first + datetime.timedelta(days=offset)
        hours_start = closure.start.strftime("%H:%M") if date == first else HOURS_START
        hours_end = closure.end.strftime("%H:%M") if date == last else HOURS_END
        if date == last and date != first and hours_end == HOURS_START:
            continue  # the closure ends at midnight: nothing left on that day
        entries.append(Desired(date, key, hours_start, hours_end))
    return entries


def compute_desired(
    country: str,
    subdivision: str | None,
    excluded: Iterable[str],
    today: datetime.date,
    provider: Provider = holidays_for,
    closures: Iterable[Closure] = (),
) -> list[Desired]:
    end = today + datetime.timedelta(days=WINDOW_DAYS)
    excluded_keys = set(excluded)
    by_date: dict[datetime.date, str] = {}
    for year in (today.year, today.year + 1):
        for holiday in provider(country, subdivision, year):
            if not today <= holiday.date < end or holiday.key in excluded_keys:
                continue
            by_date.setdefault(holiday.date, holiday.key)
    by_slot = {
        item.slot: item
        for item in (Desired(date=date, key=key) for date, key in by_date.items())
    }
    for closure in closures:
        for item in closure_slots(closure):
            if today <= item.date < end:
                by_slot.setdefault(item.slot, item)  # a holiday wins on a full day
    return sorted(by_slot.values(), key=lambda item: (item.date, item.hours_start))


def period_kwargs(desired: Desired, destination: Destination) -> dict:
    """Fields of the exceptional period closing the given holiday or closure day."""
    return {
        "hours_start": desired.hours_start,
        "hours_end": desired.hours_end,
        "week_days": list(ALL_WEEK_DAYS),
        "month_days": [desired.date.day],
        "months_list": [desired.date.month],
        "action": destination.action,
        "actionid": destination.arg1,
        "actionargs": destination.arg2,
    }


def signature(period) -> Slot | None:
    """Slot if the period looks like one created by the plugin."""
    if not period.hours_start or not period.hours_end:
        return None
    if sorted(period.week_days) != ALL_WEEK_DAYS:
        return None
    if len(period.month_days) != 1 or len(period.months_list) != 1:
        return None
    return (
        period.months_list[0],
        period.month_days[0],
        period.hours_start,
        period.hours_end,
    )


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
    old_slots = {entry.slot for entry in previous}
    wanted_slots = {item.slot for item in desired}

    remove = []
    kept_slots: set[Slot] = set()
    manual_slots: set[Slot] = set()
    for period in existing:
        slot = signature(period)
        if slot is None:
            continue
        if slot not in old_slots:
            manual_slots.add(slot)
        elif (
            slot in wanted_slots
            and slot not in kept_slots
            and Destination.from_period(period) == destination
        ):
            kept_slots.add(slot)
        else:
            remove.append(period)

    add = []
    managed = []
    for item in desired:
        if item.slot in kept_slots:
            managed.append(item)
        elif item.slot in manual_slots:
            continue
        else:
            add.append(item)
            managed.append(item)

    return SyncPlan(remove=remove, add=add, managed=managed, _previous=previous)
