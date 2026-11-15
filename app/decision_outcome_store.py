"""Write-once content-addressed store for terminal decision-outcome-v1 results.

Phase G/H groundwork. A trusted host caller (none exists yet) passes one retained
decision-observation-v1 ``(identity, record)`` pair and the decision-outcome-v1
result that ``evaluate_decision_outcome`` produced for it. The outcome is
retained so later metrics are computed from immutable retained evidence rather
than re-evaluated on demand.

Only TERMINAL outcomes are retained: CLOSED, NO_ENTRY and UNEVALUABLE.
PENDING_PATH is refused (OUTCOME_NOT_FINAL) because it is expected to change;
a decision without a retained outcome stays NOT_EVALUATED in the unchanged
aggregate metrics, so withholding an outcome can never improve a segment.

One outcome per decision and exact ``OutcomeAssumptions``. The first terminal
outcome is kept. A retry that differs only in ``path_complete_through_at``,
``evaluated_at`` or ``bars_supplied`` returns ALREADY_RETAINED with the first
record; any other difference (another observation of the decision, another
status, exit, price, metric or bar provenance, for example after a data
revision) is OUTCOME_CONFLICT. A held loss therefore cannot be replaced by a
re-evaluation, and a revision is visible instead of silently applied.

Layout and helpers are those of ``app.decision_observation_store`` /
``app.news_event_store``: one ``<sha256>.json`` file (0444) per record holding
the canonical bytes; the file name is their SHA-256. No pointer; nothing is
replaced or deleted. Retention, under an exclusive non-blocking lock:

1. re-verifies the observation against its content address, checks the outcome
   with the unchanged ``decision_outcome_metrics._check_outcome`` (field set,
   fixed safety labels, current rules, exact assumptions, binding to the
   observation, evaluated at or before the store clock, registered status and
   reason, metrics iff CLOSED) and passes the exact bytes through the same
   reader gate used on load, before any write;
2. reads every retained record (damaged or unexpected entries, or two records
   for one decision and assumptions, fail closed);
3. applies the one-outcome rule above, then writes once (temporary file, fsync,
   hard link without overwrite), fsyncs the directory and re-reads the bytes.

The reader alone cannot see the observation; it checks structure only. The
observation binding is checked at retention and again by
``aggregate_decision_outcomes`` against the caller's population. A digest is
integrity under a trusted host writer, not a signature. Bars are not retained
here, so numbers are not recomputed. Outcomes are hypothetical results from
session bars: not fills, not execution evidence, not performance. No provider,
scheduler, route or caller. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
import re

from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time, read_retained_decision_observation
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import BOUND_FIELDS, OUTCOME_FIELDS, _check_outcome
from app.news_event_record import MAX_BYTES, _constant, _pairs
from app.news_event_store import (
    _RECORD_NAME, _TEMP_NAME, NewsEventStoreError, _open_directory, _write_once)
from app.scanner_v2_card_report_loader import _read_regular, _Unavailable

RESULT_SCHEMA = 'decision-outcome-retention-v1'
MAX_RECORDS = 10000
TERMINAL_STATUSES = ('CLOSED', 'NO_ENTRY', 'UNEVALUABLE')
# Fields that may differ between two evaluations of one observation under one
# assumptions set without changing the retained outcome.
EVALUATION_FIELDS = ('path_complete_through_at', 'evaluated_at', 'bars_supplied')
STORE_REASONS = (
    'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'DIRECTORY_UNSAFE', 'STORE_BUSY',
    'RECORD_REJECTED', 'OUTCOME_NOT_FINAL', 'STORE_INVALID', 'STORE_FULL',
    'OUTCOME_CONFLICT', 'CONTENT_CONFLICT', 'WRITE_FAILED', 'RETENTION_UNVERIFIED')
_BPS_FIELDS = ('entry_slippage_bps', 'exit_slippage_bps', 'cost_bps_per_side')
_HEX64 = re.compile(r'[0-9a-f]{64}')


class RetainedDecisionOutcomeError(ValueError):
    """Retained outcome bytes failed closed; nothing from them may be used."""


class DecisionOutcomeStoreError(RuntimeError):
    """Retention or load refused; ``reason`` is one of STORE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


class _NotFinal(Exception):
    """A valid outcome that is still PENDING_PATH; never retained."""


def _fail(reason):
    raise RetainedDecisionOutcomeError(reason)


def _assumptions(held):
    """JSON form of the exact OutcomeAssumptions ``held`` encodes, or fail."""
    if type(held) is not dict:
        _fail('outcome assumptions must be an object')
    values = dict(held)
    try:
        for key in _BPS_FIELDS:
            if type(values.get(key)) is not str:
                _fail(f'assumptions {key} must be a decimal string')
            values[key] = Decimal(values[key])
        dumped = OutcomeAssumptions(**values).model_dump(mode='json')
    except (ValueError, TypeError, InvalidOperation) as exc:
        if isinstance(exc, RetainedDecisionOutcomeError):
            raise
        _fail('outcome assumptions are not valid OutcomeAssumptions')
    if dumped != held:
        _fail('outcome assumptions are not in canonical form')
    return dumped


