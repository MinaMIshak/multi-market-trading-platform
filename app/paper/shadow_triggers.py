"""Auditable forward trigger evaluation; a trigger is never a simulated fill."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_facts import ForwardFactBundle, audit_forward_fact_event
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("datetime.timezone.utc required")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


class TriggerEvaluation(BaseModel):
    """A deterministic observation result, without queue or fill inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-trigger-evaluation-v1"] = "shadow-trigger-evaluation-v1"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = LABEL
    candidate_id: str = Field(min_length=1)
    status: Literal[
        "NOT_TRIGGERED", "INVALIDATED_OPEN_GAP", "TARGET_PASSED_OPEN_GAP",
        "INVALIDATED_BEFORE_ENTRY", "TARGET_PASSED_BEFORE_ENTRY",
        "TRIGGERED", "TRIGGERED_AMBIGUOUS_BAR", "UNSUPPORTED_CORPORATE_ACTION",
    ]
    evaluated_through_sequence: int = Field(ge=1)
    evaluated_through_available_at: datetime
    trigger_sequence: int | None = Field(default=None, ge=1)
    trigger_reference_price: Decimal | None = Field(default=None, gt=0)
    reason: Literal[
        "ENTRY_ZONE_NOT_TOUCHED", "OPEN_AT_OR_BELOW_STOP_BEFORE_ENTRY",
        "OPEN_AT_OR_ABOVE_TARGET_BEFORE_ENTRY", "ENTRY_ZONE_TOUCHED",
        "STOP_TOUCHED_WITHOUT_ENTRY", "TARGET_TOUCHED_WITHOUT_ENTRY",
        "ENTRY_TOUCHED_WITH_PATH_ORDER_UNKNOWN", "UNSUPPORTED_CORPORATE_ACTION",
    ]

    @field_validator("evaluated_through_available_at", mode="before")
    @classmethod
    def exact_time(cls, value):
        return _utc(value)

    @field_validator("trigger_reference_price", mode="before")
    @classmethod
    def exact_price(cls, value):
        if value is not None and (type(value) is not Decimal or not value.is_finite()):
            raise ValueError("finite exact Decimal required")
        return value

    @model_validator(mode="after")
    def coherent(self):
        triggered = self.status in {"TRIGGERED", "TRIGGERED_AMBIGUOUS_BAR"}
        if triggered != (self.trigger_sequence is not None and self.trigger_reference_price is not None):
            raise ValueError("trigger fields do not match status")
        if self.trigger_sequence is not None and self.trigger_sequence > self.evaluated_through_sequence:
            raise ValueError("trigger follows evaluation boundary")
        return self


