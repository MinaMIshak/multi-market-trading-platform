"""Conservative authenticated paper exits; no portfolio or performance aggregation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_continuations import ForwardContinuationBundle
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_facts import audit_forward_fact_event
from app.paper.shadow_fills import ShadowFillPolicy, _canonical, _utc
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_positions import audit_position_open_event
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage, require_historical_evidence

EXIT_SLIPPAGE_FIELDS = {"stop_slippage_bps", "target_slippage_bps"}
EXIT_PARTICIPATION_FIELDS = {"max_volume_participation_pct"}
EXIT_COST_FIELDS = {"cost_bps_per_side", "fixed_cost_per_side", "currency"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ShadowExitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-exit-policy-v2"] = "shadow-exit-policy-v2"
    market: Literal["EGX", "US"]
    currency: Literal["EGP", "USD"]
    stop_slippage_bps: Decimal = Field(ge=0)
    target_slippage_bps: Decimal = Field(ge=0)
    cost_bps_per_side: Decimal = Field(ge=0)
    fixed_cost_per_side: Decimal = Field(ge=0)
    max_volume_participation_pct: Decimal = Field(gt=0, le=1)
    participation_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    slippage_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("stop_slippage_bps", "target_slippage_bps", "cost_bps_per_side",
                     "fixed_cost_per_side", "max_volume_participation_pct", mode="before")
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


def _packages(policy, supplied, cutoff):
    if type(supplied) is not tuple:
        raise ValueError("exact exit evidence package tuple required")
    found = {}
    for package in supplied:
        if type(package) is not HistoricalEvidencePackage or package.identity in found:
            raise ValueError("exact unique exit evidence packages required")
        found[package.identity] = package
    expected = {policy.slippage_evidence_package_id, policy.cost_evidence_package_id,
                policy.participation_evidence_package_id}
    if set(found) != expected:
        raise ValueError("exit evidence packages must match references exactly")
    for package in found.values():
        require_historical_evidence(package, decision_at=cutoff, research_built_at=cutoff)
    for identity, fields in ((policy.slippage_evidence_package_id, EXIT_SLIPPAGE_FIELDS),
                             (policy.cost_evidence_package_id, EXIT_COST_FIELDS),
                             (policy.participation_evidence_package_id, EXIT_PARTICIPATION_FIELDS)):
        if not fields <= set(found[identity].evidence.covered_fields):
            raise ValueError("evidence package does not cover exit-policy fields")
    return found


def evaluate_exit(position: dict, facts: ForwardFactBundle, policy: ShadowExitPolicy) -> dict:
    entry = position["entry"]
    if (policy.market != facts.session.market or policy.currency != entry["currency"]
            or str(facts.identity.instrument_id) != entry["instrument_id"]
            or facts.identity.ticker != entry["ticker"]):
        raise ValueError("exit inputs do not bind open position")
    stop, target = Decimal(position["initial_stop"]), Decimal(position["initial_targets"][0])
    entry_sequence = entry["bar_sequence"]
    outcome, reason, bar, raw = "OPEN", "NO_EXIT_OBSERVED", None, None
    for item in facts.bars:
        if item.sequence < entry_sequence:
            continue
        entry_at_open = (item.sequence == entry_sequence
                         and Decimal(entry["raw_price"]) == item.open)
        if item.sequence == entry_sequence and not entry_at_open:
            # The accepted trigger proves stop/target did not precede entry, but does not
            # establish their relative post-entry order if both occur.
            if item.low <= stop and item.high >= target:
                return {"status": "UNKNOWN", "reason": "ENTRY_BAR_STOP_TARGET_ORDER_UNKNOWN",
                        "evaluated_through_sequence": facts.bars[-1].sequence, "exit": None}
        if item.open <= stop:
            outcome, reason, bar, raw = "CLOSED", "STOP_GAP", item, item.open
            break
        if item.open >= target:
            outcome, reason, bar, raw = "CLOSED", "TARGET_GAP", item, target
            break
        hit_stop, hit_target = item.low <= stop, item.high >= target
        if hit_stop and hit_target:
            return {"status": "UNKNOWN", "reason": "STOP_TARGET_ORDER_UNKNOWN",
                    "evaluated_through_sequence": facts.bars[-1].sequence, "exit": None}
        if hit_stop:
            outcome, reason, bar, raw = "CLOSED", "STOP", item, stop
            break
        if hit_target:
            outcome, reason, bar, raw = "CLOSED", "TARGET_1", item, target
            break
    if outcome == "OPEN":
        return {"status": outcome, "reason": reason,
                "evaluated_through_sequence": facts.bars[-1].sequence, "exit": None}
    # Whole-bar participation is an upper bound, not liquidity at the exit price.
    # Count entry and exit together when both use this same observed bar.
    with localcontext(Context(prec=34)):
        capacity = int(Decimal(bar.volume) * policy.max_volume_participation_pct)
    required = entry["quantity"] * (2 if bar.sequence == entry_sequence else 1)
    if capacity < required:
        return {"status": "UNKNOWN", "reason": "INSUFFICIENT_EXIT_CAPACITY",
                "evaluated_through_sequence": bar.sequence, "exit": None}
    bps = policy.stop_slippage_bps if reason.startswith("STOP") else policy.target_slippage_bps
    with localcontext(Context(prec=34)):
        price = raw * (Decimal(1) - bps / Decimal(10000))
        if price <= 0:
            raise ValueError("exit slippage produces nonpositive price")
        notional = price * entry["quantity"]
        cost = notional * policy.cost_bps_per_side / Decimal(10000) + policy.fixed_cost_per_side
    return {"status": outcome, "reason": reason,
            "evaluated_through_sequence": bar.sequence,
            "exit": {"quantity": entry["quantity"], "bar_sequence": bar.sequence,
                     "interval_start": bar.interval_start.isoformat(),
                     "interval_end": bar.interval_end.isoformat(),
                     "known_at": bar.available_at.isoformat(), "raw_price": str(raw),
                     "fill_price": str(price), "notional": str(notional),
                     "exit_cost": str(cost), "currency": policy.currency}}


def evaluate_continuation_exit(position: dict, facts: ForwardContinuationBundle,
                               policy: ShadowExitPolicy) -> dict:
    """Evaluate one authenticated later session without reusing entry-session sequence."""
    facts = ForwardContinuationBundle.model_validate(facts.model_dump(mode="python"))
    policy = ShadowExitPolicy.model_validate(policy.model_dump(mode="python"))
    entry = position["entry"]
    target_session = facts.calendar_days[-1]
    if (policy.market != target_session.market or policy.currency != entry["currency"]
            or str(facts.identity.instrument_id) != entry["instrument_id"]
            or facts.identity.ticker != entry["ticker"]):
        raise ValueError("continuation exit inputs do not bind open position")
    stop, target = Decimal(position["initial_stop"]), Decimal(position["initial_targets"][0])
    reason = None
    bar = None
    raw = None
    for item in facts.bars:
        if item.open <= stop:
            reason, bar, raw = "STOP_GAP", item, item.open
            break
        if item.open >= target:
            reason, bar, raw = "TARGET_GAP", item, target
            break
        hit_stop, hit_target = item.low <= stop, item.high >= target
        if hit_stop and hit_target:
            return {"status": "UNKNOWN", "reason": "STOP_TARGET_ORDER_UNKNOWN",
                    "market_date": target_session.market_date.isoformat(),
                    "evaluated_through_sequence": facts.bars[-1].sequence, "exit": None}
        if hit_stop:
            reason, bar, raw = "STOP", item, stop
            break
        if hit_target:
            reason, bar, raw = "TARGET_1", item, target
            break
    if reason is None:
        return {"status": "OPEN", "reason": "NO_EXIT_OBSERVED",
                "market_date": target_session.market_date.isoformat(),
                "evaluated_through_sequence": facts.bars[-1].sequence, "exit": None}
    with localcontext(Context(prec=34)):
        capacity = int(Decimal(bar.volume) * policy.max_volume_participation_pct)
    if capacity < entry["quantity"]:
        return {"status": "UNKNOWN", "reason": "INSUFFICIENT_EXIT_CAPACITY",
                "market_date": target_session.market_date.isoformat(),
                "evaluated_through_sequence": bar.sequence, "exit": None}
    bps = policy.stop_slippage_bps if reason.startswith("STOP") else policy.target_slippage_bps
    with localcontext(Context(prec=34)):
        price = raw * (Decimal(1) - bps / Decimal(10000))
        if price <= 0:
            raise ValueError("exit slippage produces nonpositive price")
        notional = price * entry["quantity"]
        cost = notional * policy.cost_bps_per_side / Decimal(10000) + policy.fixed_cost_per_side
    return {"status": "CLOSED", "reason": reason,
            "market_date": target_session.market_date.isoformat(),
            "evaluated_through_sequence": bar.sequence,
            "exit": {"quantity": entry["quantity"], "bar_sequence": bar.sequence,
                     "market_date": target_session.market_date.isoformat(),
                     "interval_start": bar.interval_start.isoformat(),
                     "interval_end": bar.interval_end.isoformat(),
                     "known_at": bar.available_at.isoformat(), "raw_price": str(raw),
                     "fill_price": str(price), "notional": str(notional),
                     "exit_cost": str(cost), "currency": policy.currency}}


def _basis(position, facts, fact_event, policy, package_ids):
    evaluation = evaluate_exit(position, facts, policy)
    return {"schema_version": "shadow-exit-event-v2", "label": LABEL,
            "event_type": "EXIT_EVALUATED", "scoring": "NOT SCORED",
            "portfolio_status": "SHARED CAPITAL NOT ALLOCATED",
            "performance_status": "NO P&L OR NAV",
            "position_event_id": position["event_id"],
            "position_event_sha256": hashlib.sha256(_canonical(position)).hexdigest(),
            "fact_record_id": facts.record_id, "fact_event_id": fact_event["event_id"],
            "fact_event_sha256": hashlib.sha256(_canonical(fact_event)).hexdigest(),
            "exit_package_ids": package_ids,
            "policy": policy.model_dump(mode="json"), "evaluation": evaluation}


def _require_monotonic_fact_extension(original: ForwardFactBundle,
                                      evaluation: ForwardFactBundle) -> None:
    """Reject a later snapshot that rewrites or drops already admitted facts."""
    original_values = original.model_dump(mode="json", exclude={"bars", "trading_status"})
    evaluation_values = evaluation.model_dump(mode="json", exclude={"bars", "trading_status"})
    if evaluation_values != original_values:
        raise ValueError("exit evaluation facts rewrite original admitted facts")
    original_status = original.trading_status.model_dump(mode="json")
    evaluation_status = evaluation.trading_status.model_dump(mode="json")
    original_end = original_status.pop("coverage_end")
    evaluation_end = evaluation_status.pop("coverage_end")
    original_status.pop("evidence_package_id")
    evaluation_status.pop("evidence_package_id")
    if evaluation_status != original_status or evaluation_end < original_end:
        raise ValueError("exit evaluation facts rewrite original trading status")
    if len(evaluation.bars) < len(original.bars):
        raise ValueError("exit evaluation facts truncate original admitted bars")
    original_bars = tuple(item.model_dump(mode="json") for item in original.bars)
    evaluation_prefix = tuple(
        item.model_dump(mode="json") for item in evaluation.bars[:len(original.bars)]
    )
    if evaluation_prefix != original_bars:
        raise ValueError("exit evaluation facts rewrite original admitted bars")


def exit_event_path(directory: Path, position_event_id: str, fact_event_id: str) -> Path:
    """Address one immutable evaluation of a position against one fact snapshot."""
    if (type(position_event_id) is not str or len(position_event_id) != 64
            or any(c not in "0123456789abcdef" for c in position_event_id)):
        raise ValueError("invalid position event id")
    if (type(fact_event_id) is not str or len(fact_event_id) != 64
            or any(c not in "0123456789abcdef" for c in fact_event_id)):
        raise ValueError("invalid fact event id")
    return Path(directory) / "exit-events" / f"{position_event_id}.{fact_event_id}.json"


def append_exit_event(directory: Path, watchlist: ShadowWatchlist,
                      watchlist_packages: tuple[HistoricalEvidencePackage, ...],
                      facts: ForwardFactBundle, fact_packages: tuple[HistoricalEvidencePackage, ...],
                      fill_policy: ShadowFillPolicy, fill_packages: tuple[HistoricalEvidencePackage, ...],
                      exit_policy: ShadowExitPolicy,
                      exit_packages: tuple[HistoricalEvidencePackage, ...], *,
                      evaluation_facts: ForwardFactBundle | None = None,
                      evaluation_fact_packages: tuple[HistoricalEvidencePackage, ...] | None = None) -> Path:
    exit_policy = ShadowExitPolicy.model_validate(exit_policy.model_dump(mode="python"))
    position = audit_position_open_event(directory, watchlist, watchlist_packages, facts,
                                         fact_packages, fill_policy, fill_packages)
    evaluation_facts = facts if evaluation_facts is None else evaluation_facts
    evaluation_fact_packages = fact_packages if evaluation_fact_packages is None else evaluation_fact_packages
    _require_monotonic_fact_extension(facts, evaluation_facts)
    fact_event = audit_forward_fact_event(directory, watchlist, watchlist_packages,
                                          evaluation_facts, evaluation_fact_packages)
    packages = _packages(exit_policy, exit_packages, watchlist.information_cutoff)
    basis = _basis(position, evaluation_facts, fact_event, exit_policy, sorted(packages))
    recorded_at = _utc(_now())
    if recorded_at < _utc(datetime.fromisoformat(position["recorded_at"])) or recorded_at < evaluation_facts.bars[-1].available_at:
        raise ValueError("exit publication precedes authenticated inputs")
    payload = basis | {"event_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                       "recorded_at": recorded_at.isoformat()}
    path = _publish_once(
        exit_event_path(directory, position["event_id"], fact_event["event_id"]), payload,
    )
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during exit publication")
    return path


def audit_exit_event(directory: Path, watchlist: ShadowWatchlist,
                     watchlist_packages: tuple[HistoricalEvidencePackage, ...],
                     facts: ForwardFactBundle, fact_packages: tuple[HistoricalEvidencePackage, ...],
                     fill_policy: ShadowFillPolicy, fill_packages: tuple[HistoricalEvidencePackage, ...],
                     exit_policy: ShadowExitPolicy,
                     exit_packages: tuple[HistoricalEvidencePackage, ...], *,
                     evaluation_facts: ForwardFactBundle | None = None,
                     evaluation_fact_packages: tuple[HistoricalEvidencePackage, ...] | None = None) -> dict:
    exit_policy = ShadowExitPolicy.model_validate(exit_policy.model_dump(mode="python"))
    position = audit_position_open_event(directory, watchlist, watchlist_packages, facts,
                                         fact_packages, fill_policy, fill_packages)
    evaluation_facts = facts if evaluation_facts is None else evaluation_facts
    evaluation_fact_packages = fact_packages if evaluation_fact_packages is None else evaluation_fact_packages
    _require_monotonic_fact_extension(facts, evaluation_facts)
    fact_event = audit_forward_fact_event(directory, watchlist, watchlist_packages,
                                          evaluation_facts, evaluation_fact_packages)
    packages = _packages(exit_policy, exit_packages, watchlist.information_cutoff)
    basis = _basis(position, evaluation_facts, fact_event, exit_policy, sorted(packages))
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate exit-event field")
            result[key] = value
        return result
    path = exit_event_path(directory, position["event_id"], fact_event["event_id"])
    event = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected exit-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    expected = basis | {"event_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                        "recorded_at": recorded_at.isoformat()}
    if not max(_utc(datetime.fromisoformat(position["recorded_at"])), evaluation_facts.bars[-1].available_at) <= recorded_at <= _now() or event != expected:
        raise ValueError("exit event does not bind authenticated inputs")
    return event
