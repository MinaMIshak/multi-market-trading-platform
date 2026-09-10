from datetime import date

from app.core.holiday_promotion import (
    HolidayPromotionAction,
    HolidayPromotionPolicy,
)
from app.core.holiday_promotion_service import (
    HolidayPromotionService,
)
from app.core.holiday_verification import (
    HolidayVerificationDecision,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionResult,
)


DAY = date(2026, 7, 2)


class FakeVerificationService:
    def __init__(self, status):
        self.status = status
        self.calls = []

    def evaluate(self, market_date):
        self.calls.append(market_date)

        return HolidayVerificationDecision(
            market_date=market_date,
            status=self.status,
            reasons=("test",),
        )


class FakeTradingRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def get_market_session(
        self,
        market_date,
    ):
        self.calls.append(market_date)
        return self.existing


class FakeTransitionRepository:
    def __init__(
        self,
        result=(
            MarketSessionTransitionResult.CREATED
        ),
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
            (
                session,
                set(replaceable_statuses),
            )
        )
        return self.result


def build(
    *,
    verification_status,
    existing=None,
    transition_result=(
        MarketSessionTransitionResult.CREATED
    ),
):
    verification = FakeVerificationService(
        verification_status
    )
    trading = FakeTradingRepository(
        existing
    )
    transitions = FakeTransitionRepository(
        transition_result
    )

    service = HolidayPromotionService(
        verification_service=verification,
        trading_repository=trading,
        promotion_policy=(
            HolidayPromotionPolicy()
        ),
        transition_repository=transitions,
    )

    return (
        service,
        verification,
        trading,
        transitions,
    )


def test_unknown_verification_never_writes():
    (
        service,
        verification,
        trading,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.UNKNOWN
        ),
    )

    outcome = service.promote(DAY)

    assert verification.calls == [DAY]
    assert trading.calls == [DAY]

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.NOOP
    )

    assert outcome.transition is None
    assert transitions.calls == []


def test_verified_holiday_creates_when_absent():
    (
        service,
        _,
        _,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.HOLIDAY
        ),
    )

    outcome = service.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.CREATE
    )

    assert (
        outcome.transition
        == MarketSessionTransitionResult.CREATED
    )

    assert len(transitions.calls) == 1

    session, replaceable = transitions.calls[0]

    assert session.market_date == DAY
    assert (
        session.status
        == MarketSessionStatus.HOLIDAY
    )
    assert replaceable == set()


def test_weekend_can_be_atomically_replaced():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.WEEKEND,
    )

    (
        service,
        _,
        _,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.HOLIDAY
        ),
        existing=existing,
        transition_result=(
            MarketSessionTransitionResult.REPLACED
        ),
    )

    outcome = service.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.REPLACE
    )

    assert (
        outcome.transition
        == MarketSessionTransitionResult.REPLACED
    )

    _, replaceable = transitions.calls[0]

    assert replaceable == {
        MarketSessionStatus.WEEKEND,
        MarketSessionStatus.UNKNOWN,
    }


def test_existing_verified_conflicts_without_write():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.VERIFIED,
    )

    (
        service,
        _,
        _,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.HOLIDAY
        ),
        existing=existing,
    )

    outcome = service.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.CONFLICT
    )

    assert outcome.transition is None
    assert transitions.calls == []


def test_existing_holiday_is_idempotent():
    existing = MarketSession(
        market_date=DAY,
        status=MarketSessionStatus.HOLIDAY,
    )

    (
        service,
        _,
        _,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.HOLIDAY
        ),
        existing=existing,
    )

    outcome = service.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.NOOP
    )

    assert outcome.transition is None
    assert transitions.calls == []


def test_atomic_race_conflict_is_exposed():
    (
        service,
        _,
        _,
        transitions,
    ) = build(
        verification_status=(
            MarketSessionStatus.HOLIDAY
        ),
        existing=None,
        transition_result=(
            MarketSessionTransitionResult.CONFLICT
        ),
    )

    outcome = service.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.CREATE
    )

    assert (
        outcome.transition
        == MarketSessionTransitionResult.CONFLICT
    )

    assert len(transitions.calls) == 1
