# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime
from types import SimpleNamespace

import pytest

from wazo_schedule_holidays.holiday_calendar import Holiday
from wazo_schedule_holidays.sync import (
    Desired,
    Destination,
    compute_desired,
    local_today,
    period_kwargs,
    plan_sync,
)

D = datetime.date
DEST = Destination("voicemail", "12", None)


def holiday(key, date):
    return Holiday(key=key, name=key, date=date)


def provider_for(table):
    def provider(country, subdivision, year):
        return table.get(year, [])

    return provider


def make_period(
    month,
    day,
    destination=DEST,
    hours=("00:00", "23:59"),
    week_days=range(1, 8),
):
    return SimpleNamespace(
        hours_start=hours[0],
        hours_end=hours[1],
        week_days=list(week_days),
        month_days=[day],
        months_list=[month],
        action=destination.action,
        actionid=destination.arg1,
        actionargs=destination.arg2,
    )


def build(desired, destination=DEST):
    return SimpleNamespace(**period_kwargs(desired, destination))


def apply(plan, existing, destination=DEST):
    kept = [p for p in existing if not any(p is r for r in plan.remove)]
    return kept + [build(desired, destination) for desired in plan.add]


TABLE = {
    2026: [
        holiday("A", D(2026, 11, 17)),
        holiday("B", D(2026, 11, 18)),
        holiday("C", D(2026, 12, 25)),
    ],
    2027: [
        holiday("D", D(2027, 11, 17)),
        holiday("E", D(2027, 11, 18)),
    ],
}


def test_window_is_today_included_and_today_plus_365_days_excluded():
    desired = compute_desired(
        "NL", None, [], D(2026, 11, 18), provider=provider_for(TABLE)
    )

    assert [d.key for d in desired] == ["B", "C", "D"]


def test_window_during_a_leap_year_still_has_each_day_at_most_once():
    table = {
        2027: [holiday("X", D(2027, 11, 18))],
        2028: [holiday("Y", D(2028, 2, 29)), holiday("Z", D(2028, 11, 17))],
    }

    desired = compute_desired(
        "NL", None, [], D(2027, 11, 18), provider=provider_for(table)
    )

    assert [d.key for d in desired] == ["X", "Y"]


def test_excluded_keys_are_removed():
    desired = compute_desired(
        "NL", None, ["C"], D(2026, 11, 18), provider=provider_for(TABLE)
    )

    assert [d.key for d in desired] == ["B", "D"]


def test_two_holidays_on_the_same_date_give_one_entry_with_first_kept_key():
    table = {
        2026: [holiday("A", D(2026, 12, 25)), holiday("B", D(2026, 12, 25))],
    }

    desired = compute_desired(
        "NL", None, ["A"], D(2026, 11, 18), provider=provider_for(table)
    )

    assert desired == [Desired(date=D(2026, 12, 25), key="B")]


def test_two_holidays_on_the_same_date_both_excluded_gives_nothing():
    table = {
        2026: [holiday("A", D(2026, 12, 25)), holiday("B", D(2026, 12, 25))],
    }

    assert (
        compute_desired(
            "NL", None, ["A", "B"], D(2026, 11, 18), provider=provider_for(table)
        )
        == []
    )


def test_result_is_sorted_by_date():
    table = {
        2026: [holiday("late", D(2026, 12, 25)), holiday("early", D(2026, 11, 20))],
    }

    desired = compute_desired(
        "NL", None, [], D(2026, 11, 18), provider=provider_for(table)
    )

    assert [d.key for d in desired] == ["early", "late"]


def test_real_calendar_nl_easter_monday_2027_from_november_2026():
    desired = compute_desired("NL", None, [], D(2026, 11, 18))

    keys = {d.key: d.date for d in desired}
    assert keys["Easter Monday"] == D(2027, 3, 29)
    assert "Christmas Day" in keys


def test_local_today_uses_the_schedule_timezone():
    now = datetime.datetime(2026, 11, 18, 23, 30, tzinfo=datetime.UTC)

    assert local_today("Europe/Amsterdam", now=now) == D(2026, 11, 19)
    assert local_today("America/New_York", now=now) == D(2026, 11, 18)


def test_local_today_without_timezone_returns_a_date():
    assert isinstance(local_today(None), datetime.date)


def desired_list(*items):
    return [Desired(date=date, key=key) for key, date in items]


def test_first_sync_adds_everything():
    desired = desired_list(("B", D(2026, 11, 18)), ("C", D(2026, 12, 25)))

    plan = plan_sync([], [], desired, DEST)

    assert plan.add == desired
    assert plan.remove == []
    assert plan.managed == desired
    assert plan.changed


