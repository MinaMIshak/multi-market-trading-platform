"""NYSE trading calendar from the exchange's published holiday rules.

Separate from the EGX calendar (Sunday–Thursday, Cairo); the US week is
Monday–Friday, America/New_York. Full-day holidays follow NYSE Rule 7.2:

- New Year's Day; if it falls on a Saturday it is not observed on the
  preceding Friday (NYSE does not close on 31 December). A Sunday holiday is
  observed on Monday.
- Martin Luther King Jr. Day (third Monday of January), Washington's Birthday
  (third Monday of February), Good Friday, Memorial Day (last Monday of May),
  Juneteenth (19 June, from 2022), Independence Day (4 July), Labor Day
  (first Monday of September), Thanksgiving (fourth Thursday of November),
  Christmas (25 December). Saturday holidays are observed on the Friday
  before, Sunday holidays on the Monday after.

Unscheduled closures (for example national days of mourning: 2018-12-05,
2025-01-09) are not predictable by rule. They are listed explicitly in
``UNSCHEDULED_CLOSURES``. A session is *verified* only when index bars
confirm it (see ``verified_sessions``). Early closes (13:00) do not change
daily bars and are reported only.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

UNSCHEDULED_CLOSURES = {date(2018, 12, 5): "National Day of Mourning (George H. W. Bush)",
                        date(2025, 1, 9): "National Day of Mourning (Jimmy Carter)"}


def _nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year):
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(day):
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=64)
def holidays(year: int) -> dict:
    result = {}
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        result[new_year + timedelta(days=1)] = "New Year's Day (observed)"
    elif new_year.weekday() < 5:
        result[new_year] = "New Year's Day"
    result[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    result[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    result[_easter(year) - timedelta(days=2)] = "Good Friday"
    result[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        result[_observed(date(year, 6, 19))] = "Juneteenth National Independence Day"
    result[_observed(date(year, 7, 4))] = "Independence Day"
    result[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    result[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    result[_observed(date(year, 12, 25))] = "Christmas Day"
    result.update({day: name for day, name in UNSCHEDULED_CLOSURES.items() if day.year == year})
    return result


def is_session(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def closure_reason(day: date) -> str | None:
    if day.weekday() >= 5:
        return "WEEKEND"
    return holidays(day.year).get(day)


def previous_session(day: date) -> date:
    """The last NYSE session strictly before ``day``."""
    current = day - timedelta(days=1)
    while not is_session(current):
        current -= timedelta(days=1)
    return current


def next_session(day: date) -> date:
    """The first NYSE session strictly after ``day``."""
    current = day + timedelta(days=1)
    while not is_session(current):
        current += timedelta(days=1)
    return current


def verified_sessions(index_bar_dates, start: date, end: date) -> dict:
    """Compare rule sessions with observed index bars on [start, end].

    VERIFIED_SESSION: rule session with a bar. VERIFIED_CLOSED: rule closure
    without a bar. CONFLICT_*: rule and observation disagree (reported; the
    observation wins for history, the rule never overrides evidence).
    """
    observed = set(index_bar_dates)
    result, day = {}, start
    while day <= end:
        rule, seen = is_session(day), day in observed
        result[day] = ("VERIFIED_SESSION" if rule and seen else "VERIFIED_CLOSED" if not rule and not seen
                       else "CONFLICT_BAR_ON_RULE_HOLIDAY" if seen else "CONFLICT_NO_BAR_ON_RULE_SESSION")
        day += timedelta(days=1)
    return result
