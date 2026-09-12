"""Operational forward collection receipts; all outputs remain paper-only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.paper import shadow_freeze
from app.paper.shadow_records import ShadowSession, ShadowWatchlist, freeze_watchlist
from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)


LABEL = "EXPERIMENTAL / PAPER ONLY"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _publish_once(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        published = True
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if published:
            path.unlink()
        raise
    finally:
        os.unlink(temporary)
    return path


def _admit_packages(
    watchlist: ShadowWatchlist,
    packages: tuple[HistoricalEvidencePackage, ...],
    *,
    built_at: datetime,
) -> None:
    if type(packages) is not tuple or not packages:
        raise ValueError("nonempty exact evidence package tuple required")
    admitted: dict[str, HistoricalEvidencePackage] = {}
    for package in packages:
        if type(package) is not HistoricalEvidencePackage:
            raise ValueError("exact HistoricalEvidencePackage required")
        item = require_historical_evidence(
            package,
            decision_at=watchlist.information_cutoff,
            research_built_at=built_at,
        )
        if item.identity in admitted:
            raise ValueError("duplicate evidence package identity")
        admitted[item.identity] = item

    references = {item.evidence_id: item for item in watchlist.evidence}
    if set(admitted) != set(references):
        raise ValueError("evidence packages must match watchlist references exactly")
    for identity, package in admitted.items():
        reference = references[identity]
        availability = package.evidence.availability
        latest_available_at = (
            availability.exact_at if availability.kind == "EXACT" else availability.end
        )
        if reference.available_at != latest_available_at:
            raise ValueError("watchlist availability must bind package latest availability")
        if (
            reference.artifact_sha256 != package.raw_receipt.sha256
            or reference.source_locator != package.raw_receipt.source_locator
            or reference.source_authority not in {
                attachment.source_authority_context for attachment in package.attachments
            }
        ):
            raise ValueError("watchlist evidence does not bind admitted raw artifact")


def complete_watchlist(
    directory: Path,
    watchlist: ShadowWatchlist,
    evidence_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Freeze an authenticated watchlist and publish its completion receipt."""
    if type(watchlist) is not ShadowWatchlist:
        raise ValueError("exact ShadowWatchlist required")
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    started_at = _now()
    if not watchlist.generated_at <= started_at < watchlist.session.decision_cutoff:
        raise ValueError("future generation or missed decision cutoff")
    _admit_packages(watchlist, evidence_packages, built_at=started_at)
    directory = Path(directory)
    watchlist_path = freeze_watchlist(directory / "watchlists", watchlist)
    envelope = shadow_freeze.audit_document(watchlist_path)
    completed_at = _now()
    if not started_at <= completed_at < watchlist.session.decision_cutoff:
        raise ValueError("clock rollback or missed cutoff before completion")
    payload = {
        "schema_version": "shadow-completion-v1",
        "label": LABEL,
        "status": "FROZEN",
        "scoring": "NOT SCORED",
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "market_date": watchlist.session.market_date.isoformat(),
        "decision_cutoff": watchlist.session.decision_cutoff.isoformat(),
        "completed_at": completed_at.isoformat(),
        "watchlist_sha256": hashlib.sha256(watchlist_path.read_bytes()).hexdigest(),
        "document_sha256": envelope["document_sha256"],
        "evidence_package_ids": sorted(package.identity for package in evidence_packages),
    }
    path = directory / "receipts" / f"{watchlist.record_id}.json"
    result = _publish_once(path, payload)
    if not completed_at <= _now() < watchlist.session.decision_cutoff:
        result.unlink()
        raise ValueError("clock rollback or missed cutoff during completion")
    return result


def record_missed_session(
    directory: Path,
    *,
    record_id: str,
    session: ShadowSession,
    session_package: HistoricalEvidencePackage,
    reason: Literal["NO_TIMELY_WATCHLIST", "COLLECTION_FAILED"],
) -> Path:
    """After cutoff, durably record a session that must never be reconstructed."""
    if type(session) is not ShadowSession or type(session_package) is not HistoricalEvidencePackage:
        raise ValueError("exact session and evidence package required")
    if reason not in ("NO_TIMELY_WATCHLIST", "COLLECTION_FAILED"):
        raise ValueError("unsupported missed-session reason")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", record_id) is None:
        raise ValueError("invalid record_id")
    session = ShadowSession.model_validate(session.model_dump(mode="python"))
    missed_at = _now()
    if missed_at < session.decision_cutoff:
        raise ValueError("cannot record missed session before decision cutoff")
    require_historical_evidence(
        session_package,
        decision_at=session.decision_cutoff,
        research_built_at=missed_at,
    )
    if tuple(session.evidence_ids) != (session_package.identity,):
        raise ValueError("session evidence package mismatch")
    directory = Path(directory)
    if (directory / "watchlists" / f"{record_id}.json").exists():
        raise ValueError("watchlist exists; session cannot be marked missed")
    payload = {
        "schema_version": "shadow-completion-v1",
        "label": LABEL,
        "status": "MISSED",
        "scoring": "NOT SCORED",
        "record_id": record_id,
        "market": session.market,
        "market_date": session.market_date.isoformat(),
        "decision_cutoff": session.decision_cutoff.isoformat(),
        "completed_at": missed_at.isoformat(),
        "reason": reason,
        "session_evidence_package_id": session_package.identity,
    }
    result = _publish_once(directory / "receipts" / f"{record_id}.json", payload)
    if _now() < missed_at:
        result.unlink()
        raise ValueError("clock rollback during missed-session publication")
    return result
