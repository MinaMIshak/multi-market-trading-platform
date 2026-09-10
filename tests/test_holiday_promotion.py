import pytest

from app.core.holiday_promotion import (
    HolidayPromotionAction,
    HolidayPromotionPolicy,
)
from app.domain.enums import (
    MarketSessionStatus,
)


def evaluate(existing, holiday=MarketSessionStatus.HOLIDAY):
    return HolidayPromotionPolicy().evaluate(
        holiday_status=holiday,
        existing_status=existing,
    )


def test_unknown_holiday_decision_never_writes():
    result = evaluate(
        None,
        MarketSessionStatus.UNKNOWN,
    )

    assert result.action == HolidayPromotionAction.NOOP
    assert result.target_status is None


def test_verified_holiday_creates_when_absent():
    result = evaluate(None)

    assert result.action == HolidayPromotionAction.CREATE
    assert (
        result.target_status
        == MarketSessionStatus.HOLIDAY
    )


def test_existing_holiday_is_idempotent():
    result = evaluate(
        MarketSessionStatus.HOLIDAY
    )

    assert result.action == HolidayPromotionAction.NOOP


@pytest.mark.parametrize(
    "existing",
    [
        MarketSessionStatus.WEEKEND,
        MarketSessionStatus.UNKNOWN,
    ],
)
def test_lower_authority_state_can_be_replaced(
    existing,
):
    result = evaluate(existing)

    assert (
        result.action
        == HolidayPromotionAction.REPLACE
    )

    assert (
        result.target_status
        == MarketSessionStatus.HOLIDAY
    )


@pytest.mark.parametrize(
    "existing",
    [
        MarketSessionStatus.VERIFIED,
        MarketSessionStatus.PRE_MARKET,
        MarketSessionStatus.OPEN,
        MarketSessionStatus.FIRST15_COMPLETE,
        MarketSessionStatus.CLOSED,
        MarketSessionStatus.DATA_PENDING,
    ],
)
def test_trading_session_states_conflict(
    existing,
):
    result = evaluate(existing)

    assert (
        result.action
        == HolidayPromotionAction.CONFLICT
    )

    assert (
        result.target_status
        == MarketSessionStatus.HOLIDAY
    )
