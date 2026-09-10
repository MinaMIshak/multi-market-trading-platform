from datetime import date, datetime, timezone

import pytest

from app.domain import MarketSession
from app.domain.enums import MarketSessionStatus
from app.storage import Database, TradingRepository
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
    MarketSessionTransitionResult,
)


DAY = date(2026, 9, 10)
NOW = datetime(
    2026, 9, 10, 12, 0,
    tzinfo=timezone.utc,
)


def build(tmp_path):
    db = Database(tmp_path / "platform.db")
    db.initialize()

    return (
        db,
        TradingRepository(db),
        MarketSessionTransitionRepository(db),
    )


def session(status, *, verified_at=None):
    return MarketSession(
        market_date=DAY,
        status=status,
        data_verified_at=verified_at,
    )


def test_create_when_absent(tmp_path):
    _, trading, transitions = build(tmp_path)

    result = transitions.compare_and_promote(
        session(MarketSessionStatus.HOLIDAY)
    )

    assert result == MarketSessionTransitionResult.CREATED
    assert (
        trading.get_market_session(DAY).status
        == MarketSessionStatus.HOLIDAY
    )


def test_same_state_is_idempotent(tmp_path):
    _, trading, transitions = build(tmp_path)

    trading.save_market_session(
        session(MarketSessionStatus.HOLIDAY)
    )

    result = transitions.compare_and_promote(
        session(
            MarketSessionStatus.HOLIDAY,
            verified_at=NOW,
        )
    )

    assert result == MarketSessionTransitionResult.UNCHANGED
    assert (
        trading.get_market_session(DAY)
        .data_verified_at
        is None
    )


@pytest.mark.parametrize(
    "existing",
    [
        MarketSessionStatus.WEEKEND,
        MarketSessionStatus.UNKNOWN,
    ],
)
def test_declared_lower_state_can_be_replaced(
    tmp_path,
    existing,
):
    _, trading, transitions = build(tmp_path)

    trading.save_market_session(session(existing))

    result = transitions.compare_and_promote(
        session(MarketSessionStatus.HOLIDAY),
        replaceable_statuses={
            MarketSessionStatus.WEEKEND,
            MarketSessionStatus.UNKNOWN,
        },
    )

    assert result == MarketSessionTransitionResult.REPLACED
    assert (
        trading.get_market_session(DAY).status
        == MarketSessionStatus.HOLIDAY
    )


def test_disallowed_state_conflicts(tmp_path):
    _, trading, transitions = build(tmp_path)

    trading.save_market_session(
        session(MarketSessionStatus.VERIFIED)
    )

    result = transitions.compare_and_promote(
        session(MarketSessionStatus.HOLIDAY),
        replaceable_statuses={
            MarketSessionStatus.WEEKEND,
            MarketSessionStatus.UNKNOWN,
        },
    )

    assert result == MarketSessionTransitionResult.CONFLICT
    assert (
        trading.get_market_session(DAY).status
        == MarketSessionStatus.VERIFIED
    )


def test_stale_expected_state_cannot_overwrite(
    tmp_path,
):
    _, trading, transitions = build(tmp_path)

    trading.save_market_session(
        session(MarketSessionStatus.WEEKEND)
    )

    first = transitions.compare_and_promote(
        session(MarketSessionStatus.HOLIDAY),
        replaceable_statuses={
            MarketSessionStatus.WEEKEND,
        },
    )

    second = transitions.compare_and_promote(
        session(MarketSessionStatus.VERIFIED),
        replaceable_statuses={
            MarketSessionStatus.WEEKEND,
        },
    )

    assert first == MarketSessionTransitionResult.REPLACED
    assert second == MarketSessionTransitionResult.CONFLICT
    assert (
        trading.get_market_session(DAY).status
        == MarketSessionStatus.HOLIDAY
    )
