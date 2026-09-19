"""Explicit admission from M4 research candidates into Shadow paper records.

This boundary deliberately does not infer BUY/WATCH/AVOID from an M4 strategy
state. M4 candidates remain UNVALIDATED with execution_allowed=False. Any
Shadow decision, sizing, liquidity or price geometry must be supplied explicitly.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.paper.shadow_records import ShadowCandidate
from app.strategies.contracts import Candidate as StrategyCandidate


class ShadowCandidateAdmission(BaseModel):
    """Explicit paper-only admission data; never inferred from strategy state."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )

    candidate_id: str = Field(min_length=1)
    decision_status: Literal[
        "BUY_CANDIDATE",
        "WATCH",
        "AVOID",
    ]

    identity_status: Literal["KNOWN", "UNKNOWN"]
    instrument_id: UUID | None

    thesis: str = Field(min_length=1)
    context: tuple[str, ...]
    technical_setup: str = Field(min_length=1)
    entry_condition: str = Field(min_length=1)

    entry_rule: Literal["LONG_ENTRY_ZONE_TOUCH_V1"]

    entry_low: Decimal | None = Field(
        default=None,
        gt=0,
    )
    entry_high: Decimal | None = Field(
        default=None,
        gt=0,
    )
    stop: Decimal | None = Field(
        default=None,
        gt=0,
    )
    targets: tuple[Decimal, ...]

    liquidity_status: Literal[
        "PASS",
        "BLOCKED",
        "UNKNOWN",
    ]
    liquidity_reason: str = Field(min_length=1)

    paper_quantity: int = Field(ge=0)
    paper_risk_status: Literal[
        "APPROVED",
        "BLOCKED",
        "NOT_EVALUATED",
    ]

    evidence_ids: tuple[str, ...] = Field(
        min_length=1,
    )


def strategy_candidate_sha256(
    candidate: StrategyCandidate,
) -> str:
    """Digest the exact typed M4 candidate used at this admission boundary."""

    if type(candidate) is not StrategyCandidate:
        raise ValueError(
            "exact M4 StrategyCandidate required"
        )

    payload = json.dumps(
        candidate.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def admit_strategy_candidate(
    candidate: StrategyCandidate,
    admission: ShadowCandidateAdmission,
) -> ShadowCandidate:
    """Create one Shadow candidate from an explicit admission decision.

    Strategy state, float planning references and execution flags are never
    promoted into Shadow semantics automatically.
    """

    if type(candidate) is not StrategyCandidate:
        raise ValueError(
            "exact M4 StrategyCandidate required"
        )

    if type(admission) is not ShadowCandidateAdmission:
        raise ValueError(
            "exact ShadowCandidateAdmission required"
        )

    if (
        candidate.validation_status != "UNVALIDATED"
        or candidate.execution_allowed is not False
    ):
        raise ValueError(
            "M4 candidate must remain unvalidated "
            "and execution-disabled"
        )

    digest = strategy_candidate_sha256(candidate)

    provenance = (
        "M4 research candidate "
        f"{candidate.strategy_id} "
        f"v{candidate.strategy_version} "
        f"state={candidate.state} "
        f"sha256={digest}; "
        "UNVALIDATED; execution_allowed=False"
    )

    return ShadowCandidate(
        candidate_id=admission.candidate_id,
        decision_status=admission.decision_status,
        identity_status=admission.identity_status,
        instrument_id=admission.instrument_id,
        ticker=candidate.symbol,
        thesis=admission.thesis,
        context=(
            provenance,
            *admission.context,
        ),
        technical_setup=admission.technical_setup,
        entry_condition=admission.entry_condition,
        entry_rule=admission.entry_rule,
        entry_low=admission.entry_low,
        entry_high=admission.entry_high,
        stop=admission.stop,
        targets=admission.targets,
        expected_holding_window="NOT_YET_VALIDATED",
        confidence="EXPERIMENTAL",
        probability="NOT_YET_VALIDATED",
        liquidity_status=admission.liquidity_status,
        liquidity_reason=admission.liquidity_reason,
        paper_quantity=admission.paper_quantity,
        paper_risk_status=admission.paper_risk_status,
        evidence_ids=admission.evidence_ids,
    )
