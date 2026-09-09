from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.calendar_verification import (
    CalendarVerificationDecision,
    CalendarVerificationPolicy,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)


class CalendarVerificationService:
    """
    Promote a market date to VERIFIED only when
    the official evidence policy confirms it.

    UNKNOWN decisions are never persisted.
    This service does not infer holidays.
    """

    def __init__(
        self,
        *,
        evidence_repository: Any,
        trading_repository: Any,
        policy: CalendarVerificationPolicy,
    ) -> None:
        self.evidence_repository = (
            evidence_repository
        )
        self.trading_repository = (
            trading_repository
        )
        self.policy = policy

    def verify(
        self,
        market_date: date,
        *,
        verified_at: datetime | None = None,
    ) -> CalendarVerificationDecision:
        evidence = (
            self.evidence_repository
            .load(market_date)
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

        if timestamp.tzinfo is None:
            raise ValueError(
                "verified_at must be "
                "timezone-aware"
            )

        session = MarketSession(
            market_date=market_date,
            status=MarketSessionStatus.VERIFIED,
            data_verified_at=timestamp,
        )

        self.trading_repository.save_market_session(
            session
        )

        return decision
