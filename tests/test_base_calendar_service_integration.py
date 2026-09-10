from datetime import date

from app.core.base_calendar_service import (
    BaseCalendarSessionService,
)
from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.schedule import CalendarTruth
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage import (
    Database,
    TradingRepository,
)


FRIDAY = date(2026, 9, 11)
THURSDAY = date(2026, 9, 10)


def build(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = TradingRepository(
        database
    )

    service = BaseCalendarSessionService(
        trading_repository=repository,
        policy=BaseTradingCalendarPolicy(),
    )

    resolver = CalendarTruthResolver(
        repository
    )

    return repository, service, resolver


def test_friday_persists_and_resolves_non_trading(
    tmp_path,
):
    repository, service, resolver = build(
        tmp_path
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.WEEKEND

    session = repository.get_market_session(
        FRIDAY
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )

    assert (
        resolver.resolve(FRIDAY)
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )


def test_weekday_without_evidence_stays_unverified(
    tmp_path,
):
    repository, service, resolver = build(
        tmp_path
    )

    status = service.apply(THURSDAY)

    assert status == MarketSessionStatus.UNKNOWN

    assert (
        repository.get_market_session(
            THURSDAY
        )
        is None
    )

    assert (
        resolver.resolve(THURSDAY)
        == CalendarTruth.UNVERIFIED
    )


def test_existing_holiday_keeps_precedence(
    tmp_path,
):
    repository, service, resolver = build(
        tmp_path
    )

    repository.save_market_session(
        MarketSession(
            market_date=FRIDAY,
            status=MarketSessionStatus.HOLIDAY,
        )
    )

    status = service.apply(FRIDAY)

    assert status == MarketSessionStatus.HOLIDAY

    session = repository.get_market_session(
        FRIDAY
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.HOLIDAY
    )

    assert (
        resolver.resolve(FRIDAY)
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )
