"""Atomic shared-capital reservations for authenticated paper positions."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_fills import ShadowFillPolicy, _canonical, _utc
from app.paper.shadow_exits import (
    ShadowExitPolicy, audit_continuation_exit_event, audit_exit_event, exit_event_path,
)
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_portfolio import ShadowPortfolioPolicy, audit_portfolio_policy
from app.paper.shadow_positions import audit_position_open_event
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _allocation_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".capital-reservations.lock").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate capital-reservation field")
        result[key] = value
    return result


def _read_reservations(directory: Path) -> list[dict]:
    unordered = []
    for path in sorted((directory / "capital-reservations").glob("*.json")):
        event = json.loads(path.read_bytes(), object_pairs_hook=_unique)
        expected = {
            "schema_version", "label", "event_type", "scoring", "portfolio_status",
            "candidate_position_key", "position_event_id", "position_event_sha256",
            "policy_id", "market", "currency", "capital_reserved", "risk_reserved",
            "prior_reservation_ids", "totals_after", "reservation_id", "recorded_at",
        }
        if type(event) is not dict or set(event) != expected:
            raise ValueError("unexpected capital-reservation fields")
        if event["schema_version"] != "shadow-capital-reservation-v1" or event["label"] != LABEL:
            raise ValueError("invalid capital-reservation identity")
        if (event["event_type"] != "CAPITAL_RESERVED" or event["scoring"] != "NOT SCORED"
                or event["portfolio_status"] != "SHARED CAPITAL RESERVED / NO NAV OR P&L"):
            raise ValueError("invalid capital-reservation semantics")
        for field in ("candidate_position_key", "position_event_id", "position_event_sha256", "policy_id"):
            if type(event[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", event[field]) is None:
                raise ValueError("invalid capital-reservation reference")
        if path.name != f'{event["candidate_position_key"]}.json':
            raise ValueError("capital-reservation filename mismatch")
        basis = {key: value for key, value in event.items() if key not in {"reservation_id", "recorded_at"}}
        if event["reservation_id"] != hashlib.sha256(_canonical(basis)).hexdigest():
            raise ValueError("capital-reservation hash mismatch")
        if _utc(datetime.fromisoformat(event["recorded_at"])) > _utc(_now()):
            raise ValueError("future capital-reservation clock")
        _amount(event, "capital_reserved")
        _amount(event, "risk_reserved")
        unordered.append(event)
    result = []
    remaining = unordered[:]
    while remaining:
        prior = [item["reservation_id"] for item in result]
        matches = [item for item in remaining if item["prior_reservation_ids"] == prior]
        if len(matches) != 1:
            raise ValueError("capital-reservation chain is incomplete or ambiguous")
        event = matches[0]
        if result and (
            _utc(datetime.fromisoformat(event["recorded_at"]))
            < _utc(datetime.fromisoformat(result[-1]["recorded_at"]))
        ):
            raise ValueError("capital-reservation chain clock ordering")
        if any(item["position_event_id"] == event["position_event_id"] for item in result):
            raise ValueError("duplicate reserved position")
        totals = event["totals_after"]
        if type(totals) is not dict or set(totals) != {"capital_reserved", "risk_reserved"}:
            raise ValueError("unexpected capital-reservation totals")
        for field in totals:
            old = sum((_amount(item, field) for item in result), Fraction())
            with localcontext(Context(prec=34)):
                expected_total = Decimal(old.numerator) / Decimal(old.denominator) + Decimal(event[field])
            if _amount(totals, field) != Fraction(expected_total):
                raise ValueError("capital-reservation cumulative total mismatch")
        result.append(event)
        remaining.remove(event)
    return result



def _settlement_exit_view(exit_event: dict) -> tuple[str, dict]:
    """Return the durable exit-link id and CLOSED result for supported exit schemas."""
    if type(exit_event) is not dict:
        raise ValueError("capital settlement exit event must be an exact object")

    schema = exit_event.get("schema_version")
    if schema == "shadow-exit-event-v2":
        if exit_event.get("event_type") != "EXIT_EVALUATED":
            raise ValueError("invalid single-session exit-event semantics")
        link_id = exit_event.get("fact_event_id")
        evaluation = exit_event.get("evaluation")

    elif schema == "shadow-continuation-exit-event-v3":
        if exit_event.get("event_type") != "CONTINUATION_EXIT_EVALUATED":
            raise ValueError("invalid continuation exit-event semantics")

        references = exit_event.get("continuation_events")
        evaluations = exit_event.get("evaluations")
        entry_evaluation = exit_event.get("entry_session_evaluation")
        result = exit_event.get("result")

        if (
            type(references) is not list
            or not references
            or type(evaluations) is not list
            or not evaluations
            or len(evaluations) != len(references)
            or type(entry_evaluation) is not dict
            or entry_evaluation.get("status") != "OPEN"
            or any(
                type(item) is not dict or item.get("status") != "OPEN"
                for item in evaluations[:-1]
            )
            or evaluations[-1] != result
        ):
            raise ValueError("invalid continuation exit-event chain")

        last_reference = references[-1]
        if type(last_reference) is not dict:
            raise ValueError("invalid continuation exit-event reference")
        link_id = last_reference.get("event_id")
        evaluation = result

    else:
        raise ValueError("unsupported capital-settlement exit-event schema")

    if (
        type(link_id) is not str
        or re.fullmatch(r"[0-9a-f]{64}", link_id) is None
    ):
        raise ValueError("invalid capital-settlement exit-event link")

    if (
        type(evaluation) is not dict
        or evaluation.get("status") != "CLOSED"
        or type(evaluation.get("exit")) is not dict
    ):
        raise ValueError("capital settlement requires authenticated CLOSED exit")

    return link_id, evaluation

def _read_settlements(directory: Path, reservations: list[dict]) -> list[dict]:
    """Validate immutable releases against the locally authenticated reservation chain."""
    by_id = {item["reservation_id"]: item for item in reservations}
    result = []
    seen = set()
    for path in sorted((directory / "capital-settlements").glob("*.json")):
        event = json.loads(path.read_bytes(), object_pairs_hook=_unique)
        expected_fields = {
            "schema_version", "label", "event_type", "scoring", "portfolio_status",
            "performance_status", "reservation_id", "exit_event_id", "exit_event_sha256",
            "candidate_position_key", "policy_id", "market", "currency",
            "capital_released", "risk_released", "exit_notional", "exit_cost",
            "net_exit_proceeds", "settlement_id", "recorded_at",
        }
        if type(event) is not dict or set(event) != expected_fields:
            raise ValueError("unexpected capital-settlement fields")
        if (event["schema_version"] != "shadow-capital-settlement-v1" or event["label"] != LABEL
                or event["event_type"] != "CAPITAL_SETTLED" or event["scoring"] != "NOT SCORED"
                or event["portfolio_status"] != "SHARED CAPITAL RELEASED"
                or event["performance_status"] != "NATIVE CASH FLOW / NO NAV OR PERFORMANCE"):
            raise ValueError("invalid capital-settlement semantics")
        for field in ("reservation_id", "exit_event_id", "exit_event_sha256",
                      "candidate_position_key", "policy_id"):
            if type(event[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", event[field]) is None:
                raise ValueError("invalid capital-settlement reference")
        reservation = by_id.get(event["reservation_id"])
        if reservation is None or event["reservation_id"] in seen:
            raise ValueError("capital settlement references absent or duplicate reservation")
        if path.name != f'{event["candidate_position_key"]}.json':
            raise ValueError("capital-settlement filename mismatch")
        candidates = []
        for exit_path in sorted((directory / "exit-events").glob(
                f'{reservation["position_event_id"]}.*.json')):
            candidate = json.loads(exit_path.read_bytes(), object_pairs_hook=_unique)
            if candidate.get("event_id") == event["exit_event_id"]:
                candidates.append((exit_path, candidate))
        if len(candidates) != 1:
            raise ValueError("capital settlement exit event is absent or ambiguous")
        exit_path, exit_event = candidates[0]
        exit_link_id, _ = _settlement_exit_view(exit_event)
        if exit_path != exit_event_path(
                directory, reservation["position_event_id"], exit_link_id):
            raise ValueError("capital settlement exit-event filename mismatch")
        if (exit_event.get("event_id") != event["exit_event_id"]
                or hashlib.sha256(_canonical(exit_event)).hexdigest() != event["exit_event_sha256"]
                or exit_event.get("position_event_id") != reservation["position_event_id"]):
            raise ValueError("capital settlement does not bind closed exit event")
        for field in ("capital_released", "risk_released", "exit_notional", "exit_cost",
                      "net_exit_proceeds"):
            _amount(event, field)
        if (event["candidate_position_key"] != reservation["candidate_position_key"]
                or event["policy_id"] != reservation["policy_id"]
                or event["market"] != reservation["market"]
                or event["currency"] != reservation["currency"]
                or event["capital_released"] != reservation["capital_reserved"]
                or event["risk_released"] != reservation["risk_reserved"]
                or _amount(event, "net_exit_proceeds")
                != _amount(event, "exit_notional") - _amount(event, "exit_cost")):
            raise ValueError("capital settlement does not bind reservation cash flow")
        basis = {key: value for key, value in event.items() if key not in {"settlement_id", "recorded_at"}}
        if event["settlement_id"] != hashlib.sha256(_canonical(basis)).hexdigest():
            raise ValueError("capital-settlement hash mismatch")
        recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
        if not max(_utc(datetime.fromisoformat(reservation["recorded_at"])),
                   _utc(datetime.fromisoformat(exit_event["recorded_at"]))) <= recorded_at <= _now():
            raise ValueError("invalid capital-settlement clock")
        seen.add(event["reservation_id"])
        result.append(event)
    return result


def _amount(event: dict, field: str) -> Fraction:
    value = event[field]
    if type(value) is not str:
        raise ValueError("capital-reservation amount must be an exact decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid capital-reservation amount")
    return Fraction(parsed)


def _basis(position: dict, policy_receipt: dict, portfolio: ShadowPortfolioPolicy,
           fill_policy: ShadowFillPolicy, existing: list[dict], settlements: list[dict] = ()) -> dict:
    fill = position["entry"]
    if fill["currency"] != portfolio.base_currency:
        raise ValueError("admitted point-in-time FX required for foreign-currency allocation")
    if fill_policy.currency != fill["currency"]:
        raise ValueError("fill currency mismatch")
    if any(item["policy_id"] != policy_receipt["policy_id"]
           or item["currency"] != portfolio.base_currency
           or item["market"] not in {"EGX", "US"} for item in existing):
        raise ValueError("existing exposure does not bind shared portfolio policy")
    quantity, fill_price = Decimal(fill["quantity"]), Decimal(fill["fill_price"])
    stop, entry_cost = Decimal(position["initial_stop"]), Decimal(fill["entry_cost"])
    with localcontext(Context(prec=34)):
        stop_exit_cost = stop * quantity * fill_policy.cost_bps_per_side / Decimal(10000)
        stop_exit_cost += fill_policy.fixed_cost_per_side
        capital = Decimal(fill["notional"]) + entry_cost
        risk = (fill_price - stop) * quantity + entry_cost + stop_exit_cost

    # A stop estimate is not a bound on gap/slippage losses. Any settled breach
    # halts new exposure for this ledger; profits cannot offset or reset it.
    if any(_amount(item, "capital_released") - _amount(item, "net_exit_proceeds")
           > _amount(item, "risk_released") for item in settlements):
        raise ValueError("settled loss exceeds reserved risk; new paper exposure halted")

    settled_ids = {item["reservation_id"] for item in settlements}
    active = [item for item in existing if item["reservation_id"] not in settled_ids]
    old_capital = sum((_amount(item, "capital_reserved") for item in active), Fraction())
    old_risk = sum((_amount(item, "risk_reserved") for item in active), Fraction())
    historical_capital = sum((_amount(item, "capital_reserved") for item in existing), Fraction())
    historical_risk = sum((_amount(item, "risk_reserved") for item in existing), Fraction())
    sleeve_capital = sum((_amount(item, "capital_reserved") for item in active
                          if item["market"] == position["market"]), Fraction())
    # Losses reduce reusable capacity; unvalidated gains never enlarge it.
    losses = sum((max(Fraction(), _amount(item, "capital_released")
                      - _amount(item, "net_exit_proceeds")) for item in settlements), Fraction())
    sleeve_losses = sum((max(Fraction(), _amount(item, "capital_released")
                             - _amount(item, "net_exit_proceeds")) for item in settlements
                         if item["market"] == position["market"]), Fraction())
    nav, capital_f, risk_f = Fraction(portfolio.initial_capital), Fraction(capital), Fraction(risk)
    sleeve = portfolio.egx_capital_fraction if position["market"] == "EGX" else portfolio.us_capital_fraction
    if capital_f > nav * Fraction(portfolio.max_position_fraction):
        raise ValueError("position capital cap exceeded")
    if risk_f > nav * Fraction(portfolio.max_position_risk_fraction):
        raise ValueError("position risk cap exceeded")
    if sleeve_capital + capital_f > nav * Fraction(sleeve) - sleeve_losses:
        raise ValueError("market sleeve cap exceeded")
    if old_capital + capital_f > nav * (1 - Fraction(portfolio.minimum_cash_fraction)) - losses:
        raise ValueError("minimum cash floor exceeded")
    if old_risk + risk_f > nav * Fraction(portfolio.max_portfolio_risk_fraction):
        raise ValueError("aggregate portfolio risk cap exceeded")
    with localcontext(Context(prec=34)):
        total_capital = (Decimal(historical_capital.numerator)
                         / Decimal(historical_capital.denominator) + capital)
        total_risk = (Decimal(historical_risk.numerator)
                      / Decimal(historical_risk.denominator) + risk)
    return {
        "schema_version": "shadow-capital-reservation-v1", "label": LABEL,
        "event_type": "CAPITAL_RESERVED", "scoring": "NOT SCORED",
        "portfolio_status": "SHARED CAPITAL RESERVED / NO NAV OR P&L",
        "candidate_position_key": position["candidate_position_key"],
        "position_event_id": position["event_id"],
        "position_event_sha256": hashlib.sha256(_canonical(position)).hexdigest(),
        "policy_id": policy_receipt["policy_id"], "market": position["market"],
        "currency": fill["currency"], "capital_reserved": str(capital),
        "risk_reserved": str(risk),
        "prior_reservation_ids": [item["reservation_id"] for item in existing],
        "totals_after": {"capital_reserved": str(total_capital), "risk_reserved": str(total_risk)},
    }


def append_capital_reservation(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
) -> Path:
    """Reserve one shared normalized budget after the full fill/position chain."""
    directory = Path(directory)
    position = audit_position_open_event(directory, watchlist, watchlist_packages, facts,
                                         fact_packages, fill_policy, fill_packages)
    policy_receipt = audit_portfolio_policy(directory, portfolio)
    _require_timely_policy(policy_receipt, portfolio, watchlist)
    with _allocation_lock(directory):
        existing = _read_reservations(directory)
        settlements = _read_settlements(directory, existing)
        basis = _basis(position, policy_receipt, portfolio, fill_policy, existing, settlements)
        reservation_id = hashlib.sha256(_canonical(basis)).hexdigest()
        recorded_at = _utc(_now())
        if recorded_at < _utc(datetime.fromisoformat(position["recorded_at"])):
            raise ValueError("capital reservation precedes position event")
        if existing and recorded_at < _utc(datetime.fromisoformat(existing[-1]["recorded_at"])):
            raise ValueError("capital reservation precedes prior reservation")
        if settlements and recorded_at < max(
            _utc(datetime.fromisoformat(item["recorded_at"])) for item in settlements
        ):
            raise ValueError("capital reservation precedes prior settlement")
        payload = basis | {"reservation_id": reservation_id, "recorded_at": recorded_at.isoformat()}
        path = _publish_once(directory / "capital-reservations" / f"{basis['candidate_position_key']}.json", payload)
        if _now() < recorded_at:
            path.unlink()
            raise ValueError("clock rollback during capital reservation")
        return path


def audit_capital_reservation(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
) -> dict:
    position = audit_position_open_event(directory, watchlist, watchlist_packages, facts,
                                         fact_packages, fill_policy, fill_packages)
    policy_receipt = audit_portfolio_policy(directory, portfolio)
    _require_timely_policy(policy_receipt, portfolio, watchlist)
    events = _read_reservations(Path(directory))
    settlements = _read_settlements(Path(directory), events)
    target = next((item for item in events if item["candidate_position_key"] == position["candidate_position_key"]), None)
    if target is None:
        raise FileNotFoundError("capital reservation absent")
    recorded_at = _utc(datetime.fromisoformat(target["recorded_at"]))
    prior_events = events[:events.index(target)]
    prior_ids = {item["reservation_id"] for item in prior_events}
    prior_settlements = [item for item in settlements
                         if item["reservation_id"] in prior_ids
                         and _utc(datetime.fromisoformat(item["recorded_at"])) <= recorded_at]
    expected_basis = _basis(position, policy_receipt, portfolio, fill_policy,
                            prior_events, prior_settlements)
    expected = expected_basis | {"reservation_id": hashlib.sha256(_canonical(expected_basis)).hexdigest(),
                                 "recorded_at": recorded_at.isoformat()}
    if target != expected:
        raise ValueError("capital reservation does not bind shared exposure")
    if not _utc(datetime.fromisoformat(position["recorded_at"])) <= recorded_at <= _now():
        raise ValueError("invalid capital-reservation clock ordering")
    return target



def _append_capital_settlement_event(
    directory: Path, reservation: dict, exit_event: dict,
) -> Path:
    _, evaluation = _settlement_exit_view(exit_event)
    exit_fill = evaluation["exit"]
    if exit_fill["currency"] != reservation["currency"]:
        raise ValueError("capital settlement currency mismatch")

    with _allocation_lock(directory):
        reservations = _read_reservations(directory)
        _read_settlements(directory, reservations)

        with localcontext(Context(prec=34)):
            net_proceeds = Decimal(exit_fill["notional"]) - Decimal(exit_fill["exit_cost"])
        if net_proceeds < 0:
            raise ValueError("exit costs exceed proceeds")

        basis = {
            "schema_version": "shadow-capital-settlement-v1", "label": LABEL,
            "event_type": "CAPITAL_SETTLED", "scoring": "NOT SCORED",
            "portfolio_status": "SHARED CAPITAL RELEASED",
            "performance_status": "NATIVE CASH FLOW / NO NAV OR PERFORMANCE",
            "reservation_id": reservation["reservation_id"],
            "exit_event_id": exit_event["event_id"],
            "exit_event_sha256": hashlib.sha256(_canonical(exit_event)).hexdigest(),
            "candidate_position_key": reservation["candidate_position_key"],
            "policy_id": reservation["policy_id"], "market": reservation["market"],
            "currency": reservation["currency"],
            "capital_released": reservation["capital_reserved"],
            "risk_released": reservation["risk_reserved"],
            "exit_notional": exit_fill["notional"],
            "exit_cost": exit_fill["exit_cost"],
            "net_exit_proceeds": str(net_proceeds),
        }

        recorded_at = _utc(_now())
        if recorded_at < max(
            _utc(datetime.fromisoformat(reservation["recorded_at"])),
            _utc(datetime.fromisoformat(exit_event["recorded_at"])),
        ):
            raise ValueError("capital settlement precedes authenticated inputs")

        payload = basis | {
            "settlement_id": hashlib.sha256(_canonical(basis)).hexdigest(),
            "recorded_at": recorded_at.isoformat(),
        }
        path = _publish_once(
            directory / "capital-settlements"
            / f'{reservation["candidate_position_key"]}.json',
            payload,
        )
        if _now() < recorded_at:
            path.unlink()
            raise ValueError("clock rollback during capital settlement")
        return path


def append_capital_settlement(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
    exit_policy: ShadowExitPolicy, exit_packages: tuple[HistoricalEvidencePackage, ...],
    *, evaluation_facts: ForwardFactBundle | None = None,
    evaluation_fact_packages: tuple[HistoricalEvidencePackage, ...] | None = None,
) -> Path:
    """Release one reservation after an authenticated single-session CLOSED exit."""
    directory = Path(directory)
    reservation = audit_capital_reservation(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio,
    )
    exit_event = audit_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, exit_policy, exit_packages,
        evaluation_facts=evaluation_facts,
        evaluation_fact_packages=evaluation_fact_packages,
    )
    return _append_capital_settlement_event(directory, reservation, exit_event)


def append_continuation_capital_settlement(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
    continuations: tuple, continuation_packages: tuple,
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Release one reservation only after a fully re-audited continuation CLOSED exit."""
    directory = Path(directory)
    reservation = audit_capital_reservation(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio,
    )
    exit_event = audit_continuation_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, continuations, continuation_packages,
        exit_policy, exit_packages,
    )
    return _append_capital_settlement_event(directory, reservation, exit_event)


