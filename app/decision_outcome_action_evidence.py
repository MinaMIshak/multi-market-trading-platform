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

``load_egx_outcome_action_evidence`` (v1) refuses the whole load on any
failure with one fixed reason (no partial mapping is returned).
``load_egx_outcome_action_evidence_by_request``
(decision-outcome-action-evidence-by-request-v1) applies the same request
checks and the same per-request selection, but a refusal that concerns only
one observation's evidence (ACTIONS_UNAVAILABLE or ACTIONS_REJECTED) is
reported on that request's own entry (status, detail, no selection) instead of
refusing every other observation; that observation then has no selection, so
the outcome producer's gate reports it AWAITING_ACTION_EVIDENCE and retains
nothing for it. Malformed requests (CONFIG_INVALID) and database failures
(DATABASE_UNAVAILABLE) still refuse the whole load, and no refusal is ever
resolved by choosing other evidence.

The returned selections are inputs only: this module does not
call the check, so no CLEAR / ACTIONS_IN_PATH result exists here, no price is
adjusted, no outcome is evaluated, changed or retained, and the outcome
evaluator, binding, producer, job and store are unchanged (still labelling
corporate actions NOT_CHECKED).

Database: a missing file is refused before any connection (nothing created);
this module issues no SQL of its own. On a successful load nothing is written.
On refusal (of the load, or of one request in the by-request form) the
unchanged ``ReferenceRepository`` may record its existing durable
``data_quality_issues`` audit row (ACTIONS_MISSING_OR_AMBIGUOUS,
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
BY_REQUEST_CONTRACT_VERSION = 'decision-outcome-action-evidence-by-request-v1'
REQUEST_STATUSES = ('SELECTED', 'ACTIONS_UNAVAILABLE', 'ACTIONS_REJECTED')
# Only these refusals concern one request's evidence; any other reason refuses the load.
REQUEST_REFUSALS = REQUEST_STATUSES[1:]
SELECTION_FIELDS = ('source_ingestion_id', 'raw_path', 'raw_sha256', 'provider', 'received_at',
                    'completed_at', 'coverage_start', 'coverage_end', 'published_at',
                    'actions_declared', 'review_ingestion_ids')
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


def _checked(requests, as_of):
    """Every request re-read and windowed before any database access."""
    checked = []
    for identity, record, path_through in requests:
        try:
            record = _record(identity, record)
            instrument_id, first = _window(identity, record, path_through, as_of)
        except (ValueError, TypeError, KeyError) as exc:
            _fail('CONFIG_INVALID', identity, f'record rejected: {exc}')
        checked.append((identity, record, path_through, instrument_id, first))
    return checked


def _request_entry(identity, record, instrument_id, first, path_through):
    return {
        'observation_identity': identity,
        'decision_id': record['decision_id'],
        'symbol': record['symbol'],
        'instrument_id': str(instrument_id),
        'window': {'rule': WINDOW_RULE, 'first_date': first.isoformat(),
                   'last_date': path_through.isoformat(), 'timezone': 'Africa/Cairo'},
    }


def _source(identity, record, instrument_id, first, path_through, row, selection):
    document = selection.evidence
    return {
        **_request_entry(identity, record, instrument_id, first, path_through),
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
    }


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
    checked = _checked(_requests(requests), as_of)
    repository = _repository(database, raw_root)
    selections, sources = {}, []
    for identity, record, path_through, instrument_id, first in checked:
        row, selection = _select(repository, identity, instrument_id, first, path_through, as_of)
        selections[identity] = selection
        sources.append(_source(identity, record, instrument_id, first, path_through, row, selection))
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


def load_egx_outcome_action_evidence_by_request(database, raw_root, requests, *, as_of) -> dict:
    """Like ``load_egx_outcome_action_evidence`` but refusing per request.

    Same arguments and request checks. Each ``sources`` entry (one per
    request, caller order) carries ``status`` (one of REQUEST_STATUSES) and
    ``detail``; refused entries keep the request's identity and window and set
    every selection field to None. ``selections`` holds SELECTED requests
    only. CONFIG_INVALID and DATABASE_UNAVAILABLE still raise
    ``ActionEvidenceLoadError`` for the whole load.
    """
    as_of = _aware(as_of)
    checked = _checked(_requests(requests), as_of)
    repository = _repository(database, raw_root)
    selections, sources = {}, []
    for identity, record, path_through, instrument_id, first in checked:
        try:
            row, selection = _select(
                repository, identity, instrument_id, first, path_through, as_of)
        except ActionEvidenceLoadError as exc:
            if exc.reason not in REQUEST_REFUSALS:
                raise
            sources.append({
                **_request_entry(identity, record, instrument_id, first, path_through),
                **dict.fromkeys(SELECTION_FIELDS),
                'status': exc.reason, 'detail': exc.detail,
            })
            continue
        selections[identity] = selection
        sources.append({
            **_source(identity, record, instrument_id, first, path_through, row, selection),
            'status': 'SELECTED', 'detail': None,
        })
    counts = {status: sum(s['status'] == status for s in sources) for status in REQUEST_STATUSES}
    refused = len(sources) - counts['SELECTED']
    return {
        'schema_version': BY_REQUEST_CONTRACT_VERSION,
        'market': 'EGX',
        'as_of': _utc(as_of),
        'selections': selections,
        'sources': sources,
        'request_counts': {'requests': len(sources), **counts},
        'request_selection': 'CALLER_NAMED_OBSERVATIONS',
        'refusal_scope': 'PER_REQUEST_EVIDENCE_ONLY',
        'refused_request_effect': 'NO_SELECTION_FOR_REQUEST',
        'evidence_selection': 'REFERENCE_REPOSITORY_ACTIONS_POINT_IN_TIME',
        'review_verification': 'REFERENCE_REPOSITORY_REQUIRE_REVIEW',
        'reviewer_authority': 'NOT_ATTESTED',
        'corporate_action_check': 'NOT_PERFORMED_BY_LOADER',
        'price_adjustment': 'NOT_APPLIED',
        'outcome': 'NOT_EVALUATED',
        'database_writes': ('NONE' if refused == 0
                            else 'REPOSITORY_AUDIT_ROWS_POSSIBLE_FOR_REFUSED_REQUESTS'),
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED',
    }
