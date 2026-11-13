"""Write-once content-addressed store for decision-observation-v1 records.

Phase G groundwork. A trusted host producer (not yet provisioned) calls
``retain_decision_observation`` with one exact ``TradeDecision``, the optional
``EventContext`` available at detection time and a host-provisioned directory.
The record is built by the unchanged ``app.decision_observation`` function with
``recorded_at`` set to the store clock, so the recording window
(detection <= recorded_at < fresh_until / valid_until / flat_by) is enforced at
the moment of retention and a record cannot be written after the decision
stopped being current. Records carry no outcome.

Layout mirrors ``app.news_event_store`` and reuses its directory, write-once and
bounded no-follow read helpers: one ``<sha256>.json`` file per record holding
``canonical_bytes(record)``; the file name is the SHA-256 of its bytes. There is
no pointer, and no record is ever replaced or deleted.

Retention, under an exclusive non-blocking lock on the directory:

1. builds the record, encodes it and passes the exact bytes through the same
   ``read_retained_decision_observation`` gate readers use, before any write;
2. reads every retained record (any damaged or unexpected entry, or two records
   for one decision, fails closed);
3. one record per decision: if the decision is already observed and the held
   record equals the new one apart from ``recorded_at``, the FIRST observation
   is kept and returned as ALREADY_RETAINED; any other difference (for example
   a different attached event context) is OBSERVATION_CONFLICT;
4. writes the file once (temporary file, fsync, hard link without overwrite),
   fsyncs the directory and re-reads the exact bytes through the reader.

The reader validates structure, fixed safety values, the attached event-context
digest, the recording window and risk per share. It does NOT rebuild the
``TradeDecision`` or re-derive ``decision_id``; a digest is integrity under a
trusted host writer, not a signature. No provider, scheduler, route, outcome
evaluator or caller exists. Nothing is admitted, filled or promoted;
performance_claim NONE; LIVE_MONEY=false.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
import re

from app.decision_observation import CONTRACT_VERSION, canonical_bytes, decision_observation
from app.news_event_record import MAX_BYTES, _constant, _pairs
from app.news_event_store import (
    _RECORD_NAME, _TEMP_NAME, NewsEventStoreError, _open_directory, _write_once)
from app.scanner_v2_card_report_loader import _read_regular, _Unavailable

RESULT_SCHEMA = 'decision-observation-retention-v1'
MAX_RECORDS = 10000
STORE_REASONS = (
    'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'DIRECTORY_UNSAFE', 'STORE_BUSY',
    'RECORD_REJECTED', 'STORE_INVALID', 'STORE_FULL', 'OBSERVATION_CONFLICT',
    'CONTENT_CONFLICT', 'WRITE_FAILED', 'RETENTION_UNVERIFIED')
RECORD_FIELDS = frozenset((
    'contract_version', 'decision_id', 'decision_contract_version', 'market', 'symbol',
    'instrument_id', 'strategy_id', 'strategy_version', 'config_id', 'candidate_state',
    'candidate_sha256', 'detected_at', 'data_as_of', 'available_at', 'fresh_until',
    'provenance', 'terms', 'risk_per_share', 'recorded_at', 'recorded_at_clock',
    'event_context', 'outcome_status', 'outcome', 'evaluation_status', 'performance_claim',
    'risk_status', 'confidence', 'event_risk', 'promotion', 'mode', 'live_money',
    'execution_allowed'))
# Values every decision-observation-v1 record must carry exactly.
FIXED_VALUES = {
    'contract_version': CONTRACT_VERSION,
    'decision_contract_version': 'trade-decision-v1',
    'recorded_at_clock': 'CALLER_SUPPLIED_UNVERIFIED',
    'outcome_status': 'NOT_OBSERVED',
    'outcome': None,
    'evaluation_status': 'PENDING_OUTCOME',
    'performance_claim': 'NONE',
    'risk_status': 'NOT_EVALUATED',
    'confidence': 'UNVALIDATED',
    'event_risk': 'UNKNOWN',
    'promotion': 'NOT_AUTHORIZED',
    'mode': 'SHADOW',
    'live_money': False,
    'execution_allowed': False,
}
_HEX64 = re.compile(r'[0-9a-f]{64}')


class RetainedDecisionObservationError(ValueError):
    """Retained observation bytes failed closed; nothing from them may be used."""


class DecisionObservationStoreError(RuntimeError):
    """Retention or load refused; ``reason`` is one of STORE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _fail(reason):
    raise RetainedDecisionObservationError(reason)


def _time(value, name):
    if type(value) is not str:
        _fail(f'{name} must be a timestamp string')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        _fail(f'{name} is not an ISO timestamp')
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f'{name} must be timezone-aware')
    return parsed


