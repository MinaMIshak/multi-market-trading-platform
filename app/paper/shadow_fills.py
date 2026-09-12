"""Authenticated forward simulated entry fills; never broker execution."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_records import ShadowWatchlist
from app.paper.shadow_triggers import audit_trigger_event
from app.research.historical_evidence import HistoricalEvidencePackage, require_historical_evidence


SLIPPAGE_FIELDS = {"entry_slippage_bps"}
COST_FIELDS = {"cost_bps_per_side", "fixed_cost_per_side", "currency"}
PARTICIPATION_FIELDS = {"max_volume_participation_pct"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("datetime.timezone.utc required")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


class ShadowFillPolicy(BaseModel):
    """Predeclared, evidenced entry economics for one market."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-fill-policy-v1"] = "shadow-fill-policy-v1"
    market: Literal["EGX", "US"]
    currency: Literal["EGP", "USD"]
    entry_slippage_bps: Decimal = Field(ge=0)
    cost_bps_per_side: Decimal = Field(ge=0)
    fixed_cost_per_side: Decimal = Field(ge=0)
    max_volume_participation_pct: Decimal = Field(gt=0, le=1)
    slippage_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    participation_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "entry_slippage_bps", "cost_bps_per_side", "fixed_cost_per_side",
        "max_volume_participation_pct", mode="before",
    )
    @classmethod
    def exact_decimal(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("finite exact Decimal required")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if (self.market == "US") != (self.currency == "USD"):
            raise ValueError("market/currency mismatch")
        return self


class ShadowEntryFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-entry-fill-v1"] = "shadow-entry-fill-v1"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = LABEL
    candidate_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    side: Literal["BUY"] = "BUY"
    quantity: int = Field(gt=0)
    bar_sequence: int = Field(ge=1)
    interval_start: datetime
    interval_end: datetime
    known_at: datetime
    raw_price: Decimal = Field(gt=0)
    fill_price: Decimal = Field(gt=0)
    notional: Decimal = Field(gt=0)
    entry_cost: Decimal = Field(ge=0)
    currency: Literal["EGP", "USD"]

    @field_validator("interval_start", "interval_end", "known_at", mode="before")
    @classmethod
    def exact_times(cls, value):
        return _utc(value)

    @field_validator("raw_price", "fill_price", "notional", "entry_cost", mode="before")
    @classmethod
    def exact_values(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("finite exact Decimal required")
        return value


def _packages(
    policy: ShadowFillPolicy, supplied: tuple[HistoricalEvidencePackage, ...],
    cutoff: datetime,
) -> dict[str, HistoricalEvidencePackage]:
    if type(supplied) is not tuple:
        raise ValueError("exact fill evidence package tuple required")
    result = {}
    for package in supplied:
        if type(package) is not HistoricalEvidencePackage or package.identity in result:
            raise ValueError("exact unique fill evidence packages required")
        result[package.identity] = package
    expected = {
        policy.slippage_evidence_package_id, policy.cost_evidence_package_id,
        policy.participation_evidence_package_id,
    }
    if set(result) != expected:
        raise ValueError("fill evidence packages must match references exactly")
    for package in result.values():
        require_historical_evidence(package, decision_at=cutoff, research_built_at=cutoff)
    requirements = (
        (policy.slippage_evidence_package_id, SLIPPAGE_FIELDS),
        (policy.cost_evidence_package_id, COST_FIELDS),
        (policy.participation_evidence_package_id, PARTICIPATION_FIELDS),
    )
    for identity, fields in requirements:
        if not fields <= set(result[identity].evidence.covered_fields):
            raise ValueError("evidence package does not cover fill-policy fields")
    return result


def evaluate_entry_fill(
    watchlist: ShadowWatchlist, facts: ForwardFactBundle, trigger_event: dict,
    policy: ShadowFillPolicy,
) -> ShadowEntryFill | None:
    """Return a full simulated fill or None for evidenced capacity rejection."""
    if type(policy) is not ShadowFillPolicy:
        raise ValueError("exact fill policy required")
    policy = ShadowFillPolicy.model_validate(policy.model_dump(mode="python"))
    candidate = {item.candidate_id: item for item in watchlist.candidates}.get(facts.candidate_id)
    evaluation = trigger_event["evaluation"]
    if candidate is None or evaluation["status"] != "TRIGGERED":
        raise ValueError("fill requires an unambiguous authenticated trigger")
    if policy.market != watchlist.session.market:
        raise ValueError("fill policy market mismatch")
    sequence = evaluation["trigger_sequence"]
    bar = next((item for item in facts.bars if item.sequence == sequence), None)
    if bar is None:
        raise ValueError("trigger bar is absent")
    capacity = int(Decimal(bar.volume) * policy.max_volume_participation_pct)
    if candidate.paper_quantity > capacity:
        return None
    raw = Decimal(evaluation["trigger_reference_price"])
    with localcontext(Context(prec=34)):
        price = raw * (Decimal(1) + policy.entry_slippage_bps / Decimal(10000))
        assert candidate.stop is not None and candidate.targets
        if not candidate.stop < price < candidate.targets[0]:
            raise ValueError("slipped fill outside candidate geometry")
        notional = price * candidate.paper_quantity
        cost = notional * policy.cost_bps_per_side / Decimal(10000) + policy.fixed_cost_per_side
    return ShadowEntryFill(
        candidate_id=candidate.candidate_id, instrument_id=str(candidate.instrument_id),
        ticker=candidate.ticker, quantity=candidate.paper_quantity, bar_sequence=bar.sequence,
        interval_start=bar.interval_start, interval_end=bar.interval_end,
        known_at=bar.available_at, raw_price=raw, fill_price=price,
        notional=notional, entry_cost=cost, currency=policy.currency,
    )


def _basis(trigger_event: dict, policy: ShadowFillPolicy, package_ids: list[str],
           fill: ShadowEntryFill | None) -> dict:
    return {
        "schema_version": "shadow-fill-event-v1", "label": LABEL,
        "event_type": "ENTRY_FILL_EVALUATED", "scoring": "NOT SCORED",
        "execution_status": "SIMULATED ENTRY FILL CREATED" if fill else "NO FILL: INSUFFICIENT CAPACITY",
        "position_status": "POSITION EVENT NOT CREATED",
        "trigger_event_id": trigger_event["event_id"],
        "trigger_event_sha256": hashlib.sha256(_canonical(trigger_event)).hexdigest(),
        "fill_package_ids": package_ids, "policy": policy.model_dump(mode="json"),
        "fill": None if fill is None else fill.model_dump(mode="json"),
    }


def append_fill_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    if type(policy) is not ShadowFillPolicy:
        raise ValueError("exact fill policy required")
    policy = ShadowFillPolicy.model_validate(policy.model_dump(mode="python"))
    trigger = audit_trigger_event(directory, watchlist, watchlist_packages, facts, fact_packages)
    packages = _packages(policy, fill_packages, watchlist.information_cutoff)
    fill = evaluate_entry_fill(watchlist, facts, trigger, policy)
    basis = _basis(trigger, policy, sorted(packages), fill)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()
    recorded_at = _now()
    trigger_at = _utc(datetime.fromisoformat(trigger["recorded_at"]))
    if recorded_at < trigger_at:
        raise ValueError("fill evaluation precedes trigger event")
    payload = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    path = _publish_once(Path(directory) / "fill-events" / f"{event_id}.json", payload)
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during fill publication")
    return path


def audit_fill_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    if type(policy) is not ShadowFillPolicy:
        raise ValueError("exact fill policy required")
    policy = ShadowFillPolicy.model_validate(policy.model_dump(mode="python"))
    trigger = audit_trigger_event(directory, watchlist, watchlist_packages, facts, fact_packages)
    packages = _packages(policy, fill_packages, watchlist.information_cutoff)
    fill = evaluate_entry_fill(watchlist, facts, trigger, policy)
    basis = _basis(trigger, policy, sorted(packages), fill)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate fill-event field")
            result[key] = value
        return result

    event = json.loads(
        (Path(directory) / "fill-events" / f"{event_id}.json").read_bytes(),
        object_pairs_hook=unique_object,
    )
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected fill-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    trigger_at = _utc(datetime.fromisoformat(trigger["recorded_at"]))
    expected = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    if not trigger_at <= recorded_at <= _now() or event != expected:
        raise ValueError("fill event does not bind authenticated inputs")
    return event
