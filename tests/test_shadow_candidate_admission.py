"""Artificial admission fixtures only; no recommendations or market evidence."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.paper.shadow_candidate_admission import (
    ShadowCandidateAdmission,
    admit_strategy_candidate,
    strategy_candidate_sha256,
)
from app.strategies.contracts import Candidate


AT = datetime(
    2026,
    9,
    19,
    12,
    0,
    tzinfo=timezone.utc,
)


def strategy_candidate(**changes):
    values = dict(
        strategy_id="SWING",
        strategy_version="1",
        config_id="fixture-config",
        symbol="COMI",
        decision_time=AT,
        data_cutoff=AT,
        state="READY",
        entry_reference=Decimal("10"),
        stop_reference=Decimal("9"),
        target_reference=None,
        evidence_json='{"fixture":true}',
    )

    # M4 deliberately uses floats for planning references.
    values["entry_reference"] = 10.0
    values["stop_reference"] = 9.0

    return Candidate(**(values | changes))


def watch_admission(**changes):
    values = dict(
        candidate_id="fixture-comi",
        decision_status="WATCH",
        identity_status="UNKNOWN",
        instrument_id=None,
        thesis="explicit artificial thesis",
        context=("operator admission fixture",),
        technical_setup="explicit setup description",
        entry_condition="explicit paper condition",
        entry_rule="LONG_ENTRY_ZONE_TOUCH_V1",
        entry_low=None,
        entry_high=None,
        stop=None,
        targets=(),
        liquidity_status="UNKNOWN",
        liquidity_reason="not evaluated in fixture",
        paper_quantity=0,
        paper_risk_status="NOT_EVALUATED",
        evidence_ids=("evidence-fixture",),
    )
    return ShadowCandidateAdmission(
        **(values | changes)
    )


def buy_admission(**changes):
    values = dict(
        candidate_id="fixture-comi-buy",
        decision_status="BUY_CANDIDATE",
        identity_status="KNOWN",
        instrument_id=UUID(int=1),
        thesis="explicit artificial thesis",
        context=("operator admission fixture",),
        technical_setup="explicit setup description",
        entry_condition="explicit paper condition",
        entry_rule="LONG_ENTRY_ZONE_TOUCH_V1",
        entry_low=Decimal("100"),
        entry_high=Decimal("101"),
        stop=Decimal("95"),
        targets=(
            Decimal("110"),
            Decimal("115"),
        ),
        liquidity_status="PASS",
        liquidity_reason="artificial capacity fixture",
        paper_quantity=1,
        paper_risk_status="APPROVED",
        evidence_ids=("evidence-fixture",),
    )
    return ShadowCandidateAdmission(
        **(values | changes)
    )


def test_ready_strategy_does_not_imply_buy():
    result = admit_strategy_candidate(
        strategy_candidate(state="READY"),
        watch_admission(),
    )

    assert result.decision_status == "WATCH"
    assert result.paper_quantity == 0


def test_explicit_shadow_decision_is_preserved():
    result = admit_strategy_candidate(
        strategy_candidate(state="WATCH"),
        buy_admission(),
    )

    assert result.decision_status == "BUY_CANDIDATE"
    assert result.ticker == "COMI"
    assert result.confidence == "EXPERIMENTAL"
    assert (
        result.expected_holding_window
        == "NOT_YET_VALIDATED"
    )
    assert result.probability == "NOT_YET_VALIDATED"


def test_m4_float_planning_references_are_not_promoted():
    result = admit_strategy_candidate(
        strategy_candidate(
            entry_reference=1.0,
            stop_reference=0.5,
        ),
        buy_admission(),
    )

    assert result.entry_low == Decimal("100")
    assert result.entry_high == Decimal("101")
    assert result.stop == Decimal("95")
    assert result.targets == (
        Decimal("110"),
        Decimal("115"),
    )


def test_exact_m4_candidate_digest_is_preserved_in_context():
    candidate = strategy_candidate()

    result = admit_strategy_candidate(
        candidate,
        watch_admission(),
    )

    digest = strategy_candidate_sha256(candidate)

    assert digest in result.context[0]
    assert "SWING" in result.context[0]
    assert "state=READY" in result.context[0]
    assert "UNVALIDATED" in result.context[0]
    assert "execution_allowed=False" in result.context[0]


def test_digest_changes_when_strategy_candidate_changes():
    first = strategy_candidate(state="READY")
    second = strategy_candidate(state="WATCH")

    assert (
        strategy_candidate_sha256(first)
        != strategy_candidate_sha256(second)
    )


def test_shadow_candidate_validation_remains_authoritative():
    with pytest.raises(
        ValidationError,
        match="passed liquidity",
    ):
        admit_strategy_candidate(
            strategy_candidate(),
            buy_admission(
                liquidity_status="UNKNOWN",
            ),
        )


def test_watch_cannot_reserve_paper_capital():
    with pytest.raises(
        ValidationError,
        match="WATCH/AVOID",
    ):
        admit_strategy_candidate(
            strategy_candidate(),
            watch_admission(
                paper_quantity=1,
                paper_risk_status="APPROVED",
            ),
        )


def test_requires_exact_typed_inputs():
    candidate = strategy_candidate()
    admission = watch_admission()

    with pytest.raises(
        ValueError,
        match="exact M4",
    ):
        admit_strategy_candidate(
            candidate.model_dump(),
            admission,
        )

    with pytest.raises(
        ValueError,
        match="exact ShadowCandidateAdmission",
    ):
        admit_strategy_candidate(
            candidate,
            admission.model_dump(),
        )


def test_invalidated_strategy_setup_cannot_become_buy_candidate():
    """Explicit admission may be stricter than strategy state, never looser than an
    invalidated setup; the invalidated setup may still be recorded as WATCH/AVOID."""
    with pytest.raises(ValueError, match="invalidated"):
        admit_strategy_candidate(strategy_candidate(state="INVALIDATED"), buy_admission())
    result = admit_strategy_candidate(strategy_candidate(state="INVALIDATED"), watch_admission())
    assert (result.decision_status, result.paper_quantity) == ("WATCH", 0)
    assert "state=INVALIDATED" in result.context[0]
