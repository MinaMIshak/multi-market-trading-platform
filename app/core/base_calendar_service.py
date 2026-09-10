from __future__ import annotations

from datetime import date

from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)


class BaseCalendarSessionService:
    """
    Persist deterministic WEEKEND sessions only
    when no market-session evidence already exists.

    Existing rows are never overwritten by the
    lower-authority weekly calendar.
    """

    def __init__(
        self,
        *,
        trading_repository,
        policy: BaseTradingCalendarPolicy,
    ) -> None:
        self.trading_repository = (
            trading_repository
        )
        self.policy = policy

    def apply(
        self,
        market_date: date,
    ) -> MarketSessionStatus:
        status = self.policy.classify(
            market_date
        )

        if status != MarketSessionStatus.WEEKEND:
            return status

        existing = (
            self.trading_repository
            .get_market_session(
                market_date
            )
        )

        if existing is not None:
            return existing.status

        self.trading_repository.save_market_session(
            MarketSession(
                market_date=market_date,
                status=MarketSessionStatus.WEEKEND,
            )
        )

        return MarketSessionStatus.WEEKEND
