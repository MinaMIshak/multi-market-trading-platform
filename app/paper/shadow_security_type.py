"""Evidence-bound Shadow security-type classification; never an ETF comparison."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.paper.shadow_collection import (
    LABEL,
    _publish_once,
    audit_completed_watchlist,
)
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.historical_identity import HistoricalUSListingFact


STATUS = "AUDITED EXACT-DATED SECURITY TYPE / NOT AN ETF COMPARISON"
COMPARISON_STATUS = "UNAVAILABLE / NO COMPARABLE ETF EVIDENCE"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _exact_fact(
    fact: HistoricalUSListingFact,
) -> HistoricalUSListingFact:
    if type(fact) is not HistoricalUSListingFact:
        raise ValueError(
            "exact HistoricalUSListingFact required"
        )

    # Re-run the existing exact listing/evidence-package validators without
    # weakening their object-type requirements through dict reconstruction.
    return HistoricalUSListingFact(
        listing=fact.listing,
        evidence_package=fact.evidence_package,
    )


def _binding_basis(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[
        HistoricalEvidencePackage,
        ...
    ],
    candidate_id: str,
    fact: HistoricalUSListingFact,
) -> tuple[dict, dict]:
    if type(watchlist) is not ShadowWatchlist:
        raise ValueError("exact ShadowWatchlist required")

    if type(candidate_id) is not str or not candidate_id:
        raise ValueError("nonempty exact candidate_id required")

    fact = _exact_fact(fact)

    completion = audit_completed_watchlist(
        directory,
        watchlist,
        watchlist_packages,
    )

    if watchlist.session.market != "US":
        raise ValueError(
            "US security-type binding requires US Shadow session"
        )

    matches = tuple(
        candidate
        for candidate in watchlist.candidates
        if candidate.candidate_id == candidate_id
    )

    if len(matches) != 1:
        raise ValueError(
            "candidate_id must identify exactly one frozen candidate"
        )

    candidate = matches[0]

    if (
        candidate.identity_status != "KNOWN"
        or candidate.instrument_id is None
    ):
        raise ValueError(
            "security type requires known stable candidate identity"
        )

    listing = fact.listing

    if listing.effective_date != watchlist.session.market_date:
        raise ValueError(
            "security type requires exact Shadow market date"
        )

    if listing.instrument_id != candidate.instrument_id:
        raise ValueError(
            "listing instrument_id does not bind Shadow candidate"
        )

    if listing.canonical_symbol != candidate.ticker:
        raise ValueError(
            "listing symbol does not bind Shadow candidate"
        )

    if listing.listing_mic != watchlist.session.calendar_mic:
        raise ValueError(
            "listing MIC does not bind Shadow session"
        )

    # The historical package may be audited later, but this Shadow binding may
    # only use evidence that was already admissible by collection generation.
    require_historical_evidence(
        fact.evidence_package,
        decision_at=watchlist.information_cutoff,
        research_built_at=watchlist.generated_at,
    )

    completion_sha256 = hashlib.sha256(
        _canonical(completion)
    ).hexdigest()

    basis = {
        "schema_version":
            "shadow-security-type-binding-v1",
        "label": LABEL,
        "status": STATUS,
        "comparison_status": COMPARISON_STATUS,
        "scoring": "NOT SCORED",
        "record_id": watchlist.record_id,
        "candidate_id": candidate.candidate_id,
        "market": watchlist.session.market,
        "market_date":
            watchlist.session.market_date.isoformat(),
        "instrument_id": str(candidate.instrument_id),
        "ticker": candidate.ticker,
        "listing_mic": listing.listing_mic,
        "security_type": listing.security_type.value,
        "listing_fact_id": fact.identity,
        "evidence_package_id":
            fact.evidence_package.identity,
        "watchlist_completion_sha256":
            completion_sha256,
    }

    return basis, completion


def record_security_type_binding(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[
        HistoricalEvidencePackage,
        ...
    ],
    candidate_id: str,
    fact: HistoricalUSListingFact,
) -> Path:
    """Publish one immutable pre-cutoff security-type binding."""

    directory = Path(directory)

    basis, completion = _binding_basis(
        directory,
        watchlist,
        watchlist_packages,
        candidate_id,
        fact,
    )

    recorded_at = _now()
    completed_at = datetime.fromisoformat(
        completion["completed_at"]
    )

    if (
        type(recorded_at) is not datetime
        or recorded_at.tzinfo is not timezone.utc
        or not completed_at
        <= recorded_at
        < watchlist.session.decision_cutoff
    ):
        raise ValueError(
            "security-type binding must be recorded "
            "after completion and before decision cutoff"
        )

    binding_id = hashlib.sha256(
        _canonical(basis)
    ).hexdigest()

    payload = basis | {
        "binding_id": binding_id,
        "recorded_at": recorded_at.isoformat(),
    }

    path = (
        directory
        / "security-type-bindings"
        / f"{binding_id}.json"
    )

    result = _publish_once(path, payload)

    observed = _now()
    if (
        observed < recorded_at
        or observed >= watchlist.session.decision_cutoff
    ):
        result.unlink()
        raise ValueError(
            "clock crossed security-type publication boundary"
        )

    return result


def audit_security_type_binding(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[
        HistoricalEvidencePackage,
        ...
    ],
    candidate_id: str,
    fact: HistoricalUSListingFact,
) -> dict:
    """Re-audit one immutable classification and all of its source bindings."""

    directory = Path(directory)

    basis, completion = _binding_basis(
        directory,
        watchlist,
        watchlist_packages,
        candidate_id,
        fact,
    )

    binding_id = hashlib.sha256(
        _canonical(basis)
    ).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    "duplicate security-type receipt field"
                )
            result[key] = value
        return result

    path = (
        directory
        / "security-type-bindings"
        / f"{binding_id}.json"
    )

    receipt = json.loads(
        path.read_bytes(),
        object_pairs_hook=unique_object,
    )

    fields = (
        set(basis)
        | {"binding_id", "recorded_at"}
    )

    if (
        type(receipt) is not dict
        or set(receipt) != fields
    ):
        raise ValueError(
            "unexpected security-type receipt fields"
        )

    recorded_at = datetime.fromisoformat(
        receipt["recorded_at"]
    )
    completed_at = datetime.fromisoformat(
        completion["completed_at"]
    )

    if (
        type(recorded_at) is not datetime
        or recorded_at.tzinfo is not timezone.utc
        or not completed_at
        <= recorded_at
        < watchlist.session.decision_cutoff
        or recorded_at > _now()
    ):
        raise ValueError(
            "invalid security-type receipt clock ordering"
        )

    expected = basis | {
        "binding_id": binding_id,
        "recorded_at": recorded_at.isoformat(),
    }

    if receipt != expected:
        raise ValueError(
            "security-type receipt does not bind exact candidate evidence"
        )

    return receipt
