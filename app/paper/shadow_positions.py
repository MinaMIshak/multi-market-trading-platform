"""Append-only position provenance from authenticated paper fills; no NAV allocation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_fills import ShadowFillPolicy, _canonical, _utc, audit_fill_event
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _basis(watchlist: ShadowWatchlist, fill_event: dict) -> dict:
    fill = fill_event["fill"]
    if fill is None:
        raise ValueError("position requires an authenticated simulated fill")
    candidate = next(item for item in watchlist.candidates
                     if item.candidate_id == fill["candidate_id"])
    return {
        "schema_version": "shadow-position-open-v1", "label": LABEL,
        "event_type": "POSITION_OPENED", "scoring": "NOT SCORED",
        "position_status": "SIMULATED OPEN AT ENTRY",
        "portfolio_status": "SHARED CAPITAL NOT ALLOCATED",
        "valuation_status": "NO MARK, EXIT, P&L OR NAV",
        "fill_event_id": fill_event["event_id"],
        "fill_event_sha256": hashlib.sha256(_canonical(fill_event)).hexdigest(),
        "market": watchlist.session.market,
        "entry": fill,
        "initial_stop": str(candidate.stop),
        "initial_targets": [str(value) for value in candidate.targets],
        "holding_window": "NOT_YET_VALIDATED",
    }


def append_position_open_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Preserve one position-open event per exact fill, never infer current holdings."""
    fill = audit_fill_event(directory, watchlist, watchlist_packages, facts,
                            fact_packages, policy, fill_packages)
    basis = _basis(watchlist, fill)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()
    recorded_at = _utc(_now())
    if recorded_at < _utc(datetime.fromisoformat(fill["recorded_at"])):
        raise ValueError("position publication precedes fill event")
    payload = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    # Key by upstream fill, so a fill cannot open multiple positions in this ledger.
    path = _publish_once(Path(directory) / "position-open-events" / f'{fill["event_id"]}.json', payload)
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during position publication")
    return path


def audit_position_open_event(
    directory: Path, watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...], facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...], policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Recompute the entire fill chain and position binding without writes."""
    fill = audit_fill_event(directory, watchlist, watchlist_packages, facts,
                            fact_packages, policy, fill_packages)
    basis = _basis(watchlist, fill)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate position-event field")
            result[key] = value
        return result

    path = Path(directory) / "position-open-events" / f'{fill["event_id"]}.json'
    event = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected position-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    fill_at = _utc(datetime.fromisoformat(fill["recorded_at"]))
    expected = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    if not fill_at <= recorded_at <= _now() or event != expected:
        raise ValueError("position event does not bind authenticated fill")
    return event
