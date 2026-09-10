from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.calendar_verification import (
    CalendarVerificationDecision,
    CalendarVerificationPolicy,
)
from app.core.trading_day_promotion import (
    TradingDayPromotionAction,
    TradingDayPromotionPolicy,
)
from app.domain import MarketSession
from app.domain.enums import MarketSessionStatus
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
    MarketSessionTransitionResult,
)


class CalendarVerificationService:
    """
    Promote VERIFIED trading days through an
    atomic compare-and-promote transition.

    Authoritative conflicts fail closed and
    existing lifecycle timestamps are preserved.
    """

    def __init__(
        self,
        *,
        evidence_repository: Any,
        trading_repository: Any,
        policy: CalendarVerificationPolicy,
        promotion_policy: (
            TradingDayPromotionPolicy | None
        ) = None,
        transition_repository: (
            MarketSessionTransitionRepository | None
        ) = None,
    ) -> None:
        self.evidence_repository = evidence_repository
        self.trading_repository = trading_repository
        self.policy = policy
        self.promotion_policy = (
            promotion_policy
            or TradingDayPromotionPolicy()
        )

        if transition_repository is not None:
            self.transition_repository = (
                transition_repository
            )
        else:
            self.transition_repository = (
                MarketSessionTransitionRepository(
                    trading_repository.database
                )
            )

    @staticmethod
    def _fail_closed(
        decision: CalendarVerificationDecision,
        reason: str,
    ) -> CalendarVerificationDecision:
        return CalendarVerificationDecision(
            market_date=decision.market_date,
            status=MarketSessionStatus.UNKNOWN,
            reasons=decision.reasons + (reason,),
        )

    def verify(
        self,
        market_date: date,
        *,
        verified_at: datetime | None = None,
    ) -> CalendarVerificationDecision:
        evidence = self.evidence_repository.load(
            market_date
        )

        decision = self.policy.evaluate(
            market_date=market_date,
            evidence=evidence,
        )

        if (
            decision.status
            != MarketSessionStatus.VERIFIED
        ):
            return decision

        timestamp = (
            verified_at
            if verified_at is not None
            else datetime.now(timezone.utc)
        )

        if (
            timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError(
                "verified_at must be timezone-aware"
            )

        existing = (
            self.trading_repository
            .get_market_session(market_date)
        )

        existing_status = (
            existing.status
            if existing is not None
            else None
        )

        promotion = self.promotion_policy.evaluate(
            verified_status=decision.status,
            existing_status=existing_status,
        )

        if (
            promotion.action
            == TradingDayPromotionAction.CONFLICT
        ):
            return self._fail_closed(
                decision,
                "market_session_state_conflict",
            )

        if (
            promotion.action
            == TradingDayPromotionAction.NOOP
        ):
            return decision

        if existing is None:
            session = MarketSession(
                market_date=market_date,
                status=MarketSessionStatus.VERIFIED,
                data_verified_at=timestamp,
            )
            replaceable = frozenset()
        else:
            session = existing.model_copy(
                update={
                    "status": (
                        MarketSessionStatus.VERIFIED
                    ),
                    "data_verified_at": timestamp,
                }
            )

            # Exact compare-and-swap. Do not use the
            # whole policy replaceable set here:
            # stale lifecycle payload must not win.
            replaceable = frozenset(
                {existing.status}
            )

        transition = (
            self.transition_repository
            .compare_and_promote(
                session,
                replaceable_statuses=replaceable,
            )
        )

        if (
            transition
            == MarketSessionTransitionResult.CONFLICT
        ):
            return self._fail_closed(
                decision,
                "market_session_transition_conflict",
            )

        return decision
