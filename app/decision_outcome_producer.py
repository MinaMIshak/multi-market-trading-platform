"""EGX decision-outcome producer over retained observations and daily evidence.

Phase G/H collection step (decision-outcome-production-v1). For every EGX
decision-observation-v1 record in a host-provisioned observation store it
composes the existing pieces unchanged; it adds no binding, evaluation, record
or store logic:

1. ``load_retained_decision_observations`` (fails closed on any damaged entry);
2. ``load_retained_decision_outcomes`` under ONE exact ``OutcomeAssumptions``:
   an observation that already has a retained terminal outcome is reported
   ALREADY_RETAINED and is not re-evaluated (revision detection for retained
   outcomes is NOT_PERFORMED here; the store's OUTCOME_CONFLICT rule still
   guards any other writer);
3. ``bind_egx_outcome_session_bars`` with the caller's retained daily rows of
   that instrument, the caller's receipt time of those exact bytes and the
   retained session window trimmed by CALENDAR_THROUGH_RECEIPT_DATE_V1 (Cairo
   dates from the detection date through the receipt date, so sessions after
   the receipt are never offered to the path);
4. ``evaluate_decision_outcome`` with ``evaluated_at`` = the producer clock;
5. ``retain_decision_outcome`` for terminal outcomes (CLOSED, NO_ENTRY,
   UNEVALUABLE) with the same clock. PENDING_PATH is reported, never retained.

Per-observation results are visible, never dropped: AWAITING_DAILY_EVIDENCE
(no rows supplied for the instrument, or rows received before detection),
OBSERVATION_AFTER_CLOCK, BINDING_REJECTED and EVALUATION_REJECTED carry the
fixed reason or the fail-closed message; such decisions get no outcome and the
unchanged aggregate keeps counting them NOT_EVALUATED, so a gap can never
improve a segment. A store refusal aborts the run (RETENTION_FAILED); outcomes
retained earlier in the run stay (write-once, individually valid) and a rerun
is idempotent.

The daily rows, receipt times and sessions are caller-supplied retained
evidence (no database or network IO here); the receipt clock is
CALLER_ASSERTED_UNVERIFIED, calendar admission NOT_ESTABLISHED and corporate
actions NOT_CHECKED, so no outcome or metric derived from this run is
empirical evidence until those gaps close. Its only caller is the host job
entrypoint ``app.decision_outcome_job`` (not scheduled); no route; both store
directories are explicit arguments. Outcomes are
hypothetical results from session bars: not fills, not performance.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os

from app.decision_observation import canonical_bytes
from app.decision_observation_store import (
    DecisionObservationStoreError, _time, load_retained_decision_observations)
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_session_bars import bind_egx_outcome_session_bars
from app.decision_outcome_store import (
    TERMINAL_STATUSES, DecisionOutcomeStoreError, load_retained_decision_outcomes,
    retain_decision_outcome)
from app.domain.models import MarketSession
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-production-v1'
CALENDAR_RULE = 'CALENDAR_THROUGH_RECEIPT_DATE_V1'
PRODUCTION_REASONS = (
    'CONFIG_INVALID', 'OBSERVATIONS_UNAVAILABLE', 'OUTCOMES_UNAVAILABLE', 'RETENTION_FAILED')
ENTRY_STATUSES = (
    'RETAINED', 'ALREADY_RETAINED', 'PENDING_PATH', 'AWAITING_DAILY_EVIDENCE',
    'OBSERVATION_AFTER_CLOCK', 'BINDING_REJECTED', 'EVALUATION_REJECTED')
AWAITING_REASONS = ('NO_DAILY_EVIDENCE', 'RECEIVED_BEFORE_DETECTION')
MAX_DETAIL = 200


class DecisionOutcomeProductionError(RuntimeError):
    """Production refused or failed; ``reason`` is one of PRODUCTION_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class EgxDailyEvidence:
    """Retained canonical daily rows of one instrument and the receipt time of their bytes."""

    rows: tuple
    received_at: datetime


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _aware(value):
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None)


def _directory(value):
    if type(value) is not str or not os.path.isabs(value) or '\x00' in value:
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'absolute directory required')
    return os.path.normpath(value)


def _config(observation_directory, outcome_directory, daily, sessions, assumptions, now):
    if _directory(observation_directory) == _directory(outcome_directory):
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'stores must be distinct')
    if type(assumptions) is not OutcomeAssumptions:
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'exact OutcomeAssumptions required')
    try:
        assumptions = OutcomeAssumptions.model_validate(assumptions.model_dump(mode='python'))
    except ValueError:
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'invalid assumptions') from None
    if assumptions.market != 'EGX':
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'EGX assumptions required')
    if type(sessions) is not tuple or any(type(item) is not MarketSession for item in sessions):
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'tuple of exact MarketSession required')
    if type(daily) is not dict:
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'daily evidence mapping required')
    for key, evidence in daily.items():
        if type(key) is not str or type(evidence) is not EgxDailyEvidence:
            raise DecisionOutcomeProductionError(
                'CONFIG_INVALID', 'instrument id -> EgxDailyEvidence required')
        if not _aware(evidence.received_at):
            raise DecisionOutcomeProductionError('CONFIG_INVALID', 'timezone-aware received_at required')
        # A receipt after the producer clock would let bytes not yet received shape an outcome.
        if evidence.received_at > now:
            raise DecisionOutcomeProductionError('CONFIG_INVALID', 'daily evidence received after clock')
    return assumptions


def _detail(exc):
    return str(exc)[:MAX_DETAIL]


