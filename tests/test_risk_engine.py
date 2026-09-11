from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domain import (
    MarketRegimeType,
    RiskDecisionType,
    TradePlan,
    TradeState,
)
from app.risk import (
    RiskContext,
    RiskEngine,
    RiskPolicy,
)


CAIRO = ZoneInfo("Africa/Cairo")


def make_plan(
    *,
    entry: str = "10",
    stop: str = "9",
    target: str = "12",
) -> TradePlan:
    now = datetime(2026, 9, 10, 10, tzinfo=CAIRO)

    entry_decimal = Decimal(entry)

    return TradePlan(
        signal_id=uuid4(),
        symbol="SWDY",
        entry_low=entry_decimal,
        entry_high=entry_decimal,
        entry_reference=entry_decimal,
        stop_price=Decimal(stop),
        target_1=Decimal(target),
        created_at=now,
        valid_until=now + timedelta(hours=3),
    )


def test_risk_on_approves_normal_trade():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        context=make_context(),
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
    )

    assert result.decision == RiskDecisionType.APPROVE
    assert result.risk_budget == Decimal("700")
    assert result.quantity == 700
    assert result.approved_risk == Decimal("700")


def test_neutral_reduces_risk():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        context=make_context(),
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.NEUTRAL,
    )

    assert result.decision == RiskDecisionType.REDUCE
    assert result.risk_budget == Decimal("350")
    assert result.quantity == 350


def test_risk_off_blocks_trade():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        context=make_context(),
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_OFF,
    )

    assert result.decision == RiskDecisionType.BLOCK
    assert result.quantity == 0
    assert "MARKET_REGIME_BLOCK" in result.blockers


def test_daily_loss_kill_switch():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
        context=make_context(
            daily_realized_r=Decimal("-2.0"),
        ),
    )

    assert result.decision == RiskDecisionType.BLOCK
    assert "DAILY_LOSS_LIMIT_REACHED" in result.blockers


def test_max_open_positions_blocks():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
        context=make_context(
            open_positions=3,
        ),
    )

    assert result.decision == RiskDecisionType.BLOCK
    assert "MAX_OPEN_POSITIONS_REACHED" in result.blockers


def test_low_reward_risk_blocks():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        context=make_context(),
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(
            entry="10",
            stop="9",
            target="11.5",
        ),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
    )

    assert result.decision == RiskDecisionType.BLOCK
    assert "INSUFFICIENT_REWARD_RISK" in result.blockers


def test_liquidity_cap_reduces_quantity():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
        context=make_context(
            liquidity_cap_value=Decimal("2000"),
        ),
    )

    assert result.decision == RiskDecisionType.REDUCE
    assert result.quantity == 200
    assert "REDUCED_BY_LIQUIDITY" in result.reasons


def test_portfolio_exposure_reduces_quantity():
    engine = RiskEngine(make_policy())

    result = engine.evaluate(
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
        context=make_context(
            current_exposure_value=Decimal("40000"),
        ),
    )

    assert result.decision == RiskDecisionType.REDUCE
    assert result.quantity == 200
    assert (
        "REDUCED_BY_PORTFOLIO_EXPOSURE"
        in result.reasons
    )


def test_custom_policy_works():
    policy = make_policy(
        risk_per_trade_pct=Decimal("0.005"),
        max_position_pct=Decimal("0.20"),
        max_portfolio_exposure_pct=Decimal("0.50"),
        max_open_positions=2,
    )

    engine = RiskEngine(policy)

    result = engine.evaluate(
        context=make_context(),
        decision_time=datetime(2026, 9, 10, 11, tzinfo=CAIRO),
        trade_state=TradeState.READY,
        plan=make_plan(),
        account_equity=Decimal("70000"),
        market_regime=MarketRegimeType.RISK_ON,
    )

    assert result.risk_budget == Decimal("350.000")
    assert result.quantity == 350


def make_policy(**overrides):
    values = dict(
        policy_version="research-v1", risk_per_trade_pct=Decimal("0.01"),
        max_position_pct=Decimal("0.25"), max_portfolio_exposure_pct=Decimal("0.60"),
        max_portfolio_open_risk_pct=Decimal("0.03"), max_symbol_exposure_pct=Decimal("0.25"),
        max_correlation_group_exposure_pct=None, max_open_positions=3,
        daily_loss_limit_r=Decimal("2"), min_target1_r=Decimal("1.80"),
        risk_on_scale=Decimal("1"), neutral_scale=Decimal("0.50"),
        risk_off_scale=Decimal("0"), allow_short=False,
    )
    values.update(overrides)
    return RiskPolicy(**values)


def make_context(**overrides):
    values = dict(open_positions=0, pending_entries=0,
                  current_exposure_value=Decimal("0"), daily_realized_r=Decimal("0"),
                  cash_balance=Decimal("70000"), reserved_cash=Decimal("0"),
                  current_open_risk_value=Decimal("0"), symbol_exposure_value=Decimal("0"))
    values.update(overrides)
    return RiskContext(**values)
