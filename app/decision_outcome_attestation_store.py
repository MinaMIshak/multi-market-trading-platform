"""Write-once sidecar store for decision-outcome-action-attestation-v1 records.

Phase G/K groundwork. The pure ``app.decision_outcome_action_attestation``
binds one retained terminal decision-outcome-v1 record (by its content address)
to the exact CLEAR corporate-action check it passed. This module retains those
attestations so a later reader can tell checked outcomes from unchecked ones
without changing decision-outcome-v1, the outcome store or the aggregate
metrics. It is a SIDECAR: a separate host-provisioned directory beside the
outcome store, never the outcome store itself (an outcome record in this
directory, or an attestation in the outcome directory, fails closed on scan).

One attestation per retained outcome identity. Retention, under an exclusive
non-blocking lock:

1. re-derives the attestation with the unchanged
   ``verify_outcome_action_attestation`` from the caller's retained outcome
   ``(identity, record)`` and observation ``(identity, record)`` pair (outcome
   content address, observation binding, exact CLEAR check over the whole path,
   canonical bytes) and refuses a check ``as_of`` after the store clock, before
   any write;
2. reads every retained attestation (damaged or unexpected entries, or two
   attestations for one outcome, fail closed);
3. keeps the first attestation of an outcome. A retry that differs only in the
   check time (``check_as_of``, ``check_timing``, the embedded check's
   ``as_of`` and therefore ``check_sha256``) returns ALREADY_RETAINED with the
   first record; any other difference (other coverage, evidence, reviews or
   window) is ATTESTATION_CONFLICT, visible instead of silently replaced.
   Then it writes once (temporary file, fsync, hard link without overwrite),
   fsyncs the directory and re-reads the bytes.

The reader alone sees neither the outcome nor the observation: it checks the
record's structure, fixed safety labels and the embedded check's digest only.
A loaded attestation is NOT proof that an outcome was checked until the caller
re-verifies it against the retained outcome and observation with
``verify_outcome_action_attestation`` (the next unit's loader). Absence of an
attestation means the outcome is unattested, never that it is clear. A digest
is integrity under a trusted host writer, not a signature.

Layout and helpers are those of ``app.decision_outcome_store`` /
``app.news_event_store``: one ``<sha256>.json`` file (0444) per record holding
the canonical bytes. Nothing is replaced or deleted. No price is adjusted; the
check is not re-run (its evidence document is not embedded). No caller,
producer, job, provider, scheduler or route; the SYSTEM gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open. Outcomes are hypothetical
results from session bars: not fills, not performance. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
import re

from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome_action_attestation import (
    CHECK_FIELDS, CONTRACT_VERSION as ATTESTATION_CONTRACT, FIXED_CHECK_VALUES, PATH_RULE,
    _fixed, verify_outcome_action_attestation)
from app.decision_outcome_corporate_actions import WINDOW_RULE
from app.decision_outcome_store import TERMINAL_STATUSES
from app.news_event_record import MAX_BYTES, _constant, _pairs
from app.news_event_store import (
    _RECORD_NAME, _TEMP_NAME, NewsEventStoreError, _open_directory, _write_once)
from app.scanner_v2_card_report_loader import _read_regular, _Unavailable

RESULT_SCHEMA = 'decision-outcome-attestation-retention-v1'
MAX_RECORDS = 10000
ATTESTATION_FIELDS = frozenset((
    'contract_version', 'outcome_identity', 'observation_identity', 'decision_id', 'market',
    'symbol', 'instrument_id', 'strategy_id', 'strategy_version', 'config_id',
    'assumptions_version', 'outcome_status', 'outcome_evaluated_at', 'path_complete_through_at',
    'path_rule', 'window_rule', 'corporate_action_status', 'check_as_of', 'check_timing', 'check',
    'check_sha256', 'check_recomputation', 'review_verification', 'price_adjustment', 'fill_kind',
    'execution_evidence', 'performance_claim', 'mode', 'live_money', 'execution_allowed',
    'runtime_activation', 'promotion'))
FIXED_VALUES = {
    'contract_version': ATTESTATION_CONTRACT, 'market': 'EGX', 'path_rule': PATH_RULE,
    'window_rule': WINDOW_RULE, 'corporate_action_status': 'CLEAR',
    'check_recomputation': 'NOT_PERFORMED', 'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
    'price_adjustment': 'NOT_APPLIED', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
    'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
    'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
    'promotion': 'NOT_AUTHORIZED'}
# Fields that may differ between two attestations of one outcome without
# changing what is attested (only the time the same check was made).
CHECK_TIME_FIELDS = ('check_as_of', 'check_timing', 'check_sha256')
STORE_REASONS = (
    'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'DIRECTORY_UNSAFE', 'STORE_BUSY',
    'RECORD_REJECTED', 'STORE_INVALID', 'STORE_FULL', 'ATTESTATION_CONFLICT',
    'CONTENT_CONFLICT', 'WRITE_FAILED', 'RETENTION_UNVERIFIED')
_TEXT_FIELDS = ('symbol', 'instrument_id', 'strategy_id', 'strategy_version', 'config_id',
                'assumptions_version')
_HEX64 = re.compile(r'[0-9a-f]{64}')


class RetainedOutcomeAttestationError(ValueError):
    """Retained attestation bytes failed closed; nothing from them may be used."""


class OutcomeAttestationStoreError(RuntimeError):
    """Retention or load refused; ``reason`` is one of STORE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _fail(reason):
    raise RetainedOutcomeAttestationError(reason)