def test_sync_is_idempotent():
    desired = desired_list(("B", D(2026, 11, 18)), ("C", D(2026, 12, 25)))
    first = plan_sync([], [], desired, DEST)
    existing = apply(first, [])

    second = plan_sync(existing, first.managed, desired, DEST)

    assert not second.changed
    assert second.add == [] and second.remove == []
    assert second.managed == desired


def test_past_managed_periods_are_removed():
    old = desired_list(("A", D(2026, 11, 17)), ("C", D(2026, 12, 25)))
    existing = [make_period(11, 17), make_period(12, 25)]

    plan = plan_sync(existing, old, desired_list(("C", D(2026, 12, 25))), DEST)

    assert plan.remove == [existing[0]]
    assert plan.add == []
    assert plan.managed == desired_list(("C", D(2026, 12, 25)))


def test_manual_periods_are_never_touched():
    manual = [
        make_period(12, 25, hours=("10:00", "12:00")),
        make_period(12, 25, week_days=[1, 2, 3]),
        make_period(12, 24),
    ]
    old = desired_list(("C", D(2026, 12, 25)))

    plan = plan_sync(manual, old, [], DEST)

    assert plan.remove == []
    assert plan.managed == []


def test_manual_period_identical_to_a_holiday_is_left_alone_and_not_managed():
    manual = make_period(12, 25)
    desired = desired_list(("C", D(2026, 12, 25)))

    plan = plan_sync([manual], [], desired, DEST)

    assert plan.add == []
    assert plan.remove == []
    assert plan.managed == []

    later = plan_sync([manual], plan.managed, [], DEST)
    assert later.remove == []


def test_destination_change_replaces_managed_periods():
    old = desired_list(("C", D(2026, 12, 25)))
    existing = [make_period(12, 25, destination=Destination("user", "3", None))]

    plan = plan_sync(existing, old, old, DEST)

    assert plan.remove == existing
    assert plan.add == old
    assert plan.managed == old


def test_empty_string_and_none_are_the_same_destination_argument():
    old = desired_list(("C", D(2026, 12, 25)))
    existing = [make_period(12, 25, destination=Destination("voicemail", "12", ""))]

    plan = plan_sync(existing, old, old, DEST)

    assert not plan.changed


def test_duplicate_managed_periods_are_collapsed_to_one():
    old = desired_list(("C", D(2026, 12, 25)))
    existing = [make_period(12, 25), make_period(12, 25)]

    plan = plan_sync(existing, old, old, DEST)

    assert plan.remove == [existing[1]]
    assert plan.add == []


def test_key_change_alone_is_a_change_without_period_writes():
    old = desired_list(("Old name", D(2026, 12, 25)))
    new = desired_list(("New name", D(2026, 12, 25)))

    plan = plan_sync([make_period(12, 25)], old, new, DEST)

    assert plan.remove == [] and plan.add == []
    assert plan.managed == new
    assert plan.changed


def test_nothing_to_do_is_not_a_change():
    plan = plan_sync([], [], [], DEST)

    assert not plan.changed


def test_changing_country_replaces_the_set():
    old = desired_list(("X", D(2026, 12, 26)))
    new = desired_list(("Y", D(2026, 12, 25)))
    existing = [make_period(12, 26)]

    plan = plan_sync(existing, old, new, DEST)
    after = apply(plan, existing)

    assert plan.remove == existing
    assert [(p.months_list, p.month_days) for p in after] == [([12], [25])]


def test_period_kwargs_describe_a_full_day_period_with_the_destination():
    kwargs = period_kwargs(Desired(date=D(2027, 3, 29), key="Easter Monday"), DEST)

    assert kwargs == {
        "hours_start": "00:00",
        "hours_end": "23:59",
        "week_days": [1, 2, 3, 4, 5, 6, 7],
        "month_days": [29],
        "months_list": [3],
        "action": "voicemail",
        "actionid": "12",
        "actionargs": None,
    }


def test_built_periods_are_recognised_as_managed_by_a_following_sync():
    desired = desired_list(("E", D(2027, 3, 29)))
    existing = [build(desired[0])]

    plan = plan_sync(existing, desired, desired, DEST)

    assert not plan.changed


@pytest.mark.parametrize(
    "bad",
    [
        {"hours_start": "00:01"},
        {"hours_end": "23:58"},
        {"week_days": [1, 2]},
        {"month_days": [29, 30]},
        {"months_list": [3, 4]},
    ],
)
def test_signature_requires_every_field_to_match(bad):
    desired = desired_list(("E", D(2027, 3, 29)))
    period = build(desired[0])
    for name, value in bad.items():
        setattr(period, name, value)

    plan = plan_sync([period], desired, [], DEST)

    assert plan.remove == []
