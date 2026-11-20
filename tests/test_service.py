# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime

import pytest

from tests.fakes import FakePeriod, FakeScheduleService, NotFound, make_schedule
from wazo_schedule_holidays.exceptions import (
    ConfigNotFound,
    InvalidConfig,
    SyncInProgress,
)
from wazo_schedule_holidays.holiday_calendar import Holiday, holidays_for
from wazo_schedule_holidays.service import HolidaysService
from wazo_schedule_holidays.storage import ConfigStorage
from wazo_schedule_holidays.sync import Closure, Desired

D = datetime.date
TENANT = "11111111-1111-1111-1111-111111111111"
TODAY = D(2026, 11, 18)


def default_table():
    return {
        2026: [
            Holiday("B", "B", D(2026, 11, 18)),
            Holiday("C", "C", D(2026, 12, 25)),
        ],
        2027: [Holiday("D", "D", D(2027, 3, 29))],
    }


class Env:
    """A service wired with fakes, for one schedule."""

    def __init__(self, tmp_path, schedule=None, provider=None, **kwargs):
        self.table = default_table()
        self.schedule = schedule or make_schedule()
        self.schedules = FakeScheduleService(self.schedule, **kwargs)
        self.storage = ConfigStorage(tmp_path / "holidays")
        self.today_calls = []
        self.default_tz_calls = 0
        self.commits = 0
        self.fail_commit = None
        self.today = TODAY
        self.service = HolidaysService(
            self.schedules,
            self.storage,
            FakePeriod,
            default_timezone=self._default_timezone,
            today=self._today,
            provider=provider or self._table_provider,
            commit=self._commit,
        )

    def _commit(self):
        if self.fail_commit:
            raise self.fail_commit
        self.commits += 1

    def _table_provider(self, country, subdivision, year):
        return self.table.get(year, [])

    def _default_timezone(self):
        self.default_tz_calls += 1
        return "UTC"

    def _today(self, timezone_name):
        self.today_calls.append(timezone_name)
        return self.today

    def configure(self, country="NL", subdivision=None, excluded=()):
        return self.service.update_config(1, None, country, subdivision, list(excluded))

    def days(self):
        return sorted(
            (p.months_list[0], p.month_days[0])
            for p in self.schedule.exceptional_periods
        )


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def real_env(tmp_path):
    return Env(tmp_path, provider=holidays_for)


def manual_period(**kwargs):
    fields = {
        "hours_start": "08:00",
        "hours_end": "12:00",
        "week_days": [1, 2],
        "month_days": [5],
        "months_list": [6],
        "action": "user",
        "actionid": "3",
        "actionargs": None,
    }
    return FakePeriod(mode="closed", **{**fields, **kwargs})


def test_get_config_without_configuration(env):
    with pytest.raises(ConfigNotFound):
        env.service.get_config(1, None)


def test_get_config_of_unknown_schedule_propagates_not_found(env):
    with pytest.raises(NotFound):
        env.service.get_config(99, None)


def test_get_config_respects_tenants(env):
    env.configure()

    with pytest.raises(NotFound):
        env.service.get_config(1, ["22222222-2222-2222-2222-222222222222"])
    assert env.service.get_config(1, [TENANT]).country == "NL"


def test_update_config_stores_the_schedule_tenant(env):
    env.configure("NL")

    assert env.storage.load_with_tenant(1)[0] == TENANT


def test_update_config_keeps_managed_entries_of_the_previous_config(env):
    env.configure()
    env.service.sync(1, None)

    updated = env.service.update_config(1, None, "FR", None, [])

    assert updated.country == "FR"
    assert updated.managed == [
        Desired(D(2026, 11, 18), "B"),
        Desired(D(2026, 12, 25), "C"),
        Desired(D(2027, 3, 29), "D"),
    ]


def test_update_config_deduplicates_excluded_keys(env):
    config = env.service.update_config(1, None, "NL", None, ["B", "C", "B"])

    assert config.excluded == ["B", "C"]


def test_update_config_rejects_unknown_country(env):
    with pytest.raises(InvalidConfig) as error:
        env.service.update_config(1, None, "ZZ", None, [])

    assert error.value.field == "country"


def test_update_config_rejects_unknown_subdivision(env):
    with pytest.raises(InvalidConfig) as error:
        env.service.update_config(1, None, "FR", "67", [])

    assert error.value.field == "subdivision"


def test_update_config_rejects_unknown_excluded_key(env):
    with pytest.raises(InvalidConfig) as error:
        env.service.update_config(1, None, "NL", None, ["B", "Nope"])

    assert error.value.field == "excluded"
    assert "Nope" in str(error.value)