def evaluate_trigger(watchlist: ShadowWatchlist, facts: ForwardFactBundle) -> TriggerEvaluation:
    """Evaluate the declared long entry zone conservatively from final raw bars."""
    if type(watchlist) is not ShadowWatchlist or type(facts) is not ForwardFactBundle:
        raise ValueError("exact watchlist and fact bundle required")
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    facts = ForwardFactBundle.model_validate(facts.model_dump(mode="python"))
    candidates = {item.candidate_id: item for item in watchlist.candidates}
    candidate = candidates.get(facts.candidate_id)
    if candidate is None or candidate.decision_status != "BUY_CANDIDATE":
        raise ValueError("trigger evaluation requires an exact BUY_CANDIDATE")
    if candidate.instrument_id != facts.identity.instrument_id or candidate.ticker != facts.identity.ticker:
        raise ValueError("trigger evaluation identity mismatch")
    assert candidate.entry_low is not None and candidate.entry_high is not None
    assert candidate.stop is not None and candidate.targets

    status = "NOT_TRIGGERED"
    reason = "ENTRY_ZONE_NOT_TOUCHED"
    trigger_sequence = None
    trigger_price = None
    # CASH_DIVIDEND is price-only, as in the exit gate; any other action effective
    # on this session invalidates the pre-open zone/stop/target raw-price basis.
    transformed = any(action.effective_date == facts.session.market_date
                      and action.action_type != "CASH_DIVIDEND"
                      for action in facts.action_coverage.actions)
    if transformed:
        status = reason = "UNSUPPORTED_CORPORATE_ACTION"
    for bar in () if transformed else facts.bars:
        if bar.open <= candidate.stop:
            status, reason = "INVALIDATED_OPEN_GAP", "OPEN_AT_OR_BELOW_STOP_BEFORE_ENTRY"
            break
        if bar.open >= candidate.targets[0]:
            status, reason = "TARGET_PASSED_OPEN_GAP", "OPEN_AT_OR_ABOVE_TARGET_BEFORE_ENTRY"
            break
        intersects = bar.low <= candidate.entry_high and bar.high >= candidate.entry_low
        if not intersects:
            # A wholly outside-zone bar can retire the thesis before a later
            # bar reaches entry. No intrabar ordering assumption is needed.
            if bar.low <= candidate.stop:
                status, reason = "INVALIDATED_BEFORE_ENTRY", "STOP_TOUCHED_WITHOUT_ENTRY"
                break
            if bar.high >= candidate.targets[0]:
                status, reason = "TARGET_PASSED_BEFORE_ENTRY", "TARGET_TOUCHED_WITHOUT_ENTRY"
                break
            continue
        trigger_sequence = bar.sequence
        trigger_price = (bar.open if candidate.entry_low <= bar.open <= candidate.entry_high
                         else candidate.entry_low if bar.open < candidate.entry_low
                         else candidate.entry_high)
        path_ambiguous = (
            (bar.low <= candidate.stop and bar.high >= candidate.targets[0])
            or (bar.open < candidate.entry_low and bar.low <= candidate.stop)
            or (bar.open > candidate.entry_high and bar.high >= candidate.targets[0])
        )
        if path_ambiguous:
            status = "TRIGGERED_AMBIGUOUS_BAR"
            reason = "ENTRY_TOUCHED_WITH_PATH_ORDER_UNKNOWN"
        else:
            status, reason = "TRIGGERED", "ENTRY_ZONE_TOUCHED"
        break
    last = facts.bars[-1]
    return TriggerEvaluation(
        candidate_id=facts.candidate_id, status=status,
        evaluated_through_sequence=last.sequence,
        evaluated_through_available_at=last.available_at,
        trigger_sequence=trigger_sequence, trigger_reference_price=trigger_price,
        reason=reason,
    )


def _basis(fact_event: dict, evaluation: TriggerEvaluation) -> dict:
    return {
        "schema_version": "shadow-trigger-event-v1", "label": LABEL,
        "event_type": "TRIGGER_EVALUATED", "scoring": "NOT SCORED",
        "execution_status": "NO FILL OR POSITION CREATED",
        "fact_event_id": fact_event["event_id"],
        "fact_event_sha256": hashlib.sha256(_canonical(fact_event)).hexdigest(),
        "evaluation": evaluation.model_dump(mode="json"),
    }


def append_trigger_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Audit exact upstream facts, evaluate once, and append immutable result."""
    fact_event = audit_forward_fact_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
    )
    evaluation = evaluate_trigger(watchlist, facts)
    recorded_at = _now()
    if not evaluation.evaluated_through_available_at <= recorded_at:
        raise ValueError("trigger evaluation precedes fact availability")
    if recorded_at < _utc(datetime.fromisoformat(fact_event["recorded_at"])):
        raise ValueError("trigger evaluation precedes fact event")
    basis = _basis(fact_event, evaluation)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()
    payload = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    path = _publish_once(Path(directory) / "trigger-events" / f"{event_id}.json", payload)
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during trigger publication")
    return path


def audit_trigger_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Verify a trigger event against exact caller-supplied upstream inputs."""
    fact_event = audit_forward_fact_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
    )
    evaluation = evaluate_trigger(watchlist, facts)
    basis = _basis(fact_event, evaluation)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate trigger-event field")
            result[key] = value
        return result

    event = json.loads(
        (Path(directory) / "trigger-events" / f"{event_id}.json").read_bytes(),
        object_pairs_hook=unique_object,
    )
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected trigger-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    fact_recorded_at = _utc(datetime.fromisoformat(fact_event["recorded_at"]))
    if not max(evaluation.evaluated_through_available_at, fact_recorded_at) <= recorded_at <= _now():
        raise ValueError("invalid trigger-event clock ordering")
    expected = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    if event != expected:
        raise ValueError("trigger event does not bind evaluation")
    return event