def _decimal(value, name):
    if type(value) is not str:
        _fail(f'{name} must be a decimal string')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail(f'{name} is not a decimal')
    if not parsed.is_finite():
        _fail(f'{name} must be finite')
    return parsed


def _check_record(document):
    if set(document) != RECORD_FIELDS:
        _fail('retained observation field set mismatch')
    for key, value in FIXED_VALUES.items():
        held = document[key]
        if type(held) is not type(value) or held != value:
            _fail(f'retained observation {key} is not {value!r}')
    if document['market'] not in ('EGX', 'US'):
        _fail('unknown market')
    for key in ('decision_id', 'candidate_sha256'):
        if type(document[key]) is not str or _HEX64.fullmatch(document[key]) is None:
            _fail(f'{key} must be a SHA-256 hex digest')
    for key in ('symbol', 'instrument_id', 'strategy_id', 'strategy_version', 'config_id',
                'candidate_state'):
        if type(document[key]) is not str or not document[key]:
            _fail(f'{key} must be a non-empty string')
    provenance = document['provenance']
    if type(provenance) is not dict or set(provenance) != {
            'source_id', 'artifact_sha256', 'evidence_ids'}:
        _fail('provenance field set mismatch')
    if type(provenance['evidence_ids']) is not list:
        _fail('provenance evidence_ids must be a list')

    terms = document['terms']
    if type(terms) is not dict or not {'entry_reference', 'stop', 'valid_until', 'flat_by'} <= set(terms):
        _fail('terms must carry entry_reference, stop, valid_until and flat_by')
    risk = _decimal(terms['entry_reference'], 'entry_reference') - _decimal(terms['stop'], 'stop')
    if risk <= 0 or document['risk_per_share'] != str(risk):
        _fail('risk_per_share does not match terms')

    detected = _time(document['detected_at'], 'detected_at')
    recorded = _time(document['recorded_at'], 'recorded_at')
    _time(document['data_as_of'], 'data_as_of')
    _time(document['available_at'], 'available_at')
    if recorded < detected:
        _fail('observation recorded before detection')
    if recorded >= _time(document['fresh_until'], 'fresh_until'):
        _fail('observation recorded after source freshness ended')
    if recorded >= _time(terms['valid_until'], 'valid_until') or (
            terms['flat_by'] is not None and recorded >= _time(terms['flat_by'], 'flat_by')):
        _fail('observation recorded after plan expiry')

    context = document['event_context']
    if type(context) is not dict or set(context) != {'status', 'section', 'section_sha256'}:
        _fail('event_context field set mismatch')
    if context['status'] == 'NOT_ATTACHED':
        if context['section'] is not None or context['section_sha256'] is not None:
            _fail('NOT_ATTACHED event_context must carry no section')
    elif context['status'] == 'ATTACHED':
        section = context['section']
        if type(section) is not dict:
            _fail('ATTACHED event_context requires a section')
        if context['section_sha256'] != sha256(canonical_bytes(section)).hexdigest():
            _fail('event_context section digest mismatch')
        if section.get('decision_id') != document['decision_id']:
            _fail('event_context section bound to another decision')
        if section.get('live_money') is not False:
            _fail('event_context section must carry live_money false')
    else:
        _fail('unknown event_context status')


def read_retained_decision_observation(payload, *, expected_sha256) -> dict:
    """Validate exact retained bytes; return the decision-observation-v1 record."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        _fail('retained observation must be bounded bytes')
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        _fail('invalid expected_sha256')
    if sha256(payload).hexdigest() != expected_sha256:
        _fail('retained observation hash mismatch')
    try:
        document = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail('retained observation is not strict JSON')
    except ValueError as exc:  # duplicate key or non-finite number from the hooks
        _fail(str(exc))
    if type(document) is not dict:
        _fail('retained observation is not a JSON object')
    try:
        canonical = canonical_bytes(document)
    except (TypeError, ValueError, RecursionError):
        _fail('retained observation cannot be re-encoded')
    if canonical != payload:
        _fail('retained observation is not canonical')
    _check_record(document)
    return document


def encode_decision_observation(record) -> bytes:
    """Canonical retained bytes of one decision-observation-v1 record."""
    if type(record) is not dict:
        raise ValueError('decision-observation-v1 dict required')
    payload = canonical_bytes(record)
    read_retained_decision_observation(payload, expected_sha256=sha256(payload).hexdigest())
    return payload


@contextmanager
def _translated():
    """Re-raise reused news-event store refusals under this store's error type."""
    try:
        yield
    except NewsEventStoreError as exc:
        raise DecisionObservationStoreError(exc.reason, exc.detail) from None


