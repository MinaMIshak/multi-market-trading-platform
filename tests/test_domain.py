from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain import (
    RiskDecision,
    RiskDecisionType,
    Signal,
    SignalStatus,
    TradePlan,
)


CAIRO = ZoneInfo("Africa/Cairo")


def now():
    return datetime.now(CAIRO)


def test_valid_long_trade_plan():
    created = now()

    plan = TradePlan(
        signal_id=uuid4(),
        symbol="swdy",
        entry_low=Decimal("134.40"),
        entry_high=Decimal("135.00"),
        entry_reference=Decimal("134.70"),
        stop_price=Decimal("132.90"),
        target_1=Decimal("138.30"),
        target_2=Decimal("141.90"),
        created_at=created,
        valid_until=created + timedelta(hours=3),
    )

    assert plan.symbol == "SWDY"
    assert plan.risk_per_share == Decimal("1.80")
    assert plan.reward_r(plan.target_1) == Decimal("2")


def test_invalid_long_stop_is_rejected():
    created = now()

    try:
        TradePlan(
            signal_id=uuid4(),
            symbol="SWDY",
            entry_low=Decimal("134"),
            entry_high=Decimal("135"),
            entry_reference=Decimal("134.5"),
            stop_price=Decimal("135"),
            target_1=Decimal("138"),
            created_at=created,
            valid_until=created + timedelta(hours=1),
        )
    except ValidationError:
        return

    raise AssertionError(
        "invalid long stop should have been rejected"
    )


def test_signal_requires_timezone_aware_datetime():
    try:
        Signal(
            symbol="TAQA",
            strategy="BREAKOUT",
            status=SignalStatus.WATCH,
            created_at=datetime(2026, 9, 9, 10, 30),
        )
    except ValidationError:
        return

    raise AssertionError(
        "naive signal datetime should have been rejected"
    )


def test_blocked_trade_has_zero_quantity():
    try:
        RiskDecision(
            trade_plan_id=uuid4(),
            decision=RiskDecisionType.BLOCK,
            account_equity=Decimal("70000"),
            risk_budget=Decimal("700"),
            approved_risk=Decimal("100"),
            quantity=10,
            max_position_value=Decimal("5000"),
            portfolio_exposure_pct=Decimal("0.20"),
        )
    except ValidationError:
        return

    raise AssertionError(
        "blocked risk decision should have been rejected"
    )
