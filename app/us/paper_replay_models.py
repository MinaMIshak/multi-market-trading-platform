"""US7C-B immutable binding contracts; no acquisition or execution side effects."""
from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, InstanceOf, field_validator, model_validator

from app.paper.models import PaperExecutionConfig
from app.paper.replay_models import PaperReplayInput, PaperReplayResult
from app.risk import RiskPolicy
from app.strategies.contracts import Contract
from app.us.contracts import US_MARKET_TIMEZONE
from app.us.historical_actions import AdmittedUSCorporateActionHistory
from app.us.historical_identity import AdmittedUSListingHistory
from app.us.historical_intraday import AdmittedUSIntradaySession
from app.us.historical_session import AdmittedUSSessionHistory
from app.us.research_adapter import USSwingResearchResult
from app.us.research_planning import (
    USResearchPlanTerms, USResearchRiskAdmission, USResearchRiskSnapshot,
    USResearchTradePlan,
)

ACTION_POLICY = "us-replay-actions-date-inclusive-v1"


def semantic_hash(payload) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def exact_utc(value):
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("exact datetime.timezone.utc required")
    return value


def exact_equal(expected, supplied):
    """Structural equality that cannot equate Decimal/float or bool/int copies."""
    if type(expected) is not type(supplied):
        return False
    if isinstance(expected, BaseModel):
        names = type(expected).model_fields
        return (set(supplied.__dict__) == set(names)
                and all(exact_equal(getattr(expected, n), getattr(supplied, n)) for n in names))
    if is_dataclass(expected):
        names = tuple(f.name for f in fields(expected))
        return (set(supplied.__dict__) == set(names)
                and all(exact_equal(getattr(expected, n), getattr(supplied, n)) for n in names))
    if isinstance(expected, (tuple, list)):
        return len(expected) == len(supplied) and all(exact_equal(a, b) for a, b in zip(expected, supplied))
    if isinstance(expected, dict):
        return expected.keys() == supplied.keys() and all(exact_equal(expected[k], supplied[k]) for k in expected)
    if isinstance(expected, Decimal):
        return expected.as_tuple() == supplied.as_tuple()
    if isinstance(expected, datetime):
        return expected == supplied and expected.tzinfo is supplied.tzinfo
    return expected == supplied


def canonical(value, kind):
    if type(value) is not kind:
        raise ValueError(f"exact canonical {kind.__name__} required")
    if set(value.__dict__) != set(kind.model_fields):
        raise ValueError(f"invalid {kind.__name__} field set")
    rebuilt = kind.model_validate(
        {name: getattr(value, name) for name in kind.model_fields}, strict=True,
    )
    if not exact_equal(rebuilt, value):
        raise ValueError(f"noncanonical {kind.__name__}; repair forbidden")
    return rebuilt


class USReplayCheckpoint(Contract):
    """Explicit evidence-complete prefix endpoint on one local calendar date."""
    market_date: date
    path_complete_through_at: datetime

    @field_validator("market_date", mode="before")
    @classmethod
    def exact_date(cls, value):
        if type(value) is not date:
            raise ValueError("exact date required")
        return value

    @field_validator("path_complete_through_at", mode="before")
    @classmethod
    def utc(cls, value):
        return exact_utc(value)

    @model_validator(mode="after")
    def local_date(self):
        if self.path_complete_through_at.astimezone(ZoneInfo(US_MARKET_TIMEZONE)).date() != self.market_date:
            raise ValueError("checkpoint must end on its explicit local date")
        return self


