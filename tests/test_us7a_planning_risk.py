"""US7A engineering fixtures only; never historical market evidence."""

from datetime import timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain import (
    MarketRegimeType,
    RiskDecisionType,
    TradeState,
)
from app.risk import RiskPolicy
from app.us.research_adapter import (
    evaluate_us_swing,
)
from app.us.research_planning import (
    USResearchPlanTerms,
    USResearchRiskAdmission,
    USResearchRiskSnapshot,
    admit_us_research_risk,
    build_us_research_trade_plan,
    materialize_legacy_risk_decision,
    materialize_legacy_trade_plan,
)
from test_us6_shared_research import (
    AT,
    config as swing_config,
    source_dataset,
    synthetic_watch_dataset,
)


D = Decimal


def watch_signal():
    return evaluate_us_swing(
        synthetic_watch_dataset(),
        swing_config(
            minimum_history=3,
            fast_ema_window=2,
            slow_ema_window=3,
            breakout_lookback=2,
        ),
    )


def no_confirmation_signal():
    return evaluate_us_swing(
        source_dataset(),
        swing_config(),
    )


def terms(**changes):
    values = {
        "planning_rule_version": (
            "explicit-us-swing-v1"
        ),
        "entry_low": D("14.5"),
        "entry_high": D("15.5"),
        "entry_reference": D("15"),
        "stop_price": D("14"),
        "target_1": D("17"),
        "target_2": None,
        "target_3": None,
        "valid_until": (
            AT + timedelta(days=2)
        ),
    }

    return USResearchPlanTerms(
        **(values | changes)
    )


def plan():
    return build_us_research_trade_plan(
        watch_signal(),
        terms(),
    )


def policy(**changes):
    values = {
        "policy_version": (
            "us-research-risk-v1"
        ),
        "risk_per_trade_pct": D(".01"),
        "max_position_pct": D(".25"),
        "max_portfolio_exposure_pct": D(".60"),
        "max_portfolio_open_risk_pct": D(".03"),
        "max_symbol_exposure_pct": D(".25"),
        "max_correlation_group_exposure_pct": None,
        "max_open_positions": 10,
        "daily_loss_limit_r": D("2"),
        "min_target1_r": D("1.8"),
        "risk_on_scale": D("1"),
        "neutral_scale": D(".5"),
        "risk_off_scale": D("0"),
        "allow_short": False,
    }

    return RiskPolicy(
        **(values | changes)
    )


def snapshot(**changes):
    values = {
        "snapshot_version": (
            "us-risk-snapshot-v1"
        ),
        "available_at": AT,
        "currency": "USD",
        "market_regime": (
            MarketRegimeType.RISK_ON
        ),
        "account_equity": D("100000"),
        "open_positions": 0,
        "pending_entries": 0,
        "current_exposure_value": D("0"),
        "daily_realized_r": D("0"),
        "cash_balance": D("100000"),
        "reserved_cash": D("0"),
        "current_open_risk_value": D("0"),
        "instrument_exposure_value": D("0"),
        "correlation_group_id": None,
        "correlation_group_exposure_value": None,
        "liquidity_cap_value": None,
        "market_regime_source_id": (
            "fixture-regime"
        ),
        "portfolio_source_id": (
            "fixture-portfolio"
        ),
        "provenance_id": (
            "fixture-provenance"
        ),
    }

    return USResearchRiskSnapshot(
        **(values | changes)
    )


def admit(
    *,
    source_plan=None,
    source_policy=None,
    source_snapshot=None,
    decision_at=None,
    trade_state=TradeState.READY,
):
    return admit_us_research_risk(
        (
            source_plan
            if source_plan is not None
            else plan()
        ),
        policy=(
            source_policy
            if source_policy is not None
            else policy()
        ),
        snapshot=(
            source_snapshot
            if source_snapshot is not None
            else snapshot()
        ),
        decision_at=(
            decision_at
            if decision_at is not None
            else AT
        ),
        trade_state=trade_state,
    )


def test_watch_builds_deterministic_identity_safe_plan():
    first = plan()
    second = plan()

    assert first == second
    assert first.identity == second.identity
    assert (
        first.instrument_id
        == watch_signal().instrument_id
    )
    assert (
        first.source_dataset_id
        == watch_signal().source_dataset_id
    )
    assert first.execution_allowed is False
    assert (
        first.validation_status
        == "UNVALIDATED"
    )


def test_no_confirmation_cannot_be_planned():
    with pytest.raises(
        ValueError,
        match="WATCH",
    ):
        build_us_research_trade_plan(
            no_confirmation_signal(),
            terms(),
        )


def test_plan_terms_are_explicit_and_have_no_hidden_stop_target_defaults():
    required = {
        "planning_rule_version",
        "entry_low",
        "entry_high",
        "entry_reference",
        "stop_price",
        "target_1",
        "target_2",
        "target_3",
        "valid_until",
    }

    assert (
        required
        <= set(
            USResearchPlanTerms.model_fields
        )
    )

    values = terms().model_dump()

    for field in required:
        candidate = dict(values)
        del candidate[field]

        with pytest.raises(
            ValidationError
        ):
            USResearchPlanTerms(
                **candidate
            )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "entry_low": D("16"),
        },
        {
            "stop_price": D("15"),
        },
        {
            "target_1": D("15"),
        },
        {
            "target_2": D("16"),
            "target_3": D("15.5"),
        },
        {
            "entry_reference": D("NaN"),
        },
    ],
)
def test_invalid_plan_terms_fail_closed(
    changes,
):
    with pytest.raises(
        (ValueError, ValidationError)
    ):
        terms(
            **changes
        )


