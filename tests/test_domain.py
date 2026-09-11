from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
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
            policy_version="test-v1",
            policy_identity="test-policy",
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


@pytest.mark.parametrize("decision,quantity,approved_risk,message", [
    (RiskDecisionType.REDUCE, 0, "1", "positive quantity"),
    (RiskDecisionType.APPROVE, 0, "1", "positive quantity"),
    (RiskDecisionType.REDUCE, 1, "0", "positive approved_risk"),
    (RiskDecisionType.APPROVE, 1, "0", "positive approved_risk"),
    (RiskDecisionType.BLOCK, 1, "0", "quantity=0"),
    (RiskDecisionType.BLOCK, 0, "1", "approved_risk=0"),
    (RiskDecisionType.APPROVE, 1, "701", "cannot exceed risk_budget"),
])
def test_risk_decision_invariants(decision, quantity, approved_risk, message):
    with pytest.raises(ValidationError, match=message):
        make_risk_decision(decision=decision, quantity=quantity,
                           approved_risk=Decimal(approved_risk))


def make_risk_decision(**overrides):
    values = dict(policy_version="test-v1", policy_identity="test-policy",
                  trade_plan_id=uuid4(), decision=RiskDecisionType.APPROVE,
                  account_equity=Decimal("70000"), risk_budget=Decimal("700"),
                  approved_risk=Decimal("1"), quantity=1,
                  max_position_value=Decimal("10"),
                  portfolio_exposure_pct=Decimal("0"))
    values.update(overrides)
    return RiskDecision(**values)


@pytest.mark.parametrize("caps", [
    {"CASH": -1}, {"": 0}, {" \t\n": 0},
    {"CASH": 1.5}, {"CASH": 1.0}, {"CASH": "1"}, {"CASH": True},
])
def test_invalid_quantity_caps_reject(caps):
    with pytest.raises(ValidationError):
        make_risk_decision(quantity_caps=caps)


@pytest.mark.parametrize("decision", list(RiskDecisionType))
def test_valid_risk_decision_boundaries(decision):
    blocked = decision == RiskDecisionType.BLOCK
    result = make_risk_decision(decision=decision, quantity=0 if blocked else 1,
                                approved_risk=Decimal("0" if blocked else "1"),
                                quantity_caps={"CASH": 0, "RISK_BUDGET": 1})
    assert result.quantity_caps == {"CASH": 0, "RISK_BUDGET": 1}
