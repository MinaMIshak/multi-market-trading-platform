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


def _amount(event: dict, field: str) -> Fraction:
    value = event[field]
    if type(value) is not str:
        raise ValueError("capital-reservation amount must be an exact decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid capital-reservation amount")
    return Fraction(parsed)


def _basis(position: dict, policy_receipt: dict, portfolio: ShadowPortfolioPolicy,
           fill_policy: ShadowFillPolicy, existing: list[dict]) -> dict:
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

    old_capital = sum((_amount(item, "capital_reserved") for item in existing), Fraction())
    old_risk = sum((_amount(item, "risk_reserved") for item in existing), Fraction())
    sleeve_capital = sum((_amount(item, "capital_reserved") for item in existing
                          if item["market"] == position["market"]), Fraction())
    nav, capital_f, risk_f = Fraction(portfolio.initial_capital), Fraction(capital), Fraction(risk)
    sleeve = portfolio.egx_capital_fraction if position["market"] == "EGX" else portfolio.us_capital_fraction
    if capital_f > nav * Fraction(portfolio.max_position_fraction):
        raise ValueError("position capital cap exceeded")
    if risk_f > nav * Fraction(portfolio.max_position_risk_fraction):
        raise ValueError("position risk cap exceeded")
    if sleeve_capital + capital_f > nav * Fraction(sleeve):
        raise ValueError("market sleeve cap exceeded")
    if old_capital + capital_f > nav * (1 - Fraction(portfolio.minimum_cash_fraction)):
        raise ValueError("minimum cash floor exceeded")
    if old_risk + risk_f > nav * Fraction(portfolio.max_portfolio_risk_fraction):
        raise ValueError("aggregate portfolio risk cap exceeded")
    with localcontext(Context(prec=34)):
        total_capital = Decimal(old_capital.numerator) / Decimal(old_capital.denominator) + capital
        total_risk = Decimal(old_risk.numerator) / Decimal(old_risk.denominator) + risk
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
        basis = _basis(position, policy_receipt, portfolio, fill_policy, existing)
        reservation_id = hashlib.sha256(_canonical(basis)).hexdigest()
        recorded_at = _utc(_now())
        if recorded_at < _utc(datetime.fromisoformat(position["recorded_at"])):
            raise ValueError("capital reservation precedes position event")
        if existing and recorded_at < _utc(datetime.fromisoformat(existing[-1]["recorded_at"])):
            raise ValueError("capital reservation precedes prior reservation")
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
    target = next((item for item in events if item["candidate_position_key"] == position["candidate_position_key"]), None)
    if target is None:
        raise FileNotFoundError("capital reservation absent")
    expected_basis = _basis(position, policy_receipt, portfolio, fill_policy, events[:events.index(target)])
    recorded_at = _utc(datetime.fromisoformat(target["recorded_at"]))
    expected = expected_basis | {"reservation_id": hashlib.sha256(_canonical(expected_basis)).hexdigest(),
                                 "recorded_at": recorded_at.isoformat()}
    if target != expected:
        raise ValueError("capital reservation does not bind shared exposure")
    if not _utc(datetime.fromisoformat(position["recorded_at"])) <= recorded_at <= _now():
        raise ValueError("invalid capital-reservation clock ordering")
    return target


def _require_timely_policy(policy_receipt: dict, portfolio: ShadowPortfolioPolicy,
                           watchlist: ShadowWatchlist) -> None:
    frozen_at = _utc(datetime.fromisoformat(policy_receipt["frozen_at"]))
    if (frozen_at > watchlist.information_cutoff
            or portfolio.effective_at > watchlist.information_cutoff):
        raise ValueError("portfolio policy was not frozen and effective by information cutoff")
