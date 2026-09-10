from __future__ import annotations

from datetime import date
from typing import Any

from app.core.schedule import CalendarTruth
from app.domain.enums import (
    MarketSessionStatus,
)


class CalendarTruthResolver:
    """
    Resolve scheduler calendar truth from
    persisted market-session evidence.

    Only explicit VERIFIED, HOLIDAY, or WEEKEND
    states are authoritative. Everything else
    remains UNVERIFIED.
    """

    def __init__(
        self,
        repository: Any,
    ) -> None:
        self.repository = repository

    def resolve(
        self,
        market_date: date,
    ) -> CalendarTruth:
        session = (
            self.repository
            .get_market_session(
                market_date
            )
        )

        if session is None:
            return CalendarTruth.UNVERIFIED

        if (
            session.status
            == MarketSessionStatus.VERIFIED
        ):
            return (
                CalendarTruth
                .VERIFIED_TRADING_DAY
            )

        if session.status in (
            MarketSessionStatus.HOLIDAY,
            MarketSessionStatus.WEEKEND,
        ):
            return (
                CalendarTruth
                .VERIFIED_NON_TRADING_DAY
            )

        return CalendarTruth.UNVERIFIED
