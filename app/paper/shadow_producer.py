"""Explicit forward-only M4 collection producer; never a scheduler or execution job."""
from datetime import datetime, timezone
import json
from pathlib import Path

from app.paper.shadow_candidate_admission import ShadowCandidateAdmission, admit_strategy_candidate
from app.paper.shadow_collection import _admit_packages, _publish_once, complete_watchlist
from app.paper.shadow_ledger import append_candidate_event, audit_candidate_event
from app.paper.shadow_records import ShadowEvidenceReference, ShadowSession, ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage
from app.strategies.contracts import Candidate, Contract


class StrategyShadowSelection(Contract):
    candidate: Candidate
    admission: ShadowCandidateAdmission


class StrategyShadowRequest(Contract):
    record_id: str
    information_cutoff: datetime
    session: ShadowSession
    evidence: tuple[ShadowEvidenceReference, ...]
    selections: tuple[StrategyShadowSelection, ...]
    evidence_packages: tuple[HistoricalEvidencePackage, ...]


def _now():
    return datetime.now(timezone.utc)


def produce_strategy_watchlist(directory: Path, request: StrategyShadowRequest) -> dict:
    """Publish one new isolated collection and its existing-UI envelope last.

    Exact explicit selections only: no ranking, strategy-state mapping, geometry
    conversion, evidence review, empty-result inference or execution. The caller
    retains responsibility for the truth of supplied strategy and review inputs.
    A failed publication may leave unscored receipts; never overwrite or backfill.
    """
    if type(request) is not StrategyShadowRequest:
        raise ValueError("exact StrategyShadowRequest required")
    request = StrategyShadowRequest(**{
        name: getattr(request, name) for name in StrategyShadowRequest.model_fields
    })
    candidates = []
    for selection in request.selections:
        if type(selection) is not StrategyShadowSelection:
            raise ValueError("exact strategy selection required")
        candidate = selection.candidate
        admitted = admit_strategy_candidate(candidate, selection.admission)
        if candidate.decision_time > request.information_cutoff:
            raise ValueError("strategy decision after information cutoff")
        candidates.append(admitted)
    watchlist = ShadowWatchlist(
        record_id=request.record_id, generated_at=_now(),
        information_cutoff=request.information_cutoff,
        session=request.session, evidence=request.evidence, candidates=tuple(candidates),
    )
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    _admit_packages(watchlist, request.evidence_packages, built_at=watchlist.generated_at)
    envelope = {
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': watchlist.model_dump(mode='json'),
        'evidence_packages': [p.model_dump(mode='json') for p in request.evidence_packages],
    }
    # Match the existing bounded UI reader, including expansion of provenance.
    if len(json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode()) > 4 * 1024 * 1024:
        raise ValueError('collection exceeds UI input size limit')
    directory = Path(directory)
    if (not directory.is_absolute() or '..' in directory.parts
            or any(parent.is_symlink() for parent in (directory, *directory.parents))):
        raise ValueError("absolute unlinked collection directory required")
    # Exclusive ownership prevents mixing sessions or replacing an existing UI input.
    # Parent must already exist; the producer never guesses a storage root.
    directory.mkdir()
    _publish_once(directory / 'strategy-source.json', request.model_dump(mode='json'))
    complete_watchlist(directory, watchlist, request.evidence_packages)
    append_candidate_event(directory, watchlist, request.evidence_packages)
    event = audit_candidate_event(directory, watchlist, request.evidence_packages)
    _publish_once(directory / 'input.json', envelope)
    return {'label': watchlist.label, 'scoring': 'NOT SCORED',
            'record_id': watchlist.record_id, 'event_id': event['event_id'],
            'candidate_count': len(candidates), 'execution_status': 'NO EXECUTION INFERENCE'}