class USPaperReplayRequest(Contract):
    schema_version: Literal["us-paper-replay-request-v1"] = "us-paper-replay-request-v1"
    signal: USSwingResearchResult
    terms: USResearchPlanTerms
    plan: USResearchTradePlan
    policy: RiskPolicy
    risk_snapshot: USResearchRiskSnapshot
    risk_admission: USResearchRiskAdmission
    execution_config: PaperExecutionConfig
    listing_history: InstanceOf[AdmittedUSListingHistory]
    session_history: InstanceOf[AdmittedUSSessionHistory]
    action_history: InstanceOf[AdmittedUSCorporateActionHistory]
    intraday_sessions: tuple[InstanceOf[AdmittedUSIntradaySession], ...]
    checkpoints: tuple[USReplayCheckpoint, ...] = Field(min_length=1)
    evidence_cutoff_at: datetime
    research_built_at: datetime
    action_policy_version: Literal["us-replay-actions-date-inclusive-v1"] = ACTION_POLICY

    @field_validator("signal", "terms", "plan", "policy", "risk_snapshot",
                     "risk_admission", "execution_config", mode="before")
    @classmethod
    def models(cls, value, info):
        kind = {
            "signal": USSwingResearchResult, "terms": USResearchPlanTerms,
            "plan": USResearchTradePlan, "policy": RiskPolicy,
            "risk_snapshot": USResearchRiskSnapshot,
            "risk_admission": USResearchRiskAdmission,
            "execution_config": PaperExecutionConfig,
        }[info.field_name]
        return canonical(value, kind)

    @field_validator("listing_history", "session_history", "action_history", mode="before")
    @classmethod
    def histories(cls, value, info):
        kind = {"listing_history": AdmittedUSListingHistory,
                "session_history": AdmittedUSSessionHistory,
                "action_history": AdmittedUSCorporateActionHistory}[info.field_name]
        if type(value) is not kind:
            raise ValueError(f"exact {kind.__name__} required")
        return value

    @field_validator("intraday_sessions", mode="before")
    @classmethod
    def sessions(cls, value):
        if type(value) is not tuple or any(type(row) is not AdmittedUSIntradaySession for row in value):
            raise ValueError("tuple of exact AdmittedUSIntradaySession required")
        return value

    @field_validator("checkpoints", mode="before")
    @classmethod
    def canonical_checkpoints(cls, value):
        if type(value) is not tuple:
            raise ValueError("tuple of canonical checkpoints required")
        return tuple(canonical(row, USReplayCheckpoint) for row in value)

    @field_validator("evidence_cutoff_at", "research_built_at", mode="before")
    @classmethod
    def utc(cls, value):
        return exact_utc(value)

    @property
    def identity(self):
        # Only local build clocks are excluded; original evidence identities
        # (including receipt/review provenance) remain bound exactly.
        return semantic_hash({
            "schema_version": self.schema_version,
            "action_policy_version": self.action_policy_version,
            "decision_inputs": {name: getattr(self, name).model_dump(mode="json")
                                for name in ("signal", "terms", "plan", "policy",
                                             "risk_snapshot", "risk_admission", "execution_config")},
            "listing_history_id": self.listing_history.identity,
            "session_history_id": self.session_history.identity,
            "action_history_id": self.action_history.identity,
            "intraday_session_ids": [row.identity for row in self.intraday_sessions],
            "checkpoints": [row.model_dump(mode="json") for row in self.checkpoints],
            "evidence_cutoff_at": self.evidence_cutoff_at.isoformat(),
        })


class USReplayBarProvenance(Contract):
    session_id: str
    session_sequence: int
    source_id: str
    provenance_id: str
    source_provider: str
    provider_symbol: str
    source_instrument_key: str
    source_row_number: int
    segment_fact_id: str
    evidence_package_id: str


class USReplayProvenance(Contract):
    request_id: str
    action_policy_version: Literal["us-replay-actions-date-inclusive-v1"] = ACTION_POLICY
    dividend_policy: Literal["RAW_PRICE_ONLY_NO_CASH_CREDIT"] = "RAW_PRICE_ONLY_NO_CASH_CREDIT"
    consumed_checkpoints: tuple[USReplayCheckpoint, ...]
    session_fact_ids: tuple[str, ...]
    intraday_session_ids: tuple[str, ...]
    full_session_proof_ids: tuple[str, ...]
    bar_lineage: tuple[USReplayBarProvenance, ...]
    checked_action_dates: tuple[date, ...]
    checked_action_ids: tuple[str, ...]


class USPaperReplayResult(Contract):
    schema_version: Literal["us-paper-replay-result-v1"] = "us-paper-replay-result-v1"
    status: Literal["REPLAYED", "INCOMPATIBLE"]
    rejection_code: str | None = None
    rejection_action_ids: tuple[str, ...] = ()
    replay_input: PaperReplayInput | None = None
    replay_result: PaperReplayResult | None = None
    provenance: USReplayProvenance

    @field_validator("replay_input", "replay_result", "provenance", mode="before")
    @classmethod
    def objects(cls, value, info):
        if value is None and info.field_name != "provenance":
            return None
        return canonical(value, {"replay_input": PaperReplayInput,
                                 "replay_result": PaperReplayResult,
                                 "provenance": USReplayProvenance}[info.field_name])

    @model_validator(mode="after")
    def state(self):
        if self.status == "INCOMPATIBLE":
            if not self.rejection_code or self.replay_input is not None or self.replay_result is not None:
                raise ValueError("incompatible replay must have a reason and no execution artifacts")
        elif (self.rejection_code is not None or self.rejection_action_ids
              or self.replay_input is None or self.replay_result is None):
            raise ValueError("replayed result requires exact input/result and no rejection")
        return self

    @property
    def identity(self):
        return semantic_hash(self.model_dump(mode="json"))
