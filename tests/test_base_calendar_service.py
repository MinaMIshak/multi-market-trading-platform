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


FRIDAY = date(2026, 9, 11)
THURSDAY = date(2026, 9, 10)


class FakeRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.reads = []
        self.saved = []

    def get_market_session(
        self,
        market_date,
    ):
        self.reads.append(market_date)
        return self.existing

    def save_market_session(
        self,
        session,
    ):
        self.saved.append(session)


def build(repository):
    return BaseCalendarSessionService(
        trading_repository=repository,
        policy=BaseTradingCalendarPolicy(),
    )


def test_friday_persists_weekend_when_empty():
    repository = FakeRepository()

    status = build(repository).apply(
        FRIDAY
    )

    assert status == MarketSessionStatus.WEEKEND
    assert repository.reads == [FRIDAY]
    assert len(repository.saved) == 1

    session = repository.saved[0]

    assert session.market_date == FRIDAY
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )


def test_weekday_stays_unknown_and_writes_nothing():
    repository = FakeRepository()

    status = build(repository).apply(
        THURSDAY
    )

    assert status == MarketSessionStatus.UNKNOWN
    assert repository.reads == []
    assert repository.saved == []


def test_existing_verified_is_never_overwritten():
    existing = MarketSession(
        market_date=FRIDAY,
        status=MarketSessionStatus.VERIFIED,
    )
    repository = FakeRepository(existing)

    status = build(repository).apply(
        FRIDAY
    )

    assert status == MarketSessionStatus.VERIFIED
    assert repository.saved == []


def test_existing_holiday_is_never_overwritten():
    existing = MarketSession(
        market_date=FRIDAY,
        status=MarketSessionStatus.HOLIDAY,
    )
    repository = FakeRepository(existing)

    status = build(repository).apply(
        FRIDAY
    )

    assert status == MarketSessionStatus.HOLIDAY
    assert repository.saved == []


def test_existing_weekend_is_idempotent():
    existing = MarketSession(
        market_date=FRIDAY,
        status=MarketSessionStatus.WEEKEND,
    )
    repository = FakeRepository(existing)

    status = build(repository).apply(
        FRIDAY
    )

    assert status == MarketSessionStatus.WEEKEND
    assert repository.saved == []