def _entry(identity, record):
    return {
        'observation_identity': identity, 'decision_id': record['decision_id'],
        'symbol': record['symbol'], 'instrument_id': record['instrument_id'],
        'strategy_id': record['strategy_id'], 'strategy_version': record['strategy_version'],
        'config_id': record['config_id'], 'detected_at': record['detected_at'],
        'status': None, 'reason': None, 'detail': None,
        'outcome_identity': None, 'outcome_status': None, 'outcome_reason': None,
        'path_complete_through_at': None, 'sessions_bound': None,
    }


def _window(sessions, detected, received):
    """CALENDAR_THROUGH_RECEIPT_DATE_V1: Cairo detection date through receipt date."""
    first, last = detected.astimezone(CAIRO).date(), received.astimezone(CAIRO).date()
    return tuple(item for item in sessions if first <= item.market_date <= last)


def _produce_one(entry, identity, record, outcome_directory, daily, sessions, assumptions, now):
    if _time(record['recorded_at'], 'recorded_at') > now:
        entry['status'] = 'OBSERVATION_AFTER_CLOCK'
        return
    evidence = daily.get(record['instrument_id'])
    if evidence is None:
        entry.update(status='AWAITING_DAILY_EVIDENCE', reason='NO_DAILY_EVIDENCE')
        return
    detected = _time(record['detected_at'], 'detected_at')
    if evidence.received_at < detected:
        entry.update(status='AWAITING_DAILY_EVIDENCE', reason='RECEIVED_BEFORE_DETECTION')
        return
    try:
        bound = bind_egx_outcome_session_bars(
            identity, record, evidence.rows,
            _window(sessions, detected, evidence.received_at),
            received_at=evidence.received_at)
    except ValueError as exc:
        entry.update(status='BINDING_REJECTED', detail=_detail(exc))
        return
    entry.update(path_complete_through_at=bound['report']['path_complete_through_at'],
                 sessions_bound=list(bound['report']['sessions_bound']))
    try:
        outcome = evaluate_decision_outcome(
            identity, record, bound['bars'], assumptions=assumptions,
            path_complete_through_at=bound['path_complete_through_at'], evaluated_at=now)
    except ValueError as exc:
        entry.update(status='EVALUATION_REJECTED', detail=_detail(exc))
        return
    entry.update(outcome_status=outcome['status'], outcome_reason=outcome['reason'])
    if outcome['status'] not in TERMINAL_STATUSES:
        entry['status'] = 'PENDING_PATH'
        return
    try:
        retained = retain_decision_outcome(outcome_directory, (identity, record), outcome, now=now)
    except DecisionOutcomeStoreError as exc:
        raise DecisionOutcomeProductionError(
            'RETENTION_FAILED', f"{record['symbol']}: {exc.reason}") from None
    if (retained.get('observation_identity') != identity
            or retained.get('status') not in ('RETAINED', 'ALREADY_RETAINED')):
        raise DecisionOutcomeProductionError('RETENTION_FAILED', 'unexpected result')
    entry.update(status=retained['status'], outcome_identity=retained['identity'])


def produce_egx_decision_outcomes(observation_directory, outcome_directory, *, daily, sessions,
                                  assumptions, now=None):
    """Evaluate and retain terminal outcomes for every retained EGX observation.

    ``daily`` maps canonical instrument UUID strings to ``EgxDailyEvidence``;
    ``sessions`` is a tuple of retained ``MarketSession`` rows (Africa/Cairo);
    ``now`` is the producer clock (defaults to real UTC), used as both
    ``evaluated_at`` and the outcome-store clock. Returns a
    ``decision-outcome-production-v1`` result with one entry per observation.
    Refusals raise ``DecisionOutcomeProductionError``.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if not _aware(now):
        raise TypeError('now must be a timezone-aware datetime')
    assumptions = _config(observation_directory, outcome_directory, daily, sessions,
                          assumptions, now)
    try:
        observations = load_retained_decision_observations(observation_directory, market='EGX')
    except DecisionObservationStoreError as exc:
        raise DecisionOutcomeProductionError('OBSERVATIONS_UNAVAILABLE', exc.reason) from None
    try:
        held = load_retained_decision_outcomes(outcome_directory, market='EGX',
                                               assumptions=assumptions)
    except DecisionOutcomeStoreError as exc:
        raise DecisionOutcomeProductionError('OUTCOMES_UNAVAILABLE', exc.reason) from None
    held = {record['observation_identity']: record for record in held}
    population = {identity for identity, _ in observations}

    entries = []
    for identity, record in observations:
        entry = _entry(identity, record)
        if identity in held:
            outcome = held[identity]
            entry.update(status='ALREADY_RETAINED',
                         outcome_identity=sha256(canonical_bytes(outcome)).hexdigest(),
                         outcome_status=outcome['status'], outcome_reason=outcome['reason'],
                         path_complete_through_at=outcome['path_complete_through_at'])
        else:
            _produce_one(entry, identity, record, outcome_directory, daily, sessions,
                         assumptions, now)
        entries.append(entry)

    return {
        'schema_version': CONTRACT_VERSION, 'market': 'EGX',
        'produced_at': _utc(now), 'produced_at_clock': 'PRODUCER_CLOCK_UNVERIFIED',
        'assumptions': assumptions.model_dump(mode='json'),
        'observations': len(observations),
        'outcomes': entries,
        'status_counts': {s: sum(e['status'] == s for e in entries) for s in ENTRY_STATUSES},
        # Retained outcomes whose observation is absent from the observation store.
        'retained_outcomes_without_observation': sum(i not in population for i in held),
        'calendar_rule': CALENDAR_RULE,
        'retained_outcome_reevaluation': 'NOT_PERFORMED',
        'daily_receipt_clock': 'CALLER_ASSERTED_UNVERIFIED',
        'calendar_admission': 'NOT_ESTABLISHED',
        'corporate_actions': 'NOT_CHECKED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