def _check_structure(document):
    """Structure of one terminal decision-outcome-v1 record, without its observation."""
    if set(document) != OUTCOME_FIELDS:
        _fail('retained outcome field set mismatch')
    for key in ('observation_identity', 'decision_id'):
        if type(document[key]) is not str or _HEX64.fullmatch(document[key]) is None:
            _fail(f'{key} must be a SHA-256 hex digest')
    if document['market'] not in ('EGX', 'US'):
        _fail('unknown market')
    assumptions = _assumptions(document['assumptions'])
    if assumptions['market'] != document['market']:
        _fail('outcome assumptions market mismatch')
    if type(document['bars_supplied']) is not int or document['bars_supplied'] < 0:
        _fail('bars_supplied must be a non-negative integer')
    if type(document['bars_used']) is not list:
        _fail('bars_used must be a list')
    # Reuse the metrics check unchanged. The observation is not available here,
    # so its binding fields are taken from the outcome itself (tautological);
    # the real binding is checked at retention and by aggregate_decision_outcomes.
    identity = document['observation_identity']
    stand_in = {key: document[key] for key in BOUND_FIELDS}
    stand_in['terms'] = {'entry_trigger': document['entry_trigger']}
    try:
        evaluated = _time(document['evaluated_at'], 'evaluated_at')
        _check_outcome(document, {identity: stand_in}, assumptions, evaluated)
    except (ValueError, TypeError) as exc:  # TypeError: e.g. an unhashable forged status
        _fail(str(exc))
    if document['status'] not in TERMINAL_STATUSES:
        _fail('retained outcome is not terminal')


def read_retained_decision_outcome(payload, *, expected_sha256) -> dict:
    """Validate exact retained bytes; return the terminal decision-outcome-v1 record."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        _fail('retained outcome must be bounded bytes')
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        _fail('invalid expected_sha256')
    if sha256(payload).hexdigest() != expected_sha256:
        _fail('retained outcome hash mismatch')
    try:
        document = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail('retained outcome is not strict JSON')
    except ValueError as exc:  # duplicate key or non-finite number from the hooks
        _fail(str(exc))
    if type(document) is not dict:
        _fail('retained outcome is not a JSON object')
    try:
        canonical = canonical_bytes(document)
    except (TypeError, ValueError, RecursionError):
        _fail('retained outcome cannot be re-encoded')
    if canonical != payload:
        _fail('retained outcome is not canonical')
    _check_structure(document)
    return document


@contextmanager
def _translated():
    """Re-raise reused news-event store refusals under this store's error type."""
    try:
        yield
    except NewsEventStoreError as exc:
        raise DecisionOutcomeStoreError(exc.reason, exc.detail) from None


def _key(record):
    return record['decision_id'], canonical_bytes(record['assumptions'])


def _read_record(dir_fd, identity):
    try:
        payload = _read_regular(dir_fd, f'{identity}.json', MAX_BYTES, 'RECORD_UNREADABLE')
    except _Unavailable as exc:
        raise DecisionOutcomeStoreError('STORE_INVALID', exc.reason) from None
    try:
        return payload, read_retained_decision_outcome(payload, expected_sha256=identity)
    except (RetainedDecisionOutcomeError, RecursionError) as exc:
        raise DecisionOutcomeStoreError('STORE_INVALID', str(exc)) from None


def _scan(dir_fd):
    """Every retained record keyed by identity; unexpected entries fail closed."""
    try:
        names = os.listdir(dir_fd)
    except OSError:
        raise DecisionOutcomeStoreError('DIRECTORY_UNAVAILABLE') from None
    identities = []
    for name in names:
        match = _RECORD_NAME.fullmatch(name)
        if match is not None:
            identities.append(match.group(1))
        elif _TEMP_NAME.fullmatch(name) is None:
            raise DecisionOutcomeStoreError('STORE_INVALID', 'unexpected directory entry')
    if len(identities) > MAX_RECORDS:
        raise DecisionOutcomeStoreError('STORE_INVALID', 'too many records')
    records = {identity: _read_record(dir_fd, identity)[1] for identity in sorted(identities)}
    keys = [_key(record) for record in records.values()]
    if len(set(keys)) != len(keys):
        raise DecisionOutcomeStoreError(
            'STORE_INVALID', 'decision evaluated more than once under one assumptions set')
    return records


