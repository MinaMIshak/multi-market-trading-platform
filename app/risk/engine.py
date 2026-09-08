from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from app.domain import (
    MarketRegimeType,
    RiskDecision,
    RiskDecisionType,
    SignalDirection,
    TradePlan,
)
from app.risk.models import RiskContext
from app.risk.policy import RiskPolicy


ZERO = Decimal("0")
ONE = Decimal("1")


def _floor_quantity(value: Decimal) -> int:
    if value <= ZERO:
        return 0

    return int(
        value.to_integral_value(
            rounding=ROUND_FLOOR,
        )
    )


class RiskEngine:
    def __init__(
        self,
        policy: RiskPolicy | None = None,
    ) -> None:
        self.policy = policy or RiskPolicy()

    def _regime_scale(
        self,
        regime: MarketRegimeType,
    ) -> Decimal:
        if regime == MarketRegimeType.RISK_ON:
            return self.policy.risk_on_scale

        if regime == MarketRegimeType.NEUTRAL:
            return self.policy.neutral_scale

        if regime == MarketRegimeType.RISK_OFF:
            return self.policy.risk_off_scale

        # Unknown market state must not receive full risk.
        return ZERO

    def _blocked(
        self,
        *,
        plan: TradePlan,
        account_equity: Decimal,
        risk_budget: Decimal,
        context: RiskContext,
        reason: str,
    ) -> RiskDecision:
        exposure_pct = (
            context.current_exposure_value
            / account_equity
        )

        exposure_pct = min(
            max(exposure_pct, ZERO),
            ONE,
        )

        return RiskDecision(
            trade_plan_id=plan.trade_plan_id,
            decision=RiskDecisionType.BLOCK,
            account_equity=account_equity,
            risk_budget=max(risk_budget, ZERO),
            approved_risk=ZERO,
            quantity=0,
            max_position_value=ZERO,
            portfolio_exposure_pct=exposure_pct,
            daily_realized_r=context.daily_realized_r,
            blockers=[reason],
        )

    def evaluate(
        self,
        *,
        plan: TradePlan,
        account_equity: Decimal,
        market_regime: MarketRegimeType,
        context: RiskContext | None = None,
    ) -> RiskDecision:
        context = context or RiskContext()

        if account_equity <= ZERO:
            raise ValueError(
                "account_equity must be positive"
            )

        base_risk_budget = (
            account_equity
            * self.policy.risk_per_trade_pct
        )

        regime_scale = self._regime_scale(
            market_regime
        )

        scaled_risk_budget = (
            base_risk_budget
            * regime_scale
        )

        if (
            plan.direction == SignalDirection.SHORT
            and not self.policy.allow_short
        ):
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="SHORT_NOT_ALLOWED",
            )

        if (
            context.daily_realized_r
            <= -self.policy.daily_loss_limit_r
        ):
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="DAILY_LOSS_LIMIT_REACHED",
            )

        if (
            context.open_positions
            >= self.policy.max_open_positions
        ):
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="MAX_OPEN_POSITIONS_REACHED",
            )

        if regime_scale <= ZERO:
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="MARKET_REGIME_BLOCK",
            )

        target1_r = plan.reward_r(
            plan.target_1
        )

        if target1_r < self.policy.min_target1_r:
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="INSUFFICIENT_REWARD_RISK",
            )

        risk_per_share = plan.risk_per_share

        if risk_per_share <= ZERO:
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="INVALID_RISK_PER_SHARE",
            )

        # Quantity permitted by risk budget.
        risk_quantity = _floor_quantity(
            scaled_risk_budget
            / risk_per_share
        )

        # Quantity permitted by single-position cap.
        position_cap_value = (
            account_equity
            * self.policy.max_position_pct
        )

        position_quantity = _floor_quantity(
            position_cap_value
            / plan.entry_reference
        )

        # Remaining portfolio capacity.
        max_total_exposure = (
            account_equity
            * self.policy.max_portfolio_exposure_pct
        )

        remaining_exposure = max(
            max_total_exposure
            - context.current_exposure_value,
            ZERO,
        )

        exposure_quantity = _floor_quantity(
            remaining_exposure
            / plan.entry_reference
        )

        quantity_caps = [
            risk_quantity,
            position_quantity,
            exposure_quantity,
        ]

        liquidity_limited = False

        if context.liquidity_cap_value is not None:
            liquidity_quantity = _floor_quantity(
                context.liquidity_cap_value
                / plan.entry_reference
            )

            quantity_caps.append(
                liquidity_quantity
            )

            if liquidity_quantity < risk_quantity:
                liquidity_limited = True

        quantity = min(quantity_caps)

        if quantity <= 0:
            return self._blocked(
                plan=plan,
                account_equity=account_equity,
                risk_budget=scaled_risk_budget,
                context=context,
                reason="NO_POSITION_CAPACITY",
            )

        approved_risk = (
            Decimal(quantity)
            * risk_per_share
        )

        actual_position_value = (
            Decimal(quantity)
            * plan.entry_reference
        )

        projected_exposure = (
            context.current_exposure_value
            + actual_position_value
        )

        projected_exposure_pct = (
            projected_exposure
            / account_equity
        )

        projected_exposure_pct = min(
            max(projected_exposure_pct, ZERO),
            ONE,
        )

        reasons: list[str] = [
            f"TARGET1_R={target1_r}",
            f"REGIME_SCALE={regime_scale}",
            f"RISK_PER_SHARE={risk_per_share}",
        ]

        reduced = False

        if regime_scale < ONE:
            reduced = True
            reasons.append(
                "REDUCED_BY_MARKET_REGIME"
            )

        if quantity < risk_quantity:
            reduced = True

            if quantity == position_quantity:
                reasons.append(
                    "REDUCED_BY_POSITION_CAP"
                )

            if quantity == exposure_quantity:
                reasons.append(
                    "REDUCED_BY_PORTFOLIO_EXPOSURE"
                )

            if liquidity_limited:
                reasons.append(
                    "REDUCED_BY_LIQUIDITY"
                )

        decision_type = (
            RiskDecisionType.REDUCE
            if reduced
            else RiskDecisionType.APPROVE
        )

        return RiskDecision(
            trade_plan_id=plan.trade_plan_id,
            decision=decision_type,
            account_equity=account_equity,
            risk_budget=scaled_risk_budget,
            approved_risk=approved_risk,
            quantity=quantity,
            max_position_value=actual_position_value,
            portfolio_exposure_pct=projected_exposure_pct,
            daily_realized_r=context.daily_realized_r,
            reasons=reasons,
            blockers=[],
        )