def test_update_config_accepts_a_key_that_only_exists_some_years(real_env):
    # Liberation Day is a public holiday in NL only every five years.
    real_env.service.update_config(1, None, "NL", None, ["Liberation Day"])


def test_update_config_accepts_real_calendar_keys(real_env):
    config = real_env.service.update_config(1, None, "FR", "6AE", ["Good Friday"])

    assert config.excluded == ["Good Friday"]


def test_update_config_does_not_write_anything_when_invalid(env):
    with pytest.raises(InvalidConfig):
        env.service.update_config(1, None, "ZZ", None, [])

    assert env.storage.list_schedule_ids() == []


def test_sync_adds_closed_periods_with_the_schedule_destination(env):
    env.configure()

    result = env.service.sync(1, None)

    assert env.days() == [(3, 29), (11, 18), (12, 25)]
    for period in env.schedule.exceptional_periods:
        assert (period.hours_start, period.hours_end) == ("00:00", "23:59")
        assert period.week_days == [1, 2, 3, 4, 5, 6, 7]
        assert (period.action, period.actionid, period.actionargs) == (
            "voicemail",
            "12",
            None,
        )
    assert env.schedules.edits == [(1, ["exceptional_periods"])]
    assert result.changed and not result.dry_run
    assert [entry.key for entry in result.added] == ["B", "C", "D"]
    assert result.removed == []
    assert env.storage.load(1, TENANT).managed == result.managed


def test_sync_is_idempotent_and_does_not_write_when_nothing_changes(env):
    env.configure()
    env.service.sync(1, None)

    again = env.service.sync(1, None)

    assert not again.changed
    assert again.added == [] and again.removed == []
    assert len(env.schedules.edits) == 1


def test_dry_run_changes_nothing(env):
    env.configure()
    before = env.storage.path(1).read_text()

    result = env.service.sync(1, None, dry_run=True)

    assert result.dry_run and result.changed
    assert [entry.key for entry in result.added] == ["B", "C", "D"]
    assert env.schedule.exceptional_periods == []
    assert env.schedules.edits == []
    assert env.storage.path(1).read_text() == before


def test_sync_keeps_manual_periods_unchanged(env):
    env.schedule.exceptional_periods = [manual_period()]
    env.configure()

    env.service.sync(1, None)

    kept = [p for p in env.schedule.exceptional_periods if p.month_days == [5]]
    assert len(kept) == 1
    period = kept[0]
    assert (period.hours_start, period.hours_end) == ("08:00", "12:00")
    assert period.week_days == [1, 2]
    assert period.months_list == [6]
    assert (period.action, period.actionid) == ("user", "3")
    assert period.mode == "closed"
    assert len(env.schedule.exceptional_periods) == 4


def test_sync_removes_managed_periods_that_left_the_window(env):
    env.configure()
    env.service.sync(1, None)
    env.table[2026] = [Holiday("C", "C", D(2026, 12, 25))]
    env.service.sync(1, None)

    assert env.days() == [(3, 29), (12, 25)]


def test_sync_applies_excluded_keys(env):
    env.configure(excluded=["C"])

    env.service.sync(1, None)

    assert env.days() == [(3, 29), (11, 18)]


def test_sync_propagates_a_destination_change(env):
    env.configure()
    env.service.sync(1, None)
    env.schedule.fallback_action = "user"
    env.schedule.fallback_actionid = "7"

    result = env.service.sync(1, None)

    assert result.changed
    assert {(p.action, p.actionid) for p in env.schedule.exceptional_periods} == {
        ("user", "7")
    }
    assert len(env.schedule.exceptional_periods) == 3


def test_sync_uses_the_schedule_timezone_then_the_default_one(tmp_path):
    with_tz = Env(tmp_path / "a", schedule=make_schedule(timezone="Asia/Tokyo"))
    with_tz.configure()
    with_tz.service.sync(1, None)
    without_tz = Env(tmp_path / "b", schedule=make_schedule(timezone=None))
    without_tz.configure()
    without_tz.service.sync(1, None)

    assert with_tz.today_calls == ["Asia/Tokyo"]
    assert with_tz.default_tz_calls == 0
    assert without_tz.today_calls == ["UTC"]
    assert without_tz.default_tz_calls == 1


def test_sync_without_configuration(env):
    with pytest.raises(ConfigNotFound):
        env.service.sync(1, None)


def test_sync_of_unknown_schedule(env):
    with pytest.raises(NotFound):
        env.service.sync(99, None)


