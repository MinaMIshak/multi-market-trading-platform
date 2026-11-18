"""Load the retained EGX corporate-action selection for outcome-path checks.

Phase G/K groundwork (decision-outcome-action-evidence-v1). The pure check
``app.decision_outcome_corporate_actions.check_egx_outcome_corporate_actions``
takes one exact ``ActionEvidence`` (``egx-actions-v1``) document plus its
source ingestion id and review ingestion ids: the selection the existing
``ReferenceRepository.actions`` makes point-in-time. This loader is the
retained-evidence source for that input; it adds no selection, review, window
or check logic of its own:

1. the caller names each request as an exact (observation identity, retained
   decision-observation-v1 record, path_through date) triple; nothing is
   searched for, defaulted or chosen as "latest";
2. each record is re-read from its exact bytes by the check's own reader
   (content address and EGX market enforced);
3. the window is the check's DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1
   (Africa/Cairo ``data_as_of`` date through ``path_through``), and
   ``path_through`` must lie between the detection date and the ``as_of``
   date, exactly as the check requires;
4. the unchanged ``ReferenceRepository.actions(instrument_id, start, end,
   as_of)`` selects exactly one validated, reviewed egx-actions-v1 document
   received, completed and published by ``as_of`` whose declared coverage
   spans the whole window; missing, partial, overlapping/ambiguous,
   unreviewed or damaged evidence is refused by it (never resolved here by
   preference).

Any failure refuses the whole load with one fixed reason (no partial mapping
is returned). The returned selections are inputs only: this module does not
call the check, so no CLEAR / ACTIONS_IN_PATH result exists here, no price is
adjusted, no outcome is evaluated, changed or retained, and the outcome
evaluator, binding, producer, job and store are unchanged (still labelling
corporate actions NOT_CHECKED).

Database: a missing file is refused before any connection (nothing created);
this module issues no SQL of its own. On a successful load nothing is written.
On refusal the unchanged ``ReferenceRepository`` may record its existing
durable ``data_quality_issues`` audit row (ACTIONS_MISSING_OR_AMBIGUOUS,
SOURCE_UNVALIDATED or REFERENCE_INTEGRITY); that is its established behaviour
and is labelled, not suppressed. Review records are verified by
``ReferenceRepository.require_review`` (subject id, digest, provider and
contract); reviewer authority itself is not attested. The loader has no
caller, job, scheduler or route; only its ``OutcomeActionSelection`` type is
accepted by the outcome producer's optional corporate-action gate.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import os
import sqlite3
from uuid import UUID

from app.data.raw_store import ImmutableRawStore
from app.data.reference import ActionEvidence
from app.decision_observation_store import _time
from app.decision_outcome_corporate_actions import (
    EVIDENCE_CONTRACT, WINDOW_RULE, _record, _uuid_text)
from app.scanner_v2_egx_rank import CAIRO
from app.storage.reference_repository import ReferenceRepository

CONTRACT_VERSION = 'decision-outcome-action-evidence-v1'
LOAD_REASONS = ('CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'ACTIONS_UNAVAILABLE',
                'ACTIONS_REJECTED')
MAX_REQUESTS = 1000
MAX_DETAIL = 200


class ActionEvidenceLoadError(RuntimeError):
    """Load refused; ``reason`` is one of LOAD_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class OutcomeActionSelection:
    """The exact ``ReferenceRepository.actions`` selection for one observation."""

    evidence: ActionEvidence
    source_ingestion_id: str
    review_ingestion_ids: tuple


def _fail(reason, identity, why):
    raise ActionEvidenceLoadError(reason, f'{identity}: {why}'[:MAX_DETAIL])


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ActionEvidenceLoadError('CONFIG_INVALID', 'timezone-aware as_of required')
    return value.astimezone(timezone.utc)


