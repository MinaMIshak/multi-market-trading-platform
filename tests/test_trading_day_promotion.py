import pytest

from app.core.trading_day_promotion import (
    TradingDayPromotionAction,
    TradingDayPromotionPolicy,
)
from app.domain.enums import (
    MarketSessionStatus,
)


def evaluate(
    existing,
    verified=MarketSessionStatus.VERIFIED,
):
    return TradingDayPromotionPolicy().evaluate(
        verified_status=verified,
        existing_status=existing,
    )


def test_unverified_decision_never_writes():
    result = evaluate(
        None,
        MarketSessionStatus.UNKNOWN,
    )

    assert (
        result.action
        == TradingDayPromotionAction.NOOP
    )
    assert result.target_status is None


def test_verified_creates_when_absent():
    result = evaluate(None)

    assert (
        result.action
        == TradingDayPromotionAction.CREATE
    )
    assert (
        result.target_status
        == MarketSessionStatus.VERIFIED
    )


def test_existing_verified_is_idempotent():
    result = evaluate(
        MarketSessionStatus.VERIFIED
    )

    assert (
        result.action
        == TradingDayPromotionAction.NOOP
    )


@pytest.mark.parametrize(
    "existing",
    [
        MarketSessionStatus.UNKNOWN,
        MarketSessionStatus.WEEKEND,
        MarketSessionStatus.PRE_MARKET,
        MarketSessionStatus.OPEN,
        MarketSessionStatus.FIRST15_COMPLETE,
        MarketSessionStatus.CLOSED,
        MarketSessionStatus.DATA_PENDING,
    ],
)
def test_lower_authority_state_can_be_replaced(
    existing,
):
    result = evaluate(existing)

    assert (
        result.action
        == TradingDayPromotionAction.REPLACE
    )
    assert (
        result.target_status
        == MarketSessionStatus.VERIFIED
    )


def test_holiday_is_hard_conflict():
    result = evaluate(
        MarketSessionStatus.HOLIDAY
    )

    assert (
        result.action
        == TradingDayPromotionAction.CONFLICT
    )
    assert (
        result.target_status
        == MarketSessionStatus.VERIFIED
    )
