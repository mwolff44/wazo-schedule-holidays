# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Public holidays of a country (and optional subdivision) for a given year.

Holidays are identified by a *key*: their ``en_US`` name, which does not depend
on the display language. If a name occurs several times in the same year (it
happens for some subdivisions), the key gets a rank suffix by date (``#1``,
``#2``). The display name follows the requested language.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass

from ._vendor import holidays_module

KEY_LANGUAGE = "en_US"

# Subdivisions that only exist for school holidays: they do not change public
# holidays, so offering them would be misleading.
IGNORED_SUBDIVISIONS: dict[str, frozenset[str]] = {
    "FR": frozenset({"Zone A", "Zone B", "Zone C"}),
    "NL": frozenset({"Midden", "Noord", "Zuid"}),
}


class UnknownCountry(ValueError):
    pass


class UnknownSubdivision(ValueError):
    pass


@dataclass(frozen=True)
class Holiday:
    key: str
    name: str
    date: datetime.date


def _supported() -> dict[str, list[str]]:
    return holidays_module().list_supported_countries(include_aliases=False)


def list_countries() -> list[str]:
    return sorted(_supported())


def list_subdivisions(country: str) -> list[str]:
    supported = _supported()
    if country not in supported:
        raise UnknownCountry(country)
    ignored = IGNORED_SUBDIVISIONS.get(country, frozenset())
    return [sub for sub in supported[country] if sub not in ignored]


def holidays_for(
    country: str,
    subdivision: str | None,
    year: int,
    language: str = KEY_LANGUAGE,
) -> list[Holiday]:
    subdivisions = list_subdivisions(country)  # raises UnknownCountry
    if subdivision is not None and subdivision not in subdivisions:
        raise UnknownSubdivision(subdivision)

    module = holidays_module()
    keyed = module.country_holidays(
        country, subdiv=subdivision, years=year, language=KEY_LANGUAGE
    )
    displayed = module.country_holidays(
        country,
        subdiv=subdivision,
        years=year,
        language=_display_language(module, country, language),
    )

    occurrences = []
    for date in sorted(keyed):
        key_names = keyed.get_list(date)
        display_names = displayed.get_list(date)
        if len(display_names) != len(key_names):
            display_names = key_names
        for key_name, display_name in zip(key_names, display_names):
            occurrences.append((date, key_name, display_name))

    totals = Counter(key_name for _, key_name, _ in occurrences)
    ranks: Counter[str] = Counter()
    result = []
    for date, key_name, display_name in occurrences:
        key = key_name
        if totals[key_name] > 1:
            ranks[key_name] += 1
            key = f"{key_name} #{ranks[key_name]}"
        result.append(Holiday(key=key, name=display_name, date=date))
    return result


def subdivision_names(country: str) -> dict[str, str]:
    """Display name of each subdivision code.

    The library only provides aliases (``{"Alsace": "6AE", "6AE": "6AE", ...}``):
    the longest alias of a code is used as its name, the code itself otherwise.
    """
    codes = list_subdivisions(country)
    aliases = getattr(
        holidays_module().country_holidays(country), "subdivisions_aliases", {}
    )
    names = {code: code for code in codes}
    for alias, code in aliases.items():
        if code in names and len(alias) > len(names[code]):
            names[code] = alias
    return names


def _display_language(module, country: str, language: str) -> str | None:
    """The language to give to the library, never ``None`` when avoidable.

    The library infers the language from the environment (LANGUAGE, LC_ALL,
    LC_MESSAGES, LANG) when none is given, and, as found in 0.109, also when the
    given one is not supported by the country: the names would then depend on
    the environment of the process (confd under systemd, cron, a test runner...).

    Fallback order: the requested language, then English (``KEY_LANGUAGE``, the
    default of the API), then the default language of the country.
    """
    entity = module.country_holidays(country)
    supported = entity.supported_languages
    if language in supported:
        return language
    if KEY_LANGUAGE in supported:
        return KEY_LANGUAGE
    return entity.default_language
