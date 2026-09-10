from datetime import date, datetime, timezone

import pytest

from app.core.calendar_verification import (
    CalendarVerificationDecision,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.core.trading_day_promotion import (
    TradingDayPromotionPolicy,
)
from app.domain import MarketSession
from app.domain.enums import MarketSessionStatus
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionResult,
)


DAY = date(2026, 9, 10)
NOW = datetime(
    2026, 9, 10, 12, 0,
    tzinfo=timezone.utc,
)


class FakeEvidenceRepository:
    def load(self, market_date):
        return ["evidence"]


class FakePolicy:
    def __init__(self, status):
        self.status = status

    def evaluate(self, *, market_date, evidence):
        return CalendarVerificationDecision(
            market_date=market_date,
            status=self.status,
            reasons=("test",),
        )


class FakeTradingRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def get_market_session(self, market_date):
        self.calls.append(market_date)
        return self.existing


class FakeTransitionRepository:
    def __init__(
        self,
        result=MarketSessionTransitionResult.CREATED,
    ):
        self.result = result
        self.calls = []

    def compare_and_promote(
        self,
        session,
        *,
        replaceable_statuses=(),
    ):
        self.calls.append(
            (session, set(replaceable_statuses))
        )
        return self.result


def build(
    status,
    *,
    existing=None,
    transition=MarketSessionTransitionResult.CREATED,
):
    trading = FakeTradingRepository(existing)
    transitions = FakeTransitionRepository(
        transition
    )

    service = CalendarVerificationService(
        evidence_repository=FakeEvidenceRepository(),
        trading_repository=trading,
        policy=FakePolicy(status),
        promotion_policy=TradingDayPromotionPolicy(),
        transition_repository=transitions,
    )

    return service, trading, transitions


def test_verified_absent_is_atomically_created():
    service, trading, transitions = build(
        MarketSessionStatus.VERIFIED
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert decision.status == MarketSessionStatus.VERIFIED
    assert trading.calls == [DAY]
    assert len(transitions.calls) == 1

    session, replaceable = transitions.calls[0]

    assert session.status == MarketSessionStatus.VERIFIED
    assert session.data_verified_at == NOW
    assert replaceable == set()


def test_unknown_decision_writes_nothing():
    service, trading, transitions = build(
        MarketSessionStatus.UNKNOWN
    )

    decision = service.verify(DAY)

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert trading.calls == []
    assert transitions.calls == []


def test_non_verified_does_not_validate_timestamp():
    service, _, transitions = build(
        MarketSessionStatus.UNKNOWN
    )

    decision = service.verify(
        DAY,
        verified_at=datetime(2026, 9, 10, 12, 0),
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert transitions.calls == []


def test_naive_verified_timestamp_rejected():
    service, _, transitions = build(
        MarketSessionStatus.VERIFIED
    )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        service.verify(
            DAY,
            verified_at=datetime(
                2026, 9, 10, 12, 0
            ),
        )

    assert transitions.calls == []


def test_existing_holiday_fails_closed():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.HOLIDAY,
    )

    service, _, transitions = build(
        MarketSessionStatus.VERIFIED,
        existing=existing,
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert (
        "market_session_state_conflict"
        in decision.reasons
    )
    assert transitions.calls == []


def test_existing_verified_is_idempotent():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.VERIFIED,
        data_verified_at=NOW,
    )

    service, _, transitions = build(
        MarketSessionStatus.VERIFIED,
        existing=existing,
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert decision.status == MarketSessionStatus.VERIFIED
    assert transitions.calls == []


def test_lifecycle_timestamps_are_preserved():
    opened = datetime(
        2026, 9, 10, 7, 0,
        tzinfo=timezone.utc,
    )
    closed = datetime(
        2026, 9, 10, 11, 30,
        tzinfo=timezone.utc,
    )

    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.CLOSED,
        opened_at=opened,
        closed_at=closed,
    )

    service, _, transitions = build(
        MarketSessionStatus.VERIFIED,
        existing=existing,
        transition=(
            MarketSessionTransitionResult.REPLACED
        ),
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert decision.status == MarketSessionStatus.VERIFIED

    session, replaceable = transitions.calls[0]

    assert session.opened_at == opened
    assert session.closed_at == closed
    assert session.data_verified_at == NOW
    assert replaceable == {
        MarketSessionStatus.CLOSED
    }


def test_atomic_race_conflict_fails_closed():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.OPEN,
    )

    service, _, transitions = build(
        MarketSessionStatus.VERIFIED,
        existing=existing,
        transition=(
            MarketSessionTransitionResult.CONFLICT
        ),
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert (
        "market_session_transition_conflict"
        in decision.reasons
    )

    _, replaceable = transitions.calls[0]

    assert replaceable == {
        MarketSessionStatus.OPEN
    }
