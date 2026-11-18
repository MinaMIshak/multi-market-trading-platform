"""Check retained EGX corporate-action evidence over one outcome price path.

Phase G/K groundwork (decision-outcome-corporate-actions-v1). The outcome path
(``decision_outcome_session_bars`` -> ``evaluate_decision_outcome``) compares
raw canonical daily OHLC with plan prices fixed from data as of the decision's
``data_as_of``. A split, dividend ex-date, rights issue, capital change, symbol
change or delisting effective inside that path makes the raw path incomparable
with the plan, so an outcome computed over it is not evidence. The outcome
contract therefore labels corporate actions NOT_CHECKED.

This pure function is the first check of that path against retained reference
evidence. It composes existing contracts only:

- one retained decision-observation-v1 record, re-read from its exact bytes;
- one exact ``ActionEvidence`` (``egx-actions-v1``) document: the complete,
  explicitly bounded action coverage that ``ReferenceRepository.actions``
  selects point-in-time (exactly one reviewed document covering the interval,
  received, completed and published by its ``as_of``), together with that
  document's ingestion id and the review ingestion ids it returned.

Window rule DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1: every Africa/Cairo
date from the record's ``data_as_of`` date through ``path_through`` (the
outcome path's final date) inclusive. The ``data_as_of`` date is included
deliberately: whether its bar already reflected an action is not established
here, so the check errs toward ACTIONS_IN_PATH, never toward CLEAR.

Fail closed (ValueError): non-EGX or edited record; evidence for another
instrument; coverage that does not span the whole window (absence of an action
outside declared coverage is never evidence); evidence published after
``as_of``; a path date before detection or after ``as_of``; malformed
provenance ids; naive clocks.

Result: CLEAR (complete declared coverage spans the window and lists no action
in it) or ACTIONS_IN_PATH (every action in the window listed, any type, with
its declared adjustment effect). Nothing is adjusted: prices are never
rescaled, no ratio is applied and no outcome is evaluated, changed or retained.
The review ids are caller-asserted; this function does not re-read reviews.
No IO, store, route, scheduler or caller; the outcome evaluator, binding,
producer and store are unchanged and still label corporate actions NOT_CHECKED.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import date, datetime, timezone
from uuid import UUID

from app.data.reference import ActionEvidence
from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time, read_retained_decision_observation
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-corporate-actions-v1'
WINDOW_RULE = 'DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1'
EVIDENCE_CONTRACT = 'egx-actions-v1'
MAX_REVIEWS = 64
STATUSES = ('CLEAR', 'ACTIONS_IN_PATH')


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'timezone-aware {name} required')
    return value.astimezone(timezone.utc)


def _uuid_text(value, name):
    if type(value) is not str:
        raise ValueError(f'{name} must be a canonical UUID string')
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f'{name} must be a canonical UUID string') from exc
    if str(parsed) != value:
        raise ValueError(f'{name} must be a canonical UUID string')
    return value


def _record(identity, record):
    if type(record) is not dict:
        raise ValueError('decision-observation-v1 record dict required')
    try:
        payload = canonical_bytes(record)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('record is not canonical JSON data') from exc
    record = read_retained_decision_observation(payload, expected_sha256=identity)
    if record['market'] != 'EGX':
        raise ValueError('EGX observation required')
    return record


def _evidence(evidence):
    if type(evidence) is not ActionEvidence:
        raise ValueError('exact egx-actions-v1 ActionEvidence required')
    # Revalidate: model_copy/model_construct bypass checks.
    evidence = ActionEvidence.model_validate(evidence.model_dump(mode='python'))
    if evidence.contract != EVIDENCE_CONTRACT:
        raise ValueError('egx-actions-v1 evidence required')
    return evidence


def check_egx_outcome_corporate_actions(identity, record, evidence, *, source_ingestion_id,
                                        review_ingestion_ids, path_through, as_of) -> dict:
    """Return a CLEAR / ACTIONS_IN_PATH report for one observation's path; pure."""
    record = _record(identity, record)
    evidence = _evidence(evidence)
    source_ingestion_id = _uuid_text(source_ingestion_id, 'source_ingestion_id')
    if (type(review_ingestion_ids) is not tuple
            or not 0 < len(review_ingestion_ids) <= MAX_REVIEWS):
        raise ValueError(f'tuple of 1..{MAX_REVIEWS} review ingestion ids required')
    reviews = tuple(_uuid_text(item, 'review_ingestion_id') for item in review_ingestion_ids)
    if len(set(reviews)) != len(reviews) or source_ingestion_id in reviews:
        raise ValueError('review ingestion ids must be unique and distinct from the source')
    if type(path_through) is not date:
        raise ValueError('path_through date required')
    as_of = _aware(as_of, 'as_of')

    if str(evidence.instrument_id) != record['instrument_id']:
        raise ValueError('action evidence instrument mismatch')
    published = _aware(evidence.published_at, 'published_at')
    if published > as_of:
        raise ValueError('action evidence published after as_of')
    detected = _aware(_time(record['detected_at'], 'detected_at'), 'detected_at')
    data_as_of = _aware(_time(record['data_as_of'], 'data_as_of'), 'data_as_of')
    if data_as_of > detected:
        raise ValueError('record data_as_of after detection')
    first = data_as_of.astimezone(CAIRO).date()
    if path_through < detected.astimezone(CAIRO).date():
        raise ValueError('path date before detection')
    if path_through > as_of.astimezone(CAIRO).date():
        raise ValueError('path date after as_of')
    # Absence of an action is evidence only inside complete declared coverage.
    if evidence.coverage_start > first or evidence.coverage_end < path_through:
        raise ValueError('action coverage does not span the outcome window')

    in_path = sorted((action for action in evidence.actions
                      if first <= action.effective_date <= path_through),
                     key=lambda action: (action.effective_date, action.event_id))
    status = 'ACTIONS_IN_PATH' if in_path else 'CLEAR'
    return {
        'contract_version': CONTRACT_VERSION,
        'observation_identity': identity,
        'decision_id': record['decision_id'],
        'market': 'EGX',
        'symbol': record['symbol'],
        'instrument_id': record['instrument_id'],
        'window': {
            'rule': WINDOW_RULE,
            'first_date': first.isoformat(),
            'last_date': path_through.isoformat(),
            'timezone': 'Africa/Cairo',
        },
        'evidence': {
            'contract': evidence.contract,
            'coverage_start': evidence.coverage_start.isoformat(),
            'coverage_end': evidence.coverage_end.isoformat(),
            'published_at': _utc(published),
            'complete': True,
            'actions_declared': len(evidence.actions),
            'source_ingestion_id': source_ingestion_id,
            'review_ingestion_ids': sorted(reviews),
            'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        },
        'as_of': _utc(as_of),
        'status': status,
        'actions_in_path': [{
            'event_id': action.event_id,
            'effective_date': action.effective_date.isoformat(),
            'action_type': action.action_type,
            'adjustment_effect': action.adjustment_effect,
        } for action in in_path],
        'clear_basis': 'COMPLETE_DECLARED_COVERAGE' if status == 'CLEAR' else None,
        'price_adjustment': 'NOT_APPLIED',
        'outcome': 'NOT_EVALUATED',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
