"""Bind one retained terminal EGX outcome to the CLEAR corporate-action check it passed.

Phase G/K groundwork (decision-outcome-action-attestation-v1). The optional
gate of the outcome producer (decision-outcome-production-v2) retains a terminal
decision-outcome-v1 record only when the unchanged pure
``check_egx_outcome_corporate_actions`` reports CLEAR, but the retained outcome
does not carry that check: an outcome retained by a checked run and one
retained by an unchecked run are indistinguishable in the outcome store, so the
SYSTEM gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open.

This pure module defines the record that will make them distinguishable
without changing decision-outcome-v1, the outcome store or the aggregate
metrics (whose exact outcome field set is unchanged): a separate attestation
that binds

- one retained terminal outcome, by its content address (the SHA-256 of its
  canonical bytes, re-read through the unchanged outcome-store reader and
  re-bound to its observation with the unchanged metrics check);
- the retained decision-observation-v1 record it was evaluated for (re-read
  from its exact bytes; EGX only);
- the exact CLEAR decision-outcome-corporate-actions-v1 report, structurally
  re-checked against both: same observation, decision, symbol and instrument;
  window DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1 from the record's Cairo
  ``data_as_of`` date through the Cairo date of the outcome's
  ``path_complete_through_at`` (PATH_COMPLETION_DATE_CAIRO_V1, the producer's
  rule); complete egx-actions-v1 coverage spanning that window, published by
  the check's ``as_of``; no action in path; fixed safety labels; check ``as_of``
  not before the outcome's ``evaluated_at`` (the check follows evaluation).

Only CLEAR is attestable: an ACTIONS_IN_PATH path is not comparable with the
plan, and the producer never retains it. The check is not recomputed here (the
evidence document is not embedded; its source and review ingestion ids are, and
review ids stay CALLER_ASSERTED_UNVERIFIED). ``verify_outcome_action_attestation``
rebuilds the record from its embedded check and compares canonical bytes, so a
retained attestation cannot be edited or re-pointed at another outcome.

No IO, store, producer wiring, route or scheduler; nothing calls this module
yet, so the gap stays open. No price is adjusted. Outcomes are hypothetical
results from session bars: not fills, not performance. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from datetime import date
from hashlib import sha256
import json

from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time, read_retained_decision_observation
from app.decision_outcome_corporate_actions import (
    CONTRACT_VERSION as CHECK_CONTRACT_VERSION, EVIDENCE_CONTRACT, MAX_REVIEWS, WINDOW_RULE,
    _uuid_text)
from app.decision_outcome_metrics import _check_outcome
from app.decision_outcome_store import read_retained_decision_outcome
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-action-attestation-v1'
# Same rule as the producer gate (decision_outcome_producer.ACTION_PATH_RULE);
# defined here so the producer can later import this module without a cycle.
PATH_RULE = 'PATH_COMPLETION_DATE_CAIRO_V1'
CHECK_FIELDS = frozenset((
    'contract_version', 'observation_identity', 'decision_id', 'market', 'symbol',
    'instrument_id', 'window', 'evidence', 'as_of', 'status', 'actions_in_path', 'clear_basis',
    'price_adjustment', 'outcome', 'performance_claim', 'mode', 'live_money',
    'execution_allowed', 'promotion'))
WINDOW_FIELDS = frozenset(('rule', 'first_date', 'last_date', 'timezone'))
EVIDENCE_FIELDS = frozenset((
    'contract', 'coverage_start', 'coverage_end', 'published_at', 'complete', 'actions_declared',
    'source_ingestion_id', 'review_ingestion_ids', 'review_verification'))
FIXED_CHECK_VALUES = {
    'contract_version': CHECK_CONTRACT_VERSION, 'market': 'EGX', 'status': 'CLEAR',
    'actions_in_path': [], 'clear_basis': 'COMPLETE_DECLARED_COVERAGE',
    'price_adjustment': 'NOT_APPLIED', 'outcome': 'NOT_EVALUATED', 'performance_claim': 'NONE',
    'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
    'promotion': 'NOT_AUTHORIZED'}
FIXED_EVIDENCE_VALUES = {
    'contract': EVIDENCE_CONTRACT, 'complete': True,
    'review_verification': 'CALLER_ASSERTED_UNVERIFIED'}


def _json(value, name):
    """(canonical bytes, plain JSON copy) of caller data, or ValueError."""
    try:
        payload = canonical_bytes(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f'{name} is not canonical JSON data') from exc
    return payload, json.loads(payload)


def _fixed(document, values, name):
    for key, value in values.items():
        if type(document[key]) is not type(value) or document[key] != value:
            raise ValueError(f'{name} {key} is not {value!r}')


def _date(value, name):
    if type(value) is not str:
        raise ValueError(f'{name} must be an ISO date string')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{name} must be an ISO date string') from exc
    if parsed.isoformat() != value:
        raise ValueError(f'{name} must be an ISO date string')
    return parsed


def _observation(observation):
    if type(observation) is not tuple or len(observation) != 2 or type(observation[1]) is not dict:
        raise ValueError('(identity, record) observation pair required')
    identity, record = observation
    payload, _ = _json(record, 'observation')
    record = read_retained_decision_observation(payload, expected_sha256=identity)
    if record['market'] != 'EGX':
        raise ValueError('EGX observation required')
    return identity, record


def _outcome(outcome_identity, outcome, identity, record):
    if type(outcome) is not dict:
        raise ValueError('decision-outcome-v1 dict required')
    payload, _ = _json(outcome, 'outcome')
    # Content address, terminal status and structure (unchanged store reader).
    outcome = read_retained_decision_outcome(payload, expected_sha256=outcome_identity)
    # Binding to this exact observation (unchanged metrics check).
    _check_outcome(outcome, {identity: record}, outcome['assumptions'],
                   _time(outcome['evaluated_at'], 'evaluated_at'))
    return outcome


def _check(check, identity, record, outcome):
    """Plain JSON copy and canonical bytes of one exact CLEAR report for this path."""
    if type(check) is not dict:
        raise ValueError('decision-outcome-corporate-actions-v1 report dict required')
    payload, check = _json(check, 'corporate-action check')
    if set(check) != CHECK_FIELDS:
        raise ValueError('corporate-action check field set mismatch')
    _fixed(check, FIXED_CHECK_VALUES, 'corporate-action check')
    for key in ('decision_id', 'symbol', 'instrument_id'):
        if check[key] != record[key]:
            raise ValueError(f'corporate-action check {key} does not match the observation')
    if check['observation_identity'] != identity:
        raise ValueError('corporate-action check is for another observation')

    window = check['window']
    if type(window) is not dict or set(window) != WINDOW_FIELDS:
        raise ValueError('corporate-action check window field set mismatch')
    _fixed(window, {'rule': WINDOW_RULE, 'timezone': 'Africa/Cairo'}, 'corporate-action window')
    first = _time(record['data_as_of'], 'data_as_of').astimezone(CAIRO).date()
    last = _time(outcome['path_complete_through_at'],
                 'path_complete_through_at').astimezone(CAIRO).date()
    if _date(window['first_date'], 'window first_date') != first:
        raise ValueError('corporate-action window does not start on the data_as_of date')
    # PATH_COMPLETION_DATE_CAIRO_V1: the check must cover this outcome's whole path.
    if _date(window['last_date'], 'window last_date') != last:
        raise ValueError('corporate-action window does not end on the path completion date')

    as_of = _time(check['as_of'], 'check as_of')
    if as_of < _time(outcome['evaluated_at'], 'evaluated_at'):
        raise ValueError('corporate-action check before outcome evaluation')

    evidence = check['evidence']
    if type(evidence) is not dict or set(evidence) != EVIDENCE_FIELDS:
        raise ValueError('corporate-action evidence field set mismatch')
    _fixed(evidence, FIXED_EVIDENCE_VALUES, 'corporate-action evidence')
    # Absence of an action is evidence only inside complete declared coverage.
    if (_date(evidence['coverage_start'], 'coverage_start') > first
            or _date(evidence['coverage_end'], 'coverage_end') < last):
        raise ValueError('action coverage does not span the outcome window')
    if _time(evidence['published_at'], 'published_at') > as_of:
        raise ValueError('action evidence published after the check as_of')
    declared = evidence['actions_declared']
    if type(declared) is not int or declared < 0:
        raise ValueError('actions_declared must be a non-negative integer')
    source = _uuid_text(evidence['source_ingestion_id'], 'source_ingestion_id')
    reviews = evidence['review_ingestion_ids']
    if type(reviews) is not list or not 0 < len(reviews) <= MAX_REVIEWS:
        raise ValueError(f'list of 1..{MAX_REVIEWS} review ingestion ids required')
    reviews = [_uuid_text(item, 'review_ingestion_id') for item in reviews]
    if reviews != sorted(set(reviews)) or source in reviews:
        raise ValueError('review ingestion ids must be sorted, unique and distinct from the source')
    return payload, check, as_of


def attest_outcome_corporate_actions(outcome_identity, outcome, observation, check) -> dict:
    """Return the decision-outcome-action-attestation-v1 record; pure, fails closed.

    ``outcome_identity`` is the SHA-256 of the retained outcome's canonical
    bytes, ``outcome`` that terminal decision-outcome-v1 record, ``observation``
    the retained ``(identity, record)`` pair it was evaluated for and ``check``
    the exact CLEAR decision-outcome-corporate-actions-v1 report for its path.
    Raises ``ValueError`` on any mismatch; nothing is adjusted or retained.
    """
    identity, record = _observation(observation)
    outcome = _outcome(outcome_identity, outcome, identity, record)
    payload, check, as_of = _check(check, identity, record, outcome)
    evaluated = _time(outcome['evaluated_at'], 'evaluated_at')
    return {
        'contract_version': CONTRACT_VERSION,
        'outcome_identity': outcome_identity,
        'observation_identity': identity,
        'decision_id': record['decision_id'],
        'market': 'EGX',
        'symbol': record['symbol'],
        'instrument_id': record['instrument_id'],
        'strategy_id': record['strategy_id'],
        'strategy_version': record['strategy_version'],
        'config_id': record['config_id'],
        'assumptions_version': outcome['assumptions']['assumptions_version'],
        'outcome_status': outcome['status'],
        'outcome_evaluated_at': outcome['evaluated_at'],
        'path_complete_through_at': outcome['path_complete_through_at'],
        'path_rule': PATH_RULE,
        'window_rule': WINDOW_RULE,
        'corporate_action_status': 'CLEAR',
        'check_as_of': check['as_of'],
        'check_timing': 'AT_EVALUATION' if as_of == evaluated else 'AFTER_EVALUATION',
        'check': check,
        'check_sha256': sha256(payload).hexdigest(),
        # The evidence document is not embedded, so the check is not re-run here.
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }


def verify_outcome_action_attestation(attestation, outcome_identity, outcome,
                                      observation) -> dict:
    """Return the attestation if it is exactly the one this outcome and its check yield.

    Rebuilds the record with ``attest_outcome_corporate_actions`` from the
    embedded check and compares canonical bytes (so ``True`` cannot stand in for
    ``1`` and no field can be added, dropped or edited). Raises ``ValueError``.
    """
    if type(attestation) is not dict:
        raise ValueError('decision-outcome-action-attestation-v1 dict required')
    payload, _ = _json(attestation, 'attestation')
    rebuilt = attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, attestation.get('check'))
    if canonical_bytes(rebuilt) != payload:
        raise ValueError('attestation does not match its outcome, observation and check')
    return rebuilt
