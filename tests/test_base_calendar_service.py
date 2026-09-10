from datetime import date

from app.core.base_calendar_service import (
    BaseCalendarSessionService,
)
from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionResult,
)


FRIDAY = date(2026, 9, 11)
THURSDAY = date(2026, 9, 10)


class FakeTradingRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.reads = []

    def get_market_session(
        self,
        market_date,
    ):
        self.reads.append(market_date)
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
    existing=None,
    transition=(
        MarketSessionTransitionResult.CREATED
    ),
):
    trading = FakeTradingRepository(
        existing
    )
    transitions = FakeTransitionRepository(
        transition
    )

    service = BaseCalendarSessionService(
        trading_repository=trading,
        policy=BaseTradingCalendarPolicy(),
        transition_repository=transitions,
    )

    return service, trading, transitions


def test_friday_uses_atomic_create_only():
    service, trading, transitions = build()

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.WEEKEND
    assert trading.reads == []
    assert len(transitions.calls) == 1

    session, replaceable = transitions.calls[0]

    assert session.market_date == FRIDAY
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )
    assert replaceable == set()


def test_weekday_stays_unknown_and_writes_nothing():
    service, trading, transitions = build()

    status = service.apply(THURSDAY)

    assert status == MarketSessionStatus.UNKNOWN
    assert trading.reads == []
    assert transitions.calls == []


def test_existing_weekend_is_idempotent():
    service, trading, transitions = build(
        transition=(
            MarketSessionTransitionResult.UNCHANGED
        )
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.WEEKEND
    assert trading.reads == []
    assert len(transitions.calls) == 1


def test_verified_race_conflict_is_preserved():
    existing = MarketSession(
        market_date=FRIDAY,
        status=MarketSessionStatus.VERIFIED,
    )

    service, trading, transitions = build(
        existing=existing,
        transition=(
            MarketSessionTransitionResult.CONFLICT
        ),
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.VERIFIED
    assert trading.reads == [FRIDAY]
    assert len(transitions.calls) == 1


def test_holiday_race_conflict_is_preserved():
    existing = MarketSession(
        market_date=FRIDAY,
        status=MarketSessionStatus.HOLIDAY,
    )

    service, trading, transitions = build(
        existing=existing,
        transition=(
            MarketSessionTransitionResult.CONFLICT
        ),
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.HOLIDAY
    assert trading.reads == [FRIDAY]
    assert len(transitions.calls) == 1


def test_conflict_without_visible_row_fails_closed():
    service, trading, transitions = build(
        existing=None,
        transition=(
            MarketSessionTransitionResult.CONFLICT
        ),
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.UNKNOWN
    assert trading.reads == [FRIDAY]
    assert len(transitions.calls) == 1
