from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import (
    MarketSessionStatus,
)


class HolidayPromotionAction(StrEnum):
    CREATE = "CREATE"
    REPLACE = "REPLACE"
    NOOP = "NOOP"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class HolidayPromotionDecision:
    action: HolidayPromotionAction
    target_status: MarketSessionStatus | None
    reason: str


class HolidayPromotionPolicy:
    """
    Decide whether verified holiday evidence may
    change the persisted market-session state.

    This policy performs no persistence.
    """

    def evaluate(
        self,
        *,
        holiday_status: MarketSessionStatus,
        existing_status: (
            MarketSessionStatus | None
        ),
    ) -> HolidayPromotionDecision:
        if (
            holiday_status
            != MarketSessionStatus.HOLIDAY
        ):
            return HolidayPromotionDecision(
                action=HolidayPromotionAction.NOOP,
                target_status=None,
                reason="holiday_not_verified",
            )

        if existing_status is None:
            return HolidayPromotionDecision(
                action=HolidayPromotionAction.CREATE,
                target_status=(
                    MarketSessionStatus.HOLIDAY
                ),
                reason="verified_holiday_new_session",
            )

        if (
            existing_status
            == MarketSessionStatus.HOLIDAY
        ):
            return HolidayPromotionDecision(
                action=HolidayPromotionAction.NOOP,
                target_status=(
                    MarketSessionStatus.HOLIDAY
                ),
                reason="holiday_already_persisted",
            )

        if existing_status in (
            MarketSessionStatus.WEEKEND,
            MarketSessionStatus.UNKNOWN,
        ):
            return HolidayPromotionDecision(
                action=HolidayPromotionAction.REPLACE,
                target_status=(
                    MarketSessionStatus.HOLIDAY
                ),
                reason=(
                    "verified_holiday_supersedes_"
                    "lower_authority_state"
                ),
            )

        return HolidayPromotionDecision(
            action=HolidayPromotionAction.CONFLICT,
            target_status=(
                MarketSessionStatus.HOLIDAY
            ),
            reason=(
                "verified_holiday_conflicts_with_"
                "existing_session"
            ),
        )