def _structure(document):
    """Structure of one attestation, without its outcome or observation; ValueError."""
    if set(document) != ATTESTATION_FIELDS:
        raise ValueError('attestation field set mismatch')
    _fixed(document, FIXED_VALUES, 'attestation')
    for key in ('outcome_identity', 'observation_identity', 'decision_id', 'check_sha256'):
        if type(document[key]) is not str or _HEX64.fullmatch(document[key]) is None:
            raise ValueError(f'{key} must be a SHA-256 hex digest')
    for key in _TEXT_FIELDS:
        if type(document[key]) is not str or not document[key]:
            raise ValueError(f'{key} must be a non-empty string')
    if type(document['outcome_status']) is not str or (
            document['outcome_status'] not in TERMINAL_STATUSES):
        raise ValueError('attested outcome is not terminal')
    evaluated = _time(document['outcome_evaluated_at'], 'outcome_evaluated_at')
    _time(document['path_complete_through_at'], 'path_complete_through_at')
    as_of = _time(document['check_as_of'], 'check_as_of')
    if as_of < evaluated:
        raise ValueError('corporate-action check before outcome evaluation')
    if document['check_timing'] != ('AT_EVALUATION' if as_of == evaluated else 'AFTER_EVALUATION'):
        raise ValueError('check_timing does not match the check and evaluation times')
    check = document['check']
    if type(check) is not dict or set(check) != CHECK_FIELDS:
        raise ValueError('embedded check field set mismatch')
    _fixed(check, FIXED_CHECK_VALUES, 'embedded check')
    if sha256(canonical_bytes(check)).hexdigest() != document['check_sha256']:
        raise ValueError('embedded check digest mismatch')
    if check['as_of'] != document['check_as_of']:
        raise ValueError('embedded check as_of mismatch')
    if check['observation_identity'] != document['observation_identity']:
        raise ValueError('embedded check is for another observation')
    for key in ('decision_id', 'symbol', 'instrument_id'):
        if check[key] != document[key]:
            raise ValueError(f'embedded check {key} mismatch')


def read_retained_outcome_attestation(payload, *, expected_sha256) -> dict:
    """Validate exact retained bytes; return the attestation record (structure only).

    Does not prove the outcome was checked: the caller must re-verify the
    record against the retained outcome and observation with
    ``verify_outcome_action_attestation``.
    """
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        _fail('retained attestation must be bounded bytes')
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        _fail('invalid expected_sha256')
    if sha256(payload).hexdigest() != expected_sha256:
        _fail('retained attestation hash mismatch')
    try:
        document = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail('retained attestation is not strict JSON')
    except ValueError as exc:  # duplicate key or non-finite number from the hooks
        _fail(str(exc))
    if type(document) is not dict:
        _fail('retained attestation is not a JSON object')
    try:
        canonical = canonical_bytes(document)
    except (TypeError, ValueError, RecursionError):
        _fail('retained attestation cannot be re-encoded')
    if canonical != payload:
        _fail('retained attestation is not canonical')
    try:
        _structure(document)
    except (ValueError, TypeError) as exc:
        _fail(str(exc))
    return document


@contextmanager
def _translated():
    """Re-raise reused news-event store refusals under this store's error type."""
    try:
        yield
    except NewsEventStoreError as exc:
        raise OutcomeAttestationStoreError(exc.reason, exc.detail) from None


def _read_record(dir_fd, identity):
    try:
        payload = _read_regular(dir_fd, f'{identity}.json', MAX_BYTES, 'RECORD_UNREADABLE')
    except _Unavailable as exc:
        raise OutcomeAttestationStoreError('STORE_INVALID', exc.reason) from None
    try:
        return payload, read_retained_outcome_attestation(payload, expected_sha256=identity)
    except (RetainedOutcomeAttestationError, RecursionError) as exc:
        raise OutcomeAttestationStoreError('STORE_INVALID', str(exc)) from None


def _scan(dir_fd):
    """Every retained attestation keyed by identity; unexpected entries fail closed."""
    try:
        names = os.listdir(dir_fd)
    except OSError:
        raise OutcomeAttestationStoreError('DIRECTORY_UNAVAILABLE') from None
    identities = []
    for name in names:
        match = _RECORD_NAME.fullmatch(name)
        if match is not None:
            identities.append(match.group(1))
        elif _TEMP_NAME.fullmatch(name) is None:
            raise OutcomeAttestationStoreError('STORE_INVALID', 'unexpected directory entry')
    if len(identities) > MAX_RECORDS:
        raise OutcomeAttestationStoreError('STORE_INVALID', 'too many records')
    records = {identity: _read_record(dir_fd, identity)[1] for identity in sorted(identities)}
    outcomes = [record['outcome_identity'] for record in records.values()]
    if len(set(outcomes)) != len(outcomes):
        raise OutcomeAttestationStoreError('STORE_INVALID', 'outcome attested more than once')
    return records


