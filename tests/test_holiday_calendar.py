# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime

import pytest

from wazo_schedule_holidays import holiday_calendar as cal
from wazo_schedule_holidays.holiday_calendar import (
    Holiday,
    UnknownCountry,
    UnknownSubdivision,
)


def by_key(holidays):
    return {holiday.key: holiday for holiday in holidays}


def test_list_countries_is_sorted_and_contains_nl_and_fr():
    countries = cal.list_countries()

    assert countries == sorted(countries)
    assert {"NL", "FR"} <= set(countries)


def test_list_subdivisions_fr_has_alsace_and_moselle_without_school_zones():
    subdivisions = cal.list_subdivisions("FR")

    assert {"6AE", "57"} <= set(subdivisions)
    assert not {"Zone A", "Zone B", "Zone C"} & set(subdivisions)


def test_list_subdivisions_nl_hides_school_regions():
    assert cal.list_subdivisions("NL") == []


def test_list_subdivisions_unknown_country():
    with pytest.raises(UnknownCountry):
        cal.list_subdivisions("ZZ")


def test_easter_2026_is_april_5th_and_monday_the_6th():
    nl = by_key(cal.holidays_for("NL", None, 2026))
    fr = by_key(cal.holidays_for("FR", None, 2026))

    assert nl["Easter Sunday"].date == datetime.date(2026, 4, 5)
    assert fr["Easter Monday"].date == datetime.date(2026, 4, 6)


def test_nl_2027_easter_monday_with_dutch_name():
    holidays = by_key(cal.holidays_for("NL", None, 2027, language="nl"))

    assert holidays["Easter Monday"] == Holiday(
        key="Easter Monday", name="Tweede paasdag", date=datetime.date(2027, 3, 29)
    )


def test_fr_french_names():
    holidays = by_key(cal.holidays_for("FR", None, 2027, language="fr"))

    assert holidays["Easter Monday"].name == "Lundi de Pâques"


def test_default_language_is_english():
    holidays = by_key(cal.holidays_for("FR", None, 2027))

    assert holidays["Easter Monday"].name == "Easter Monday"


def test_unsupported_language_falls_back_to_country_default_for_names_only():
    holidays = by_key(cal.holidays_for("NL", None, 2027, language="xx"))

    assert holidays["Easter Monday"].name == "Tweede paasdag"


def test_alsace_and_moselle_have_good_friday_and_saint_stephen():
    national = by_key(cal.holidays_for("FR", None, 2027))
    alsace = by_key(cal.holidays_for("FR", "6AE", 2027))
    moselle = by_key(cal.holidays_for("FR", "57", 2027))

    assert "Good Friday" not in national
    assert alsace["Good Friday"].date == datetime.date(2027, 3, 26)
    assert moselle["Saint Stephen's Day"].date == datetime.date(2027, 12, 26)


def test_results_are_sorted_by_date_and_stay_in_the_requested_year():
    holidays = cal.holidays_for("FR", "6AE", 2027)

    dates = [holiday.date for holiday in holidays]
    assert dates == sorted(dates)
    assert {date.year for date in dates} == {2027}


def test_duplicate_names_in_a_year_get_a_rank_suffix():
    holidays = cal.holidays_for("AD", "06", 2026)

    keys = [holiday.key for holiday in holidays]
    assert len(keys) == len(set(keys))
    suffixed = [key for key in keys if key.endswith(("#1", "#2"))]
    assert suffixed
    assert all(
        key.removesuffix(" #1").removesuffix(" #2") == "Sant Julià de Lòria Festival"
        for key in suffixed
    )


def test_unique_names_are_not_suffixed():
    keys = [holiday.key for holiday in cal.holidays_for("NL", None, 2027)]

    assert "Easter Monday" in keys
    assert not any("#" in key for key in keys)


def test_unknown_country():
    with pytest.raises(UnknownCountry):
        cal.holidays_for("ZZ", None, 2027)


def test_unknown_subdivision():
    with pytest.raises(UnknownSubdivision):
        cal.holidays_for("FR", "67", 2027)


def test_school_zone_is_not_a_valid_subdivision():
    with pytest.raises(UnknownSubdivision):
        cal.holidays_for("FR", "Zone A", 2027)


def test_subdivision_names_use_the_longest_alias():
    names = cal.subdivision_names("FR")

    assert names["6AE"] == "Alsace"
    assert names["57"] == "Moselle"
    assert names["971"] == "Guadeloupe"
    assert set(names) == set(cal.list_subdivisions("FR"))


def test_subdivision_names_unknown_country():
    with pytest.raises(UnknownCountry):
        cal.subdivision_names("ZZ")
