from __future__ import annotations

from datetime import date

from app.domain.enums import (
    MarketSessionStatus,
)


class BaseTradingCalendarPolicy:
    """
    Deterministic EGX weekly-calendar boundary.

    Friday and Saturday are known weekends.
    Sunday through Thursday require separate
    authoritative trading/holiday evidence.
    """

    WEEKEND_WEEKDAYS = frozenset(
        {
            4,  # Friday
            5,  # Saturday
        }
    )

    def classify(
        self,
        market_date: date,
    ) -> MarketSessionStatus:
        if (
            market_date.weekday()
            in self.WEEKEND_WEEKDAYS
        ):
            return MarketSessionStatus.WEEKEND

        return MarketSessionStatus.UNKNOWN
