from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.holiday_promotion import (
    HolidayPromotionAction,
    HolidayPromotionDecision,
    HolidayPromotionPolicy,
)
from app.core.holiday_verification import (
    HolidayVerificationDecision,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
    MarketSessionTransitionResult,
)


@dataclass(frozen=True)
class HolidayPromotionOutcome:
    verification: HolidayVerificationDecision
    promotion: HolidayPromotionDecision
    transition: (
        MarketSessionTransitionResult | None
    )


class HolidayPromotionService:
    """
    Promote only verified holiday evidence.

    Policy decides whether promotion is allowed.
    The transition repository performs the final
    atomic compare-and-promote.

    Conflicts never overwrite existing state.
    """

    REPLACEABLE_STATUSES = frozenset(
        {
            MarketSessionStatus.WEEKEND,
            MarketSessionStatus.UNKNOWN,
        }
    )

    def __init__(
        self,
        *,
        verification_service,
        trading_repository,
        promotion_policy: HolidayPromotionPolicy,
        transition_repository:
            MarketSessionTransitionRepository,
    ) -> None:
        self.verification_service = (
            verification_service
        )
        self.trading_repository = (
            trading_repository
        )
        self.promotion_policy = (
            promotion_policy
        )
        self.transition_repository = (
            transition_repository
        )

    def promote(
        self,
        market_date: date,
    ) -> HolidayPromotionOutcome:
        verification = (
            self.verification_service
            .evaluate(market_date)
        )

        existing = (
            self.trading_repository
            .get_market_session(
                market_date
            )
        )

        existing_status = (
            existing.status
            if existing is not None
            else None
        )

        promotion = (
            self.promotion_policy.evaluate(
                holiday_status=(
                    verification.status
                ),
                existing_status=(
                    existing_status
                ),
            )
        )

        if promotion.action in (
            HolidayPromotionAction.NOOP,
            HolidayPromotionAction.CONFLICT,
        ):
            return HolidayPromotionOutcome(
                verification=verification,
                promotion=promotion,
                transition=None,
            )

        replaceable = (
            self.REPLACEABLE_STATUSES
            if (
                promotion.action
                == HolidayPromotionAction.REPLACE
            )
            else frozenset()
        )

        transition = (
            self.transition_repository
            .compare_and_promote(
                MarketSession(
                    market_date=market_date,
                    status=(
                        MarketSessionStatus.HOLIDAY
                    ),
                ),
                replaceable_statuses=(
                    replaceable
                ),
            )
        )

        return HolidayPromotionOutcome(
            verification=verification,
            promotion=promotion,
            transition=transition,
        )
