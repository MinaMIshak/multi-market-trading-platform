from datetime import date

import pytest

from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.domain.enums import (
    MarketSessionStatus,
)


@pytest.mark.parametrize(
    "market_date",
    [
        date(2026, 9, 6),   # Sunday
        date(2026, 9, 7),   # Monday
        date(2026, 9, 8),   # Tuesday
        date(2026, 9, 9),   # Wednesday
        date(2026, 9, 10),  # Thursday
    ],
)
def test_sunday_through_thursday_are_unknown(
    market_date,
):
    status = (
        BaseTradingCalendarPolicy()
        .classify(market_date)
    )

    assert (
        status
        == MarketSessionStatus.UNKNOWN
    )


@pytest.mark.parametrize(
    "market_date",
    [
        date(2026, 9, 11),  # Friday
        date(2026, 9, 12),  # Saturday
    ],
)
def test_friday_and_saturday_are_weekend(
    market_date,
):
    status = (
        BaseTradingCalendarPolicy()
        .classify(market_date)
    )

    assert (
        status
        == MarketSessionStatus.WEEKEND
    )