def load_retained_outcome_attestations(directory) -> tuple[dict, ...]:
    """Every retained EGX attestation, ordered by outcome identity (structure only).

    Fails closed (``OutcomeAttestationStoreError``) if any entry in the
    directory is damaged or unexpected; partial content is never returned. The
    records are not yet evidence: each must be re-verified against its retained
    outcome and observation with ``verify_outcome_action_attestation``. An
    outcome without an attestation is unattested, not clear.
    """
    with _translated():
        dir_fd = _open_directory(directory)
    try:
        records = _scan(dir_fd)
    finally:
        os.close(dir_fd)
    return tuple(sorted(records.values(), key=lambda record: record['outcome_identity']))


def _aware(now):
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    return now


def _result(status, identity, record, retained_count):
    return {
        'schema_version': RESULT_SCHEMA, 'status': status, 'identity': identity,
        'outcome_identity': record['outcome_identity'],
        'observation_identity': record['observation_identity'],
        'decision_id': record['decision_id'], 'market': record['market'],
        'strategy_id': record['strategy_id'], 'strategy_version': record['strategy_version'],
        'config_id': record['config_id'], 'assumptions_version': record['assumptions_version'],
        'outcome_status': record['outcome_status'],
        'corporate_action_status': record['corporate_action_status'],
        'check_as_of': record['check_as_of'], 'check_timing': record['check_timing'],
        'retained_count': retained_count,
        'store_clock': 'CALLER_SUPPLIED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
        'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED'}


def _without_check_time(record):
    kept = {key: value for key, value in record.items() if key not in CHECK_TIME_FIELDS}
    kept['check'] = {key: value for key, value in record['check'].items() if key != 'as_of'}
    return kept


def _accept(attestation, outcome, observation, now):
    """Validated (payload, digest, record) of one attestation; no IO."""
    if type(outcome) is not tuple or len(outcome) != 2:
        raise ValueError('(identity, record) outcome pair required')
    outcome_identity, outcome_record = outcome
    verified = verify_outcome_action_attestation(
        attestation, outcome_identity, outcome_record, observation)
    if _time(verified['check_as_of'], 'check_as_of') > now:
        raise ValueError('corporate-action check after the store clock')
    payload = canonical_bytes(verified)
    digest = sha256(payload).hexdigest()
    return payload, digest, read_retained_outcome_attestation(payload, expected_sha256=digest)


def retain_outcome_action_attestation(directory, attestation, outcome, observation, *, now=None):
    """Retain one verified outcome attestation write-once in a host-provisioned directory.

    ``outcome`` is the retained ``(identity, record)`` terminal outcome pair and
    ``observation`` the retained ``(identity, record)`` observation pair it was
    evaluated for; ``now`` is the store clock (defaults to real UTC) and the
    embedded check must have been made at or before it. Returns a
    ``decision-outcome-attestation-retention-v1`` result with status RETAINED
    or ALREADY_RETAINED (first attestation kept). Any refusal raises
    ``OutcomeAttestationStoreError``; refusals before the write leave the
    directory unchanged.
    """
    now = _aware(now)
    try:
        payload, digest, accepted = _accept(attestation, outcome, observation, now)
    except (ValueError, TypeError, RecursionError) as exc:
        raise OutcomeAttestationStoreError('RECORD_REJECTED', type(exc).__name__) from None

    with _translated():
        dir_fd = _open_directory(directory)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OutcomeAttestationStoreError('STORE_BUSY') from None

        retained = _scan(dir_fd)
        if digest in retained:
            return _result('ALREADY_RETAINED', digest, accepted, len(retained))
        for identity, held in retained.items():
            if held['outcome_identity'] == accepted['outcome_identity']:
                if _without_check_time(held) != _without_check_time(accepted):
                    raise OutcomeAttestationStoreError('ATTESTATION_CONFLICT')
                return _result('ALREADY_RETAINED', identity, held, len(retained))
        if len(retained) >= MAX_RECORDS:
            raise OutcomeAttestationStoreError('STORE_FULL')

        try:
            with _translated():
                _write_once(dir_fd, digest, payload)
        except OSError as exc:
            raise OutcomeAttestationStoreError('WRITE_FAILED', type(exc).__name__) from None

        try:
            stored, verified = _read_record(dir_fd, digest)
        except OutcomeAttestationStoreError as exc:
            raise OutcomeAttestationStoreError('RETENTION_UNVERIFIED', exc.detail) from None
        if stored != payload or verified != accepted:
            raise OutcomeAttestationStoreError('RETENTION_UNVERIFIED', 'retained bytes differ')
        return _result('RETAINED', digest, accepted, len(retained) + 1)
    finally:
        os.close(dir_fd)