def load_retained_decision_outcomes(directory, *, market, assumptions) -> tuple[dict, ...]:
    """Retained outcomes of one market under one exact ``OutcomeAssumptions``.

    Ordered by observation identity, ready to pass to
    ``aggregate_decision_outcomes`` with the same assumptions and the caller's
    observation population (which re-checks every binding). Fails closed
    (``DecisionOutcomeStoreError``) if any entry in the directory is damaged or
    unexpected; partial content is never returned. An absent outcome is not a
    result: the aggregate counts it NOT_EVALUATED.
    """
    if market not in ('EGX', 'US'):
        raise ValueError('unknown market')
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    expected = OutcomeAssumptions.model_validate(
        assumptions.model_dump(mode='python')).model_dump(mode='json')
    if expected['market'] != market:
        raise ValueError('assumptions market mismatch')
    with _translated():
        dir_fd = _open_directory(directory)
    try:
        records = _scan(dir_fd)
    finally:
        os.close(dir_fd)
    return tuple(sorted(
        (record for record in records.values()
         if record['market'] == market and record['assumptions'] == expected),
        key=lambda record: record['observation_identity']))


def _aware(now):
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    return now


def _result(status, identity, record, retained_count):
    return {
        'schema_version': RESULT_SCHEMA, 'status': status, 'identity': identity,
        'observation_identity': record['observation_identity'],
        'decision_id': record['decision_id'], 'market': record['market'],
        'strategy_id': record['strategy_id'], 'strategy_version': record['strategy_version'],
        'config_id': record['config_id'],
        'assumptions_version': record['assumptions']['assumptions_version'],
        'outcome_status': record['status'], 'outcome_reason': record['reason'],
        'evaluated_at': record['evaluated_at'], 'retained_count': retained_count,
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED'}


def _without_evaluation(record):
    return {key: value for key, value in record.items() if key not in EVALUATION_FIELDS}


def _accept(observation, outcome, now):
    """Validated (payload, digest, record) of one terminal outcome; no IO."""
    if type(observation) is not tuple or len(observation) != 2 or type(observation[1]) is not dict:
        raise ValueError('(identity, record) observation pair required')
    identity, record = observation
    try:
        record_bytes = canonical_bytes(record)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('observation is not canonical JSON data') from exc
    record = read_retained_decision_observation(record_bytes, expected_sha256=identity)
    if _time(record['recorded_at'], 'recorded_at') > now:
        raise ValueError('observation recorded after the store clock')
    if type(outcome) is not dict:
        raise ValueError('decision-outcome-v1 dict required')
    try:
        payload = canonical_bytes(outcome)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('outcome is not canonical JSON data') from exc
    assumptions = _assumptions(outcome.get('assumptions'))
    if assumptions['market'] != record['market']:
        raise ValueError('assumptions market mismatch')
    _check_outcome(outcome, {identity: record}, assumptions, now)
    if outcome['status'] not in TERMINAL_STATUSES:
        raise _NotFinal()
    digest = sha256(payload).hexdigest()
    return payload, digest, read_retained_decision_outcome(payload, expected_sha256=digest)


def retain_decision_outcome(directory, observation, outcome, *, now=None):
    """Retain one terminal decision outcome write-once in a host-provisioned directory.

    ``observation`` is the retained ``(identity, record)`` pair the outcome was
    evaluated for; ``now`` is the store clock (defaults to real UTC) and the
    outcome must have been evaluated at or before it. Returns a
    ``decision-outcome-retention-v1`` result with status RETAINED or
    ALREADY_RETAINED (first outcome kept). Any refusal raises
    ``DecisionOutcomeStoreError``; refusals before the write leave the
    directory unchanged.
    """
    now = _aware(now)
    try:
        payload, digest, accepted = _accept(observation, outcome, now)
    except _NotFinal:
        raise DecisionOutcomeStoreError('OUTCOME_NOT_FINAL') from None
    except (ValueError, TypeError, RecursionError) as exc:
        raise DecisionOutcomeStoreError('RECORD_REJECTED', type(exc).__name__) from None

    with _translated():
        dir_fd = _open_directory(directory)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DecisionOutcomeStoreError('STORE_BUSY') from None

        retained = _scan(dir_fd)
        if digest in retained:
            return _result('ALREADY_RETAINED', digest, accepted, len(retained))
        for identity, held in retained.items():
            if _key(held) == _key(accepted):
                if _without_evaluation(held) != _without_evaluation(accepted):
                    raise DecisionOutcomeStoreError('OUTCOME_CONFLICT')
                return _result('ALREADY_RETAINED', identity, held, len(retained))
        if len(retained) >= MAX_RECORDS:
            raise DecisionOutcomeStoreError('STORE_FULL')

        try:
            with _translated():
                _write_once(dir_fd, digest, payload)
        except OSError as exc:
            raise DecisionOutcomeStoreError('WRITE_FAILED', type(exc).__name__) from None

        try:
            stored, verified = _read_record(dir_fd, digest)
        except DecisionOutcomeStoreError as exc:
            raise DecisionOutcomeStoreError('RETENTION_UNVERIFIED', exc.detail) from None
        if stored != payload or verified != accepted:
            raise DecisionOutcomeStoreError('RETENTION_UNVERIFIED', 'retained bytes differ')
        return _result('RETAINED', digest, accepted, len(retained) + 1)
    finally:
        os.close(dir_fd)
