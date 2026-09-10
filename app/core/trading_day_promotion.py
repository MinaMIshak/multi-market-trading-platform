from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import (
    MarketSessionStatus,
)


class TradingDayPromotionAction(StrEnum):
    CREATE = "CREATE"
    REPLACE = "REPLACE"
    NOOP = "NOOP"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class TradingDayPromotionDecision:
    action: TradingDayPromotionAction
    target_status: MarketSessionStatus | None
    reason: str


class TradingDayPromotionPolicy:
    """
    Decide whether verified trading-day evidence
    may change persisted market-session state.

    No persistence or network operations.
    """

    REPLACEABLE = frozenset(
        {
            MarketSessionStatus.UNKNOWN,
            MarketSessionStatus.WEEKEND,
            MarketSessionStatus.PRE_MARKET,
            MarketSessionStatus.OPEN,
            MarketSessionStatus.FIRST15_COMPLETE,
            MarketSessionStatus.CLOSED,
            MarketSessionStatus.DATA_PENDING,
        }
    )

    def evaluate(
        self,
        *,
        verified_status: MarketSessionStatus,
        existing_status: (
            MarketSessionStatus | None
        ),
    ) -> TradingDayPromotionDecision:
        if (
            verified_status
            != MarketSessionStatus.VERIFIED
        ):
            return TradingDayPromotionDecision(
                action=TradingDayPromotionAction.NOOP,
                target_status=None,
                reason="trading_day_not_verified",
            )

        if existing_status is None:
            return TradingDayPromotionDecision(
                action=TradingDayPromotionAction.CREATE,
                target_status=(
                    MarketSessionStatus.VERIFIED
                ),
                reason="verified_trading_day_new_session",
            )

        if (
            existing_status
            == MarketSessionStatus.VERIFIED
        ):
            return TradingDayPromotionDecision(
                action=TradingDayPromotionAction.NOOP,
                target_status=(
                    MarketSessionStatus.VERIFIED
                ),
                reason="trading_day_already_verified",
            )

        if existing_status in self.REPLACEABLE:
            return TradingDayPromotionDecision(
                action=TradingDayPromotionAction.REPLACE,
                target_status=(
                    MarketSessionStatus.VERIFIED
                ),
                reason=(
                    "verified_trading_day_supersedes_"
                    "lower_authority_state"
                ),
            )

        return TradingDayPromotionDecision(
            action=TradingDayPromotionAction.CONFLICT,
            target_status=(
                MarketSessionStatus.VERIFIED
            ),
            reason=(
                "verified_trading_day_conflicts_with_"
                "existing_session"
            ),
        )
