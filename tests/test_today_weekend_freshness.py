from datetime import date

from app.core.schedule import CalendarTruth
from app.ui.today import _daily_freshness

THU, FRI, SAT, SUN, MON = (date(2026, 10, d) for d in (1, 2, 3, 4, 5))


def test_friday_saturday_are_non_trading_by_rule_when_no_row_exists():
    assert _daily_freshness(THU, SAT, {}) == "CURRENT"
    assert _daily_freshness(THU, SUN, {}) == "CURRENT"


def test_unverified_sunday_to_thursday_stays_unknown():
    assert _daily_freshness(THU, MON, {}) == "UNKNOWN"
    assert _daily_freshness(date(2026, 9, 29), THU, {}) == "UNKNOWN"


def test_verified_trading_day_in_gap_is_stale_and_stored_rows_win():
    assert _daily_freshness(date(2026, 9, 29), THU, {date(2026, 9, 30): CalendarTruth.VERIFIED_TRADING_DAY}) == "STALE"
    assert _daily_freshness(THU, MON, {SUN: CalendarTruth.VERIFIED_TRADING_DAY}) == "STALE"
    assert _daily_freshness(THU, MON, {SUN: CalendarTruth.VERIFIED_NON_TRADING_DAY}) == "CURRENT"
    assert _daily_freshness(FRI, THU, {}) == "UNKNOWN"
