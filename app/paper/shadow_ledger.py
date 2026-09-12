"""Append-only candidate ledger; frozen candidates are never inferred fills."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.paper.shadow_collection import _publish_once, audit_completed_watchlist
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage


LABEL = "EXPERIMENTAL / PAPER ONLY"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _event_basis(watchlist: ShadowWatchlist, completion: dict) -> dict:
    candidates = [candidate.model_dump(mode="json") for candidate in watchlist.candidates]
    return {
        "schema_version": "shadow-candidate-ledger-v1",
        "label": LABEL,
        "event_type": "WATCHLIST_FROZEN",
        "scoring": "NOT SCORED",
        "execution_status": "NO EXECUTION INFERENCE",
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "market_date": watchlist.session.market_date.isoformat(),
        "information_cutoff": watchlist.information_cutoff.isoformat(),
        "decision_cutoff": watchlist.session.decision_cutoff.isoformat(),
        "completion_sha256": hashlib.sha256(_canonical(completion)).hexdigest(),
        "watchlist_sha256": completion["watchlist_sha256"],
        "document_sha256": completion["document_sha256"],
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def append_candidate_event(
    directory: Path,
    watchlist: ShadowWatchlist,
    evidence_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Append one immutable event for an audited completed watchlist.

    An empty candidate list is an explicit event. This boundary records no
    trigger, fill, position, return or score.
    """
    if type(watchlist) is not ShadowWatchlist:
        raise ValueError("exact ShadowWatchlist required")
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    directory = Path(directory)
    completion = audit_completed_watchlist(directory, watchlist, evidence_packages)
    basis = _event_basis(watchlist, completion)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()
    recorded_at = _now()
    completed_at = datetime.fromisoformat(completion["completed_at"])
    if recorded_at.tzinfo is not timezone.utc or recorded_at < completed_at:
        raise ValueError("invalid ledger clock ordering")
    payload = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    path = _publish_once(directory / "candidate-ledger" / f"{event_id}.json", payload)
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during ledger publication")
    return path


def audit_candidate_event(
    directory: Path,
    watchlist: ShadowWatchlist,
    evidence_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Read and bind an immutable candidate event to its completed watchlist."""
    if type(watchlist) is not ShadowWatchlist:
        raise ValueError("exact ShadowWatchlist required")
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    directory = Path(directory)
    completion = audit_completed_watchlist(directory, watchlist, evidence_packages)
    basis = _event_basis(watchlist, completion)
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate ledger field")
            result[key] = value
        return result

    path = directory / "candidate-ledger" / f"{event_id}.json"
    event = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected ledger fields")
    recorded_at = datetime.fromisoformat(event["recorded_at"])
    completed_at = datetime.fromisoformat(completion["completed_at"])
    if (
        recorded_at.tzinfo is not timezone.utc
        or not completed_at <= recorded_at <= _now()
        or event != basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    ):
        raise ValueError("candidate event does not bind completed watchlist")
    return event
