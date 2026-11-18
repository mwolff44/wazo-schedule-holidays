# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Test doubles mirroring xivo_dao.alchemy (ScheduleTime) and confd's schedule service."""


class NotFound(Exception):
    """Stands for xivo_dao.helpers.exception.NotFoundError."""


class FakePeriod:
    """Same columns and property behaviour as xivo_dao ScheduleTime."""

    def __init__(self, **kwargs):
        self.mode = None
        self.hours = None
        self.weekdays = None
        self.monthdays = None
        self.months = None
        self.action = None
        self.actionid = None
        self.actionargs = None
        self.commented = 0
        for name, value in kwargs.items():
            setattr(self, name, value)

    @property
    def hours_start(self):
        return self.hours.split("-", 1)[0] if self.hours else None

    @hours_start.setter
    def hours_start(self, value):
        end = self.hours_end or ""
        self.hours = f"{value or ''}{'-' + end if end else ''}"

    @property
    def hours_end(self):
        parts = self.hours.split("-", 1) if self.hours else ""
        return parts[1] if len(parts) == 2 else None

    @hours_end.setter
    def hours_end(self, value):
        start = self.hours_start or ""
        self.hours = f"{start}{'-' + value if value else ''}"

    @property
    def week_days(self):
        return _expand(self.weekdays, 7)

    @week_days.setter
    def week_days(self, value):
        self.weekdays = ",".join(str(x) for x in value)

    @property
    def month_days(self):
        return _expand(self.monthdays, 31)

    @month_days.setter
    def month_days(self, value):
        self.monthdays = ",".join(str(x) for x in value)

    @property
    def months_list(self):
        return _expand(self.months, 12)

    @months_list.setter
    def months_list(self, value):
        self.months = ",".join(str(x) for x in value)


def _expand(text, maximum):
    if not text:
        return list(range(1, maximum + 1))
    result = []
    for item in text.split(","):
        if "-" in item:
            start, end = map(int, item.split("-", 1))
            result += list(range(start, end + 1))
        else:
            result.append(int(item))
    return result


class FakeSchedule:
    """Same exceptional_periods setter as xivo_dao Schedule (sets the mode)."""

    def __init__(self, **kwargs):
        self.periods = []
        for name, value in kwargs.items():
            setattr(self, name, value)

    @property
    def exceptional_periods(self):
        return [p for p in self.periods if p.mode == "closed"]

    @exceptional_periods.setter
    def exceptional_periods(self, value):
        self.periods = [p for p in self.periods if p.mode != "closed"]
        for period in value:
            period.mode = "closed"
            self.periods.append(period)


def make_schedule(
    schedule_id=1,
    tenant_uuid="11111111-1111-1111-1111-111111111111",
    timezone="Europe/Amsterdam",
    destination=("voicemail", "12", None),
    periods=(),
):
    return FakeSchedule(
        id=schedule_id,
        tenant_uuid=tenant_uuid,
        timezone=timezone,
        fallback_action=destination[0],
        fallback_actionid=destination[1],
        fallback_actionargs=destination[2],
        exceptional_periods=list(periods),
    )


class FakeScheduleService:
    def __init__(self, *schedules, fail_edit=None):
        self.schedules = {schedule.id: schedule for schedule in schedules}
        self.edits = []
        self.fail_edit = fail_edit

    def get(self, resource_id, **kwargs):
        schedule = self.schedules.get(resource_id)
        tenant_uuids = kwargs.get("tenant_uuids")
        if schedule is None or (
            tenant_uuids is not None and schedule.tenant_uuid not in tenant_uuids
        ):
            raise NotFound(resource_id)
        return schedule

    def edit(self, schedule, updated_fields=None):
        if self.fail_edit:
            raise self.fail_edit
        self.edits.append((schedule.id, updated_fields))