def _read_record(dir_fd, identity):
    try:
        payload = _read_regular(dir_fd, f'{identity}.json', MAX_BYTES, 'RECORD_UNREADABLE')
    except _Unavailable as exc:
        raise DecisionObservationStoreError('STORE_INVALID', exc.reason) from None
    try:
        return payload, read_retained_decision_observation(payload, expected_sha256=identity)
    except (RetainedDecisionObservationError, RecursionError) as exc:
        raise DecisionObservationStoreError('STORE_INVALID', str(exc)) from None


def _scan(dir_fd):
    """Every retained record keyed by identity; unexpected entries fail closed."""
    try:
        names = os.listdir(dir_fd)
    except OSError:
        raise DecisionObservationStoreError('DIRECTORY_UNAVAILABLE') from None
    identities = []
    for name in names:
        match = _RECORD_NAME.fullmatch(name)
        if match is not None:
            identities.append(match.group(1))
        elif _TEMP_NAME.fullmatch(name) is None:
            raise DecisionObservationStoreError('STORE_INVALID', 'unexpected directory entry')
    if len(identities) > MAX_RECORDS:
        raise DecisionObservationStoreError('STORE_INVALID', 'too many records')
    records = {identity: _read_record(dir_fd, identity)[1] for identity in sorted(identities)}
    decisions = [record['decision_id'] for record in records.values()]
    if len(set(decisions)) != len(decisions):
        raise DecisionObservationStoreError('STORE_INVALID', 'decision observed more than once')
    return records


def load_retained_decision_observations(directory, *, market) -> tuple[tuple[str, dict], ...]:
    """``(identity, record)`` pairs of one market, ordered by identity.

    Fails closed (``DecisionObservationStoreError``) if any entry in the
    directory is damaged or unexpected; partial content is never returned. The
    records are pending observations, not outcomes or performance.
    """
    if market not in ('EGX', 'US'):
        raise ValueError('unknown market')
    with _translated():
        dir_fd = _open_directory(directory)
    try:
        records = _scan(dir_fd)
    finally:
        os.close(dir_fd)
    return tuple((identity, record) for identity, record in records.items()
                 if record['market'] == market)


def _aware(now):
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    return now


def _result(status, identity, record, retained_count):
    return {
        'schema_version': RESULT_SCHEMA, 'status': status, 'identity': identity,
        'decision_id': record['decision_id'], 'market': record['market'],
        'strategy_id': record['strategy_id'], 'strategy_version': record['strategy_version'],
        'recorded_at': record['recorded_at'], 'retained_count': retained_count,
        'outcome_status': 'NOT_OBSERVED', 'performance_claim': 'NONE',
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


def _without_recording_time(record):
    return {key: value for key, value in record.items() if key != 'recorded_at'}


def retain_decision_observation(directory, decision, context=None, *, now=None):
    """Retain one decision observation write-once in a host-provisioned directory.

    ``now`` is the store clock (defaults to real UTC) and becomes the record's
    ``recorded_at``. Returns a ``decision-observation-retention-v1`` result with
    status RETAINED or ALREADY_RETAINED (first observation kept). Any refusal
    raises ``DecisionObservationStoreError``; refusals before the write leave
    the directory unchanged. A retry after the recording window has closed is
    RECORD_REJECTED even if the first observation was retained.
    """
    now = _aware(now)
    try:
        record = decision_observation(decision, context, recorded_at=now)
        payload = encode_decision_observation(record)
        digest = sha256(payload).hexdigest()
        accepted = read_retained_decision_observation(payload, expected_sha256=digest)
    except (ValueError, TypeError, RecursionError) as exc:
        raise DecisionObservationStoreError('RECORD_REJECTED', type(exc).__name__) from None

    with _translated():
        dir_fd = _open_directory(directory)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DecisionObservationStoreError('STORE_BUSY') from None

        retained = _scan(dir_fd)
        if digest in retained:
            return _result('ALREADY_RETAINED', digest, accepted, len(retained))
        for identity, held in retained.items():
            if held['decision_id'] == accepted['decision_id']:
                if _without_recording_time(held) != _without_recording_time(accepted):
                    raise DecisionObservationStoreError('OBSERVATION_CONFLICT')
                return _result('ALREADY_RETAINED', identity, held, len(retained))
        if len(retained) >= MAX_RECORDS:
            raise DecisionObservationStoreError('STORE_FULL')

        try:
            with _translated():
                _write_once(dir_fd, digest, payload)
        except OSError as exc:
            raise DecisionObservationStoreError('WRITE_FAILED', type(exc).__name__) from None

        try:
            stored, verified = _read_record(dir_fd, digest)
        except DecisionObservationStoreError as exc:
            raise DecisionObservationStoreError('RETENTION_UNVERIFIED', exc.detail) from None
        if stored != payload or verified != accepted:
            raise DecisionObservationStoreError('RETENTION_UNVERIFIED', 'retained bytes differ')
        return _result('RETAINED', digest, accepted, len(retained) + 1)
    finally:
        os.close(dir_fd)