def test_expired_at_signal_plan_is_rejected():
    with pytest.raises(
        ValueError,
        match="validity",
    ):
        build_us_research_trade_plan(
            watch_signal(),
            terms(
                valid_until=AT,
            ),
        )


def test_materialized_legacy_trade_plan_preserves_deterministic_binding():
    source = plan()

    legacy = materialize_legacy_trade_plan(
        source
    )

    assert (
        legacy.trade_plan_id
        == source.trade_plan_id
    )
    assert (
        legacy.signal_id
        == source.signal_id
    )
    assert (
        legacy.symbol
        == source.canonical_symbol
    )
    assert (
        legacy.entry_reference
        == source.entry_reference
    )
    assert (
        legacy.stop_price
        == source.stop_price
    )
    assert (
        legacy.target_1
        == source.target_1
    )


def test_risk_admission_reuses_shared_engine_and_is_deterministic():
    first = admit()
    second = admit()

    assert isinstance(
        first,
        USResearchRiskAdmission,
    )

    assert first == second
    assert first.identity == second.identity
    assert (
        first.risk_decision_id
        == second.risk_decision_id
    )

    assert (
        first.decision
        == RiskDecisionType.APPROVE
    )
    assert first.quantity == 1000
    assert (
        first.approved_risk
        == D("1000")
    )

    assert (
        first.policy_identity
        == policy().identity
    )

    assert first.execution_allowed is False
    assert (
        first.validation_status
        == "UNVALIDATED"
    )


def test_stable_instrument_exposure_maps_to_legacy_symbol_cap():
    source_snapshot = snapshot(
        current_exposure_value=D("24500"),
        instrument_exposure_value=D("24500"),
    )

    result = admit(
        source_snapshot=source_snapshot,
    )

    caps = {
        item.name: item.quantity
        for item in result.quantity_caps
    }

    # 25% of 100k = 25k.
    # Existing stable instrument exposure = 24.5k.
    # Remaining capacity = 500 / entry 15 = 33 whole shares.
    assert caps[
        "SYMBOL_EXPOSURE"
    ] == 33

    assert result.quantity == 33
    assert (
        result.decision
        == RiskDecisionType.REDUCE
    )

    assert (
        result.stable_instrument_exposure_value
        == D("24500")
    )


def test_future_risk_snapshot_is_rejected():
    with pytest.raises(
        ValueError,
        match="future risk snapshot",
    ):
        admit(
            source_snapshot=snapshot(
                available_at=(
                    AT
                    + timedelta(seconds=1)
                ),
            )
        )


def test_noncanonical_utc_snapshot_is_rejected():
    equivalent_zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="datetime.timezone.utc",
    ):
        snapshot(
            available_at=AT.replace(
                tzinfo=equivalent_zero,
            )
        )


def test_risk_off_is_still_non_executable_block():
    result = admit(
        source_snapshot=snapshot(
            market_regime=(
                MarketRegimeType.RISK_OFF
            )
        )
    )

    assert (
        result.decision
        == RiskDecisionType.BLOCK
    )
    assert result.quantity == 0
    assert (
        result.approved_risk
        == D("0")
    )
    assert (
        "MARKET_REGIME_BLOCK"
        in result.blockers
    )
    assert result.execution_allowed is False


def test_materialized_risk_decision_matches_admission():
    result = admit()

    legacy = materialize_legacy_risk_decision(
        result
    )

    assert (
        legacy.risk_decision_id
        == result.risk_decision_id
    )
    assert (
        legacy.trade_plan_id
        == result.trade_plan_id
    )
    assert (
        legacy.quantity
        == result.quantity
    )
    assert (
        legacy.approved_risk
        == result.approved_risk
    )
    assert (
        legacy.policy_identity
        == result.policy_identity
    )


def test_copied_plan_corruption_is_revalidated():
    corrupted = plan().model_copy(
        update={
            "stop_price": D("16"),
        }
    )

    with pytest.raises(
        (ValueError, ValidationError)
    ):
        admit(
            source_plan=corrupted,
        )


def test_copied_snapshot_corruption_is_revalidated():
    corrupted = snapshot().model_copy(
        update={
            "reserved_cash": D("100001"),
        }
    )

    with pytest.raises(
        (ValueError, ValidationError)
    ):
        admit(
            source_snapshot=corrupted,
        )


def test_policy_boundary_requires_exact_policy():
    with pytest.raises(
        ValueError,
        match="exact RiskPolicy",
    ):
        admit_us_research_risk(
            plan(),
            policy=policy().model_dump(),
            snapshot=snapshot(),
            decision_at=AT,
            trade_state=TradeState.READY,
        )


def test_trade_state_is_explicit():
    with pytest.raises(
        ValueError,
        match="TradeState",
    ):
        admit_us_research_risk(
            plan(),
            policy=policy(),
            snapshot=snapshot(),
            decision_at=AT,
            trade_state="READY",
        )


def test_us7a_does_not_call_paper_simulator(
    monkeypatch,
):
    import app.paper.simulator as simulator

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "US7A must not execute M6"
        )

    monkeypatch.setattr(
        simulator,
        "simulate_paper",
        forbidden,
    )

    result = admit()

    assert result.execution_allowed is False


def test_risk_decision_cannot_precede_research_plan():
    with pytest.raises(
        ValueError,
        match="cannot precede research plan",
    ):
        admit(
            decision_at=(
                AT - timedelta(microseconds=1)
            ),
        )