def _audit_capital_settlement_event(
    directory: Path, reservation: dict, exit_event: dict,
) -> dict:
    _settlement_exit_view(exit_event)
    reservations = _read_reservations(directory)
    settlements = _read_settlements(directory, reservations)
    target = next(
        (
            item for item in settlements
            if item["reservation_id"] == reservation["reservation_id"]
        ),
        None,
    )
    if target is None:
        raise FileNotFoundError("capital settlement absent")
    if (
        target["exit_event_id"] != exit_event["event_id"]
        or target["exit_event_sha256"]
        != hashlib.sha256(_canonical(exit_event)).hexdigest()
        or target["candidate_position_key"] != reservation["candidate_position_key"]
    ):
        raise ValueError("capital settlement does not bind authenticated inputs")
    return target


def audit_capital_settlement(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
    exit_policy: ShadowExitPolicy, exit_packages: tuple[HistoricalEvidencePackage, ...],
    *, evaluation_facts: ForwardFactBundle | None = None,
    evaluation_fact_packages: tuple[HistoricalEvidencePackage, ...] | None = None,
) -> dict:
    """Re-audit a single-session settlement and its conservative exit."""
    directory = Path(directory)
    reservation = audit_capital_reservation(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio,
    )
    exit_event = audit_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, exit_policy, exit_packages,
        evaluation_facts=evaluation_facts,
        evaluation_fact_packages=evaluation_fact_packages,
    )
    return _audit_capital_settlement_event(directory, reservation, exit_event)


def audit_continuation_capital_settlement(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...], portfolio: ShadowPortfolioPolicy,
    continuations: tuple, continuation_packages: tuple,
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Re-audit a settlement against the complete authenticated continuation chain."""
    directory = Path(directory)
    reservation = audit_capital_reservation(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio,
    )
    exit_event = audit_continuation_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, continuations, continuation_packages,
        exit_policy, exit_packages,
    )
    return _audit_capital_settlement_event(directory, reservation, exit_event)

def _require_timely_policy(policy_receipt: dict, portfolio: ShadowPortfolioPolicy,
                           watchlist: ShadowWatchlist) -> None:
    frozen_at = _utc(datetime.fromisoformat(policy_receipt["frozen_at"]))
    if (frozen_at > watchlist.information_cutoff
            or portfolio.effective_at > watchlist.information_cutoff):
        raise ValueError("portfolio policy was not frozen and effective by information cutoff")
