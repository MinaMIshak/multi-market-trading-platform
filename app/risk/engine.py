from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_FLOOR

from app.domain import (
    MarketRegimeType,
    RiskDecision,
    RiskDecisionType,
    SignalDirection,
    TradePlan,
    TradeState,
)
from app.risk.models import RiskContext
from app.risk.policy import RiskPolicy

ZERO = Decimal("0")
ONE = Decimal("1")


def _floor_quantity(value: Decimal) -> int:
    return max(0, int(value.to_integral_value(rounding=ROUND_FLOOR)))


class RiskEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        if not isinstance(policy, RiskPolicy):
            raise TypeError("explicit RiskPolicy required")
        self.policy = RiskPolicy.model_validate(policy.model_dump())

    def evaluate(
        self, *, plan: TradePlan, account_equity: Decimal,
        market_regime: MarketRegimeType, context: RiskContext,
        decision_time: datetime, trade_state: TradeState,
    ) -> RiskDecision:
        if not isinstance(plan, TradePlan):
            raise TypeError("plan must be a TradePlan; candidates are not admitted")
        # Revalidate snapshots, including model_copy/model_construct bypasses.
        plan = TradePlan.model_validate(plan.model_dump())
        if not isinstance(context, RiskContext):
            raise TypeError("explicit RiskContext required")
        context = RiskContext.model_validate(context.model_dump())
        policy = RiskPolicy.model_validate(self.policy.model_dump())
        if (not isinstance(decision_time, datetime) or decision_time.tzinfo is None
                or decision_time.utcoffset() is None):
            raise ValueError("decision_time must be timezone-aware")
        if (not isinstance(account_equity, Decimal) or not account_equity.is_finite()
                or account_equity <= ZERO):
            raise ValueError("account_equity must be a positive finite Decimal")
        scale = (policy.risk_on_scale if market_regime == MarketRegimeType.RISK_ON
                 else policy.neutral_scale if market_regime == MarketRegimeType.NEUTRAL else ZERO)
        budget = account_equity * policy.risk_per_trade_pct * scale
        caps: dict[str, int] = {}
        reasons: list[str] = []

        def decision(quantity=0, blocker=None):
            value = Decimal(quantity) * plan.entry_reference
            return RiskDecision(
                trade_plan_id=plan.trade_plan_id, policy_version=policy.policy_version,
                policy_identity=policy.identity, quantity_caps=caps,
                decision=(RiskDecisionType.BLOCK if blocker else RiskDecisionType.REDUCE
                          if scale < ONE or quantity < caps["RISK_BUDGET"] else RiskDecisionType.APPROVE),
                account_equity=account_equity, risk_budget=budget,
                approved_risk=Decimal(quantity) * plan.risk_per_share, quantity=quantity,
                max_position_value=value,
                portfolio_exposure_pct=min(ONE, (context.current_exposure_value + value) / account_equity),
                daily_realized_r=context.daily_realized_r, reasons=reasons,
                blockers=[blocker] if blocker else [],
            )

        if not isinstance(trade_state, TradeState) or trade_state not in (TradeState.READY, TradeState.ENTRY_TRIGGERED):
            return decision(blocker="INVALID_TRADE_STATE")
        if decision_time < plan.created_at:
            return decision(blocker="PLAN_NOT_YET_VALID")
        if decision_time >= plan.valid_until:
            return decision(blocker="PLAN_EXPIRED")
        if plan.direction == SignalDirection.SHORT and not policy.allow_short:
            return decision(blocker="SHORT_NOT_ALLOWED")
        if context.daily_realized_r <= -policy.daily_loss_limit_r:
            return decision(blocker="DAILY_LOSS_LIMIT_REACHED")
        if context.open_positions + context.pending_entries >= policy.max_open_positions:
            return decision(blocker="MAX_OPEN_POSITIONS_REACHED")
        if scale <= ZERO:
            return decision(blocker="MARKET_REGIME_BLOCK")
        if plan.risk_per_share <= ZERO:
            return decision(blocker="INVALID_RISK_PER_SHARE")
        if plan.reward_r(plan.target_1) < policy.min_target1_r:
            return decision(blocker="INSUFFICIENT_REWARD_RISK")
        if policy.max_correlation_group_exposure_pct is not None and context.correlation_group_id is None:
            return decision(blocker="MISSING_CORRELATION_GROUP_CONTEXT")

        caps["RISK_BUDGET"] = _floor_quantity(budget / plan.risk_per_share)
        capacities = [
            ("POSITION_CAP", account_equity * policy.max_position_pct, plan.entry_reference),
            ("PORTFOLIO_EXPOSURE", account_equity * policy.max_portfolio_exposure_pct - context.current_exposure_value, plan.entry_reference),
            ("CASH", context.cash_balance - context.reserved_cash, plan.entry_reference),
            ("PORTFOLIO_OPEN_RISK", account_equity * policy.max_portfolio_open_risk_pct - context.current_open_risk_value, plan.risk_per_share),
            ("SYMBOL_EXPOSURE", account_equity * policy.max_symbol_exposure_pct - context.symbol_exposure_value, plan.entry_reference),
        ]
        if policy.max_correlation_group_exposure_pct is not None:
            capacities.append(("CORRELATION_GROUP_EXPOSURE", account_equity * policy.max_correlation_group_exposure_pct - context.correlation_group_exposure_value, plan.entry_reference))
        if context.liquidity_cap_value is not None:
            capacities.append(("LIQUIDITY", context.liquidity_cap_value, plan.entry_reference))
        reasons.extend([f"TARGET1_R={plan.reward_r(plan.target_1)}", f"REGIME_SCALE={scale}", f"RISK_PER_SHARE={plan.risk_per_share}"])
        if scale < ONE:
            reasons.append("REDUCED_BY_MARKET_REGIME")
        for name, capacity, divisor in capacities:
            caps[name] = _floor_quantity(capacity / divisor)
            if caps[name] < caps["RISK_BUDGET"]:
                reasons.append(f"REDUCED_BY_{name}")
            if caps[name] == 0:
                reasons.append(f"NO_CAPACITY_{name}")
        if caps["RISK_BUDGET"] == 0:
            reasons.append("NO_CAPACITY_RISK_BUDGET")
        quantity = min(caps.values())
        return decision(quantity, "NO_POSITION_CAPACITY" if quantity == 0 else None)