def _utc(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _repository(database, raw_root):
    if type(raw_root) is not str or not os.path.isabs(raw_root) or '\x00' in raw_root:
        raise ActionEvidenceLoadError('CONFIG_INVALID', 'absolute raw root required')
    # Database.connect creates a missing file; refuse first so nothing is created.
    path = getattr(database, 'path', None)
    try:
        exists = isinstance(path, Path) and path.is_file()
    except OSError:
        exists = False
    if not exists:
        raise ActionEvidenceLoadError('DATABASE_UNAVAILABLE', 'existing database file required')
    return ReferenceRepository(database, ImmutableRawStore(raw_root))


def _window(identity, record, path_through, as_of):
    """(instrument UUID, first date) for one request, by the check's own rule."""
    if type(path_through) is not date:
        _fail('CONFIG_INVALID', identity, 'path_through date required')
    detected = _time(record['detected_at'], 'detected_at').astimezone(CAIRO).date()
    first = _time(record['data_as_of'], 'data_as_of').astimezone(CAIRO).date()
    if first > detected:
        _fail('CONFIG_INVALID', identity, 'record data_as_of after detection')
    if path_through < detected:
        _fail('CONFIG_INVALID', identity, 'path date before detection')
    if path_through > as_of.astimezone(CAIRO).date():
        _fail('CONFIG_INVALID', identity, 'path date after as_of')
    return UUID(record['instrument_id']), first


def _requests(requests):
    if (type(requests) is not tuple or not 0 < len(requests) <= MAX_REQUESTS
            or any(type(item) is not tuple or len(item) != 3 for item in requests)):
        raise ActionEvidenceLoadError(
            'CONFIG_INVALID',
            f'tuple of 1..{MAX_REQUESTS} (identity, record, path_through) tuples required')
    identities = [item[0] for item in requests]
    if any(type(identity) is not str for identity in identities):
        raise ActionEvidenceLoadError('CONFIG_INVALID', 'observation identity string required')
    if len(set(identities)) != len(identities):
        raise ActionEvidenceLoadError('CONFIG_INVALID', 'duplicate observation identity')
    return requests


def _select(repository, identity, instrument_id, first, path_through, as_of):
    try:
        row, document, reviews = repository.actions(
            instrument_id=instrument_id, start=first, end=path_through, as_of=as_of)
    except sqlite3.Error:
        raise ActionEvidenceLoadError('DATABASE_UNAVAILABLE') from None
    except (ValueError, TypeError, RuntimeError, OSError) as exc:
        # Missing, partial, ambiguous, unreviewed or damaged: refused, never guessed.
        _fail('ACTIONS_UNAVAILABLE', identity, str(exc) or type(exc).__name__)
    # Defensive re-checks of what the repository returned; never repaired.
    if type(document) is not ActionEvidence or document.contract != EVIDENCE_CONTRACT:
        _fail('ACTIONS_REJECTED', identity, 'exact egx-actions-v1 evidence required')
    if (document.instrument_id != instrument_id or document.coverage_start > first
            or document.coverage_end < path_through):
        _fail('ACTIONS_REJECTED', identity, 'selection does not cover the window')
    try:
        source = _uuid_text(row['ingestion_id'], 'source_ingestion_id')
        reviews = tuple(_uuid_text(item, 'review_ingestion_id') for item in reviews)
    except (ValueError, TypeError, IndexError, KeyError):
        _fail('ACTIONS_REJECTED', identity, 'non-canonical provenance id')
    if not reviews or len(set(reviews)) != len(reviews) or source in reviews:
        _fail('ACTIONS_REJECTED', identity, 'reviews must be unique and distinct from source')
    return row, OutcomeActionSelection(
        evidence=document, source_ingestion_id=source, review_ingestion_ids=reviews)


def load_egx_outcome_action_evidence(database, raw_root, requests, *, as_of) -> dict:
    """Return ``{'selections': {identity: OutcomeActionSelection}, 'sources': [...]}``.

    ``database`` is an existing ``app.storage.Database``; ``raw_root`` the raw
    store root; ``requests`` a tuple of (observation identity, retained
    decision-observation-v1 record, path_through date) triples; ``as_of`` the
    aware point-in-time cut passed unchanged to ``ReferenceRepository.actions``
    (and to be passed unchanged to the check). ``sources`` lists one provenance
    entry per request in the given order. Refusals raise
    ``ActionEvidenceLoadError``.
    """
    as_of = _aware(as_of)
    requests = _requests(requests)
    checked = []
    for identity, record, path_through in requests:
        try:
            record = _record(identity, record)
            instrument_id, first = _window(identity, record, path_through, as_of)
        except (ValueError, TypeError, KeyError) as exc:
            _fail('CONFIG_INVALID', identity, f'record rejected: {exc}')
        checked.append((identity, record, path_through, instrument_id, first))

    repository = _repository(database, raw_root)
    selections, sources = {}, []
    for identity, record, path_through, instrument_id, first in checked:
        row, selection = _select(repository, identity, instrument_id, first, path_through, as_of)
        selections[identity] = selection
        document = selection.evidence
        sources.append({
            'observation_identity': identity,
            'decision_id': record['decision_id'],
            'symbol': record['symbol'],
            'instrument_id': str(instrument_id),
            'window': {'rule': WINDOW_RULE, 'first_date': first.isoformat(),
                       'last_date': path_through.isoformat(), 'timezone': 'Africa/Cairo'},
            'source_ingestion_id': selection.source_ingestion_id,
            'raw_path': row['raw_path'],
            'raw_sha256': row['sha256'],
            'provider': row['provider'],
            'received_at': row['received_at'],
            'completed_at': row['completed_at'],
            'coverage_start': document.coverage_start.isoformat(),
            'coverage_end': document.coverage_end.isoformat(),
            'published_at': _utc(document.published_at),
            'actions_declared': len(document.actions),
            'review_ingestion_ids': list(selection.review_ingestion_ids),
        })
    return {
        'schema_version': CONTRACT_VERSION,
        'market': 'EGX',
        'as_of': _utc(as_of),
        'selections': selections,
        'sources': sources,
        'request_selection': 'CALLER_NAMED_OBSERVATIONS',
        'evidence_selection': 'REFERENCE_REPOSITORY_ACTIONS_POINT_IN_TIME',
        'review_verification': 'REFERENCE_REPOSITORY_REQUIRE_REVIEW',
        'reviewer_authority': 'NOT_ATTESTED',
        'corporate_action_check': 'NOT_PERFORMED_BY_LOADER',
        'price_adjustment': 'NOT_APPLIED',
        'outcome': 'NOT_EVALUATED',
        'database_writes': 'NONE_ON_SUCCESS',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED',
    }
