from datetime import date

from app.domain import (
    MarketSession,
    MarketSessionStatus,
)
from app.storage import Database
from app.storage.repository import TradingRepository


def test_market_session_round_trip(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = TradingRepository(
        database
    )

    session = MarketSession(
        market_date=date(2026, 9, 10),
        status=MarketSessionStatus.VERIFIED,
    )

    repository.save_market_session(
        session
    )

    loaded = repository.get_market_session(
        date(2026, 9, 10)
    )

    assert loaded == session


def test_missing_market_session_returns_none(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = TradingRepository(
        database
    )

    assert repository.get_market_session(
        date(2026, 9, 10)
    ) is None