def test_sync_while_the_schedule_is_locked(env):
    env.configure()

    with env.storage.lock(1):
        with pytest.raises(SyncInProgress):
            env.service.sync(1, None)
        with pytest.raises(SyncInProgress):
            env.service.update_config(1, None, "NL", None, [])
        with pytest.raises(SyncInProgress):
            env.service.delete_config(1, None)


def test_failed_schedule_edit_leaves_the_intent_and_the_next_sync_recovers(tmp_path):
    env = Env(tmp_path, fail_edit=RuntimeError("db down"))
    env.configure()

    with pytest.raises(RuntimeError):
        env.service.sync(1, None)
    intent = env.storage.load(1, TENANT).managed
    env.schedules.fail_edit = None
    env.service.sync(1, None)

    assert [entry.key for entry in intent] == ["B", "C", "D"]
    assert env.days() == [(3, 29), (11, 18), (12, 25)]
    assert len(env.schedule.exceptional_periods) == 3


def test_failed_final_save_does_not_create_orphans(tmp_path):
    env = Env(tmp_path)
    env.configure()
    real_save = env.storage.save
    calls = []

    def flaky_save(schedule_id, tenant_uuid, config):
        calls.append(config)
        if len(calls) == 2:
            raise OSError("disk full")
        real_save(schedule_id, tenant_uuid, config)

    env.storage.save = flaky_save
    with pytest.raises(OSError):
        env.service.sync(1, None)
    env.storage.save = real_save
    env.service.sync(1, None)

    assert len(env.schedule.exceptional_periods) == 3
    assert env.days() == [(3, 29), (11, 18), (12, 25)]


def test_removal_still_works_after_an_interrupted_sync(tmp_path):
    env = Env(tmp_path, fail_edit=RuntimeError("db down"))
    env.configure()
    with pytest.raises(RuntimeError):
        env.service.sync(1, None)
    env.schedules.fail_edit = None
    env.service.sync(1, None)

    env.service.delete_config(1, None)

    assert env.schedule.exceptional_periods == []


def test_delete_config_removes_managed_periods_only(env):
    env.schedule.exceptional_periods = [manual_period()]
    env.configure()
    env.service.sync(1, None)

    env.service.delete_config(1, None)

    assert env.days() == [(6, 5)]
    assert env.storage.load(1, TENANT) is None
    assert env.storage.list_schedule_ids() == []


def test_delete_config_without_configuration(env):
    with pytest.raises(ConfigNotFound):
        env.service.delete_config(1, None)


def test_delete_config_of_a_schedule_without_periods_to_remove_skips_the_edit(env):
    env.configure()

    env.service.delete_config(1, None)

    assert env.schedules.edits == []
    assert env.storage.load(1, TENANT) is None


def test_real_calendar_closes_dutch_easter_monday(real_env):
    real_env.configure("NL")

    real_env.service.sync(1, None)

    assert (3, 29) in real_env.days()
    assert (12, 25) in real_env.days()


def test_commit_happens_between_the_schedule_edit_and_the_final_file_write(env):
    env.configure()
    log = []
    real_save = env.storage.save
    real_edit = env.schedules.edit
    env.storage.save = lambda *args: (log.append("save"), real_save(*args))
    env.schedules.edit = lambda *args, **kw: (
        log.append("edit"),
        real_edit(*args, **kw),
    )
    env.service._commit = lambda: log.append("commit")

    env.service.sync(1, None)

    assert log == ["save", "edit", "commit", "save"]


def test_failed_commit_keeps_the_intent_and_the_next_sync_recovers(env):
    env.configure()
    env.fail_commit = RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        env.service.sync(1, None)
    intent = env.storage.load(1, TENANT).managed
    env.fail_commit = None
    env.service.sync(1, None)

    assert [entry.key for entry in intent] == ["B", "C", "D"]
    assert len(env.schedule.exceptional_periods) == 3


def test_failed_commit_on_removal_keeps_the_removed_dates_managed(env):
    env.configure()
    env.service.sync(1, None)
    env.table[2026] = [Holiday("C", "C", D(2026, 12, 25))]
    env.fail_commit = RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        env.service.sync(1, None)
    still_managed = {
        (entry.date.month, entry.date.day)
        for entry in env.storage.load(1, TENANT).managed
    }
    env.fail_commit = None
    env.service.sync(1, None)

    assert (11, 18) in still_managed
    assert env.days() == [(3, 29), (12, 25)]


def test_delete_config_commits_before_removing_the_file(env):
    env.configure()
    env.service.sync(1, None)
    env.fail_commit = RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        env.service.delete_config(1, None)

    assert env.storage.load(1, TENANT) is not None


def test_no_commit_when_the_periods_do_not_change(env):
    env.configure()
    env.service.sync(1, None)
    commits = env.commits

    env.service.sync(1, None)

    assert env.commits == commits


def closure(start, end, label=None):
    return Closure(
        start=datetime.datetime(*start), end=datetime.datetime(*end), label=label
    )


SEMINAR = closure((2026, 11, 19, 12, 0), (2026, 11, 20, 14, 0), "Seminar")


def hours(env):
    return sorted(
        (p.months_list[0], p.month_days[0], p.hours_start, p.hours_end)
        for p in env.schedule.exceptional_periods
    )


def test_update_config_stores_closures(env):
    config = env.service.update_config(1, None, "NL", None, [], [SEMINAR])

    assert config.closures == [SEMINAR]
    assert env.service.get_config(1, None).closures == [SEMINAR]


def test_update_config_without_closures_clears_them(env):
    env.service.update_config(1, None, "NL", None, [], [SEMINAR])

    assert env.service.update_config(1, None, "NL", None, []).closures == []


def test_sync_creates_one_period_per_day_of_a_closure(env):
    env.service.update_config(1, None, "NL", None, [], [SEMINAR])

    result = env.service.sync(1, None)
    again = env.service.sync(1, None)

    assert hours(env) == sorted(
        [
            (3, 29, "00:00", "23:59"),
            (11, 18, "00:00", "23:59"),
            (11, 19, "12:00", "23:59"),
            (11, 20, "00:00", "14:00"),
            (12, 25, "00:00", "23:59"),
        ]
    )
    assert result.removed == []
    assert not again.changed


def test_closure_periods_disappear_once_past(env):
    env.service.update_config(1, None, "NL", None, [], [SEMINAR])
    env.service.sync(1, None)
    env.today = D(2026, 11, 21)

    result = env.service.sync(1, None)

    assert (11, 19, "12:00", "23:59") in result.removed
    assert (11, 20, "00:00", "14:00") in result.removed
    assert all(entry[:2] not in {(11, 19), (11, 20)} for entry in hours(env))


def test_removing_a_closure_removes_its_periods(env):
    env.service.update_config(1, None, "NL", None, [], [SEMINAR])
    env.service.sync(1, None)
    env.service.update_config(1, None, "NL", None, [])

    env.service.sync(1, None)

    assert env.days() == [(3, 29), (11, 18), (12, 25)]


def test_manual_periods_survive_closures_and_config_deletion(env):
    manual = manual_period(month_days=[19], months_list=[11])
    env.schedule.exceptional_periods = [manual]
    env.service.update_config(1, None, "NL", None, [], [SEMINAR])

    env.service.sync(1, None)
    env.service.delete_config(1, None)

    assert [
        (p.month_days, p.hours_start) for p in env.schedule.exceptional_periods
    ] == [([19], "08:00")]


@pytest.mark.parametrize(
    "bad",
    [
        closure((2026, 11, 20, 14, 0), (2026, 11, 19, 12, 0)),
        closure((2026, 11, 20, 14, 0), (2026, 11, 20, 14, 0)),
        Closure(
            datetime.datetime(2026, 11, 20, 14, 0, 30), datetime.datetime(2026, 11, 21)
        ),
        Closure(
            datetime.datetime(2026, 11, 20, tzinfo=datetime.UTC),
            datetime.datetime(2026, 11, 21, tzinfo=datetime.UTC),
        ),
        closure((2026, 1, 1, 0, 0), (2027, 1, 2, 0, 0)),
    ],
)
def test_invalid_closures_are_rejected(env, bad):
    with pytest.raises(InvalidConfig) as error:
        env.service.update_config(1, None, "NL", None, [], [bad])

    assert error.value.field == "closures"
    assert env.storage.list_schedule_ids() == []


def test_duplicate_and_too_many_closures_are_rejected(env):
    with pytest.raises(InvalidConfig):
        env.service.update_config(1, None, "NL", None, [], [SEMINAR, SEMINAR])
    many = [
        closure((2026, 12, 1, 9, 0), (2026, 12, 1, 9, minute))
        for minute in range(1, 60)
    ] + [
        closure((2026, 12, 2, 9, 0), (2026, 12, 2, 9, minute))
        for minute in range(1, 60)
    ]
    with pytest.raises(InvalidConfig):
        env.service.update_config(1, None, "NL", None, [], many)
