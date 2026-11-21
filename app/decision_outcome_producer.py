"""EGX decision-outcome producer over retained observations and daily evidence.

Phase G/H collection step (decision-outcome-production-v4). For every EGX
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
5. only when the caller supplies ``actions`` (observation identity ->
   ``OutcomeActionSelection``, the exact ``ReferenceRepository.actions``
   selection returned by ``app.decision_outcome_action_evidence``): for a
   terminal outcome, the unchanged pure
   ``check_egx_outcome_corporate_actions`` with ``path_through`` = the Africa/
   Cairo date of the binding's ``path_complete_through_at`` (the calendar's
   final date, PATH_COMPLETION_DATE_CAIRO_V1) and ``as_of`` = the producer
   clock. Only CLEAR is retained; ACTIONS_IN_PATH (with the full check report),
   AWAITING_ACTION_EVIDENCE (no selection for that observation) and
   ACTION_CHECK_REJECTED (fail-closed message) are reported per observation and
   never retained, so they stay NOT_EVALUATED in the unchanged aggregate. No
   price is adjusted. Without ``actions`` the v1 behaviour is unchanged and the
   run is labelled corporate actions NOT_CHECKED;
6. ``retain_decision_outcome`` for terminal outcomes (CLOSED, NO_ENTRY,
   UNEVALUABLE) with the same clock. PENDING_PATH is reported, never retained
   (and not checked);
7. only when the caller also supplies ``attestation_directory`` (requires
   ``actions``; a host-provisioned sidecar directory distinct from both
   stores): for an outcome this run retained (store status RETAINED, or
   ALREADY_RETAINED with exactly this run's bytes) after a CLEAR check, the
   unchanged pure ``attest_outcome_corporate_actions`` binds that retained
   outcome, its observation and the exact CLEAR report, and the unchanged
   ``retain_outcome_action_attestation`` retains it write-once with the same
   clock. The sidecar is loaded (structure only) before any outcome is
   evaluated, so a damaged or missing sidecar refuses the run before any write
   (ATTESTATIONS_UNAVAILABLE). An attestation the pure builder refuses is
   reported per observation (ATTESTATION_REJECTED) and the outcome stays
   unattested; a sidecar store refusal aborts the run (ATTESTATION_FAILED) with
   earlier outcomes and attestations kept (write-once);
8. only when the caller also passes ``attest_held_outcomes=True`` (requires
   ``attestation_directory``; HELD_RECHECK_AFTER_EVALUATION_V1): for an outcome
   already in the store when the run starts, the unchanged check runs over that
   held outcome's own retained path (Africa/Cairo date of its
   ``path_complete_through_at``) with ``as_of`` = the producer clock, and only a
   CLEAR report is attested by step 7's unchanged builder and store (so its
   ``check_timing`` is AFTER_EVALUATION when the clock is later than the held
   evaluation; a clock before it is refused by the check or the builder,
   HELD_CHECK_REJECTED / ATTESTATION_REJECTED). No selection (HELD_AWAITING_ACTION_EVIDENCE), an
   action in the path (HELD_ACTIONS_IN_PATH, full report kept) or a fail-closed
   check (HELD_CHECK_REJECTED) leaves the held outcome UNATTESTED. A held
   outcome is never re-evaluated, re-retained, replaced or price-adjusted; its
   entry status stays ALREADY_RETAINED.

v2 added only the optional check (and its result fields/statuses); v3 added
only the optional attestation step and its result fields; v4
(decision-outcome-production-v4) adds only the opt-in held-outcome re-check
(``held_outcome_attestation``, ``held_outcomes_rechecked`` and the HELD_*
attestation statuses). The retained decision-outcome-v1 record still does not
carry the check (``retained_outcome_corporate_actions`` NOT_RECORDED_IN_OUTCOME):
only a verified sidecar attestation tells a checked outcome apart. Without
``attest_held_outcomes`` outcomes already in the store when the run starts
(ALREADY_RETAINED) are neither re-checked nor attested
(``held_outcome_attestation`` NOT_PERFORMED), so an outcome retained by an
unchecked run, or whose attestation failed, stays UNATTESTED (never clear) for
the partition. The host job passes ``attestation_directory`` only with its
opt-in ``--attestation-dir`` (decision-outcome-job-v4) and passes
``attest_held_outcomes=True`` only with its opt-in ``--attest-held-outcomes``
on top of that (decision-outcome-job-v5, which then also requests selections
for held outcomes through their own path dates), and nothing reads
attestations for metrics yet, so the SYSTEM gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open.

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
CALLER_ASSERTED_UNVERIFIED and calendar admission NOT_ESTABLISHED, and the
corporate-action check is unrecorded in retained outcomes, so no outcome or
metric derived from this run is empirical evidence until those gaps close. Its
only caller is the host job entrypoint ``app.decision_outcome_job`` (not
scheduled; decision-outcome-job-v5 passes ``actions`` only with its opt-in
``--check-corporate-actions``, ``attestation_directory`` only with its
opt-in ``--attestation-dir`` on top of that, and ``attest_held_outcomes=True``
only with its opt-in ``--attest-held-outcomes`` on top of both); no route;
every store directory is an explicit argument. Outcomes are
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
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_action_evidence import OutcomeActionSelection
from app.decision_outcome_attestation_store import (
    OutcomeAttestationStoreError, load_retained_outcome_attestations,
    retain_outcome_action_attestation)
from app.decision_outcome_corporate_actions import (
    WINDOW_RULE as ACTION_WINDOW_RULE, check_egx_outcome_corporate_actions)
from app.decision_outcome_session_bars import bind_egx_outcome_session_bars
from app.decision_outcome_store import (
    TERMINAL_STATUSES, DecisionOutcomeStoreError, load_retained_decision_outcomes,
    retain_decision_outcome)
from app.domain.models import MarketSession
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-production-v4'
CALENDAR_RULE = 'CALENDAR_THROUGH_RECEIPT_DATE_V1'
ACTION_PATH_RULE = 'PATH_COMPLETION_DATE_CAIRO_V1'
# decision-outcome-production-v4: opt-in re-check of outcomes held before the run.
HELD_RULE = 'HELD_RECHECK_AFTER_EVALUATION_V1'
PRODUCTION_REASONS = (
    'CONFIG_INVALID', 'OBSERVATIONS_UNAVAILABLE', 'OUTCOMES_UNAVAILABLE', 'RETENTION_FAILED',
    'ATTESTATIONS_UNAVAILABLE', 'ATTESTATION_FAILED')
# Per-entry attestation result (None when no attestation was attempted). The
# HELD_* values occur only with attest_held_outcomes and leave the held outcome
# UNATTESTED (never clear).
ATTESTATION_STATUSES = (
    'RETAINED', 'ALREADY_RETAINED', 'ATTESTATION_REJECTED', 'RETAINED_OUTCOME_DIFFERS',
    'HELD_AWAITING_ACTION_EVIDENCE', 'HELD_ACTIONS_IN_PATH', 'HELD_CHECK_REJECTED')
ENTRY_STATUSES = (
    'RETAINED', 'ALREADY_RETAINED', 'PENDING_PATH', 'AWAITING_DAILY_EVIDENCE',
    'OBSERVATION_AFTER_CLOCK', 'BINDING_REJECTED', 'EVALUATION_REJECTED',
    'AWAITING_ACTION_EVIDENCE', 'ACTIONS_IN_PATH', 'ACTION_CHECK_REJECTED')
AWAITING_REASONS = ('NO_DAILY_EVIDENCE', 'RECEIVED_BEFORE_DETECTION', 'NO_ACTION_EVIDENCE')
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


def _config(observation_directory, outcome_directory, daily, sessions, assumptions, actions,
            attestation_directory, attest_held_outcomes, now):
    if type(attest_held_outcomes) is not bool:
        raise DecisionOutcomeProductionError('CONFIG_INVALID', 'attest_held_outcomes must be a bool')
    if attest_held_outcomes and attestation_directory is None:
        raise DecisionOutcomeProductionError(
            'CONFIG_INVALID', 'held-outcome attestation requires an attestation directory')
    stores = [_directory(observation_directory), _directory(outcome_directory)]
    if attestation_directory is not None:
        # Only a CLEAR check is attestable, so attestation requires the check.
        if actions is None:
            raise DecisionOutcomeProductionError(
                'CONFIG_INVALID', 'attestation requires action selections')
        stores.append(_directory(attestation_directory))
    if len(set(stores)) != len(stores):
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
    if actions is not None:
        if type(actions) is not dict:
            raise DecisionOutcomeProductionError('CONFIG_INVALID', 'action selection mapping required')
        for key, selection in actions.items():
            if type(key) is not str or type(selection) is not OutcomeActionSelection:
                raise DecisionOutcomeProductionError(
                    'CONFIG_INVALID', 'observation identity -> OutcomeActionSelection required')
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
        'corporate_action_status': None, 'corporate_action_check': None,
        'attestation_status': None, 'attestation_identity': None, 'attestation_detail': None,
    }


def _window(sessions, detected, received):
    """CALENDAR_THROUGH_RECEIPT_DATE_V1: Cairo detection date through receipt date."""
    first, last = detected.astimezone(CAIRO).date(), received.astimezone(CAIRO).date()
    return tuple(item for item in sessions if first <= item.market_date <= last)


def _action_report(identity, record, selection, path_complete_through_at, now):
    """The unchanged pure check over one path; raises ValueError (fail closed)."""
    return check_egx_outcome_corporate_actions(
        identity, record, selection.evidence,
        source_ingestion_id=selection.source_ingestion_id,
        review_ingestion_ids=selection.review_ingestion_ids,
        # PATH_COMPLETION_DATE_CAIRO_V1: the path's calendar ends on this date.
        path_through=path_complete_through_at.astimezone(CAIRO).date(),
        as_of=now)


def _check_actions(entry, identity, record, bound, actions, now):
    """True only when the unchanged check reports CLEAR; otherwise the entry says why."""
    selection = actions.get(identity)
    if selection is None:
        entry.update(status='AWAITING_ACTION_EVIDENCE', reason='NO_ACTION_EVIDENCE')
        return False
    try:
        report = _action_report(identity, record, selection, bound['path_complete_through_at'],
                                now)
    except ValueError as exc:
        entry.update(status='ACTION_CHECK_REJECTED', detail=_detail(exc))
        return False
    entry.update(corporate_action_status=report['status'], corporate_action_check=report)
    if report['status'] != 'CLEAR':
        # The raw path is not comparable with the plan: never retained, never adjusted.
        entry['status'] = 'ACTIONS_IN_PATH'
        return False
    return True


def _attest(entry, identity, record, outcome, attestation_directory, now):
    """Retain the attestation of an outcome this run retained after a CLEAR check."""
    outcome_identity = entry['outcome_identity']
    # A store ALREADY_RETAINED under another identity kept a different first
    # outcome; this run's CLEAR check is for this run's path, not that record.
    if outcome_identity != sha256(canonical_bytes(outcome)).hexdigest():
        entry['attestation_status'] = 'RETAINED_OUTCOME_DIFFERS'
        return
    try:
        attestation = attest_outcome_corporate_actions(
            outcome_identity, outcome, (identity, record), entry['corporate_action_check'])
    except ValueError as exc:
        entry.update(attestation_status='ATTESTATION_REJECTED', attestation_detail=_detail(exc))
        return
    try:
        retained = retain_outcome_action_attestation(
            attestation_directory, attestation, (outcome_identity, outcome), (identity, record),
            now=now)
    except OutcomeAttestationStoreError as exc:
        raise DecisionOutcomeProductionError(
            'ATTESTATION_FAILED', f"{record['symbol']}: {exc.reason}") from None
    if (retained.get('outcome_identity') != outcome_identity
            or retained.get('status') not in ('RETAINED', 'ALREADY_RETAINED')):
        raise DecisionOutcomeProductionError('ATTESTATION_FAILED', 'unexpected result')
    entry.update(attestation_status=retained['status'], attestation_identity=retained['identity'])


def _attest_held(entry, identity, record, outcome, actions, attestation_directory, now):
    """HELD_RECHECK_AFTER_EVALUATION_V1 for one outcome already in the store.

    The held outcome is never re-evaluated, re-retained or adjusted: the
    unchanged check runs over its own retained path (Cairo date of its
    ``path_complete_through_at``) at the producer clock, and only a CLEAR report
    is attested (``check_timing`` AFTER_EVALUATION whenever the clock is later
    than its evaluation). Anything else leaves it UNATTESTED, visibly.
    """
    selection = actions.get(identity)
    if selection is None:
        entry['attestation_status'] = 'HELD_AWAITING_ACTION_EVIDENCE'
        return
    try:
        report = _action_report(
            identity, record, selection,
            _time(outcome['path_complete_through_at'], 'path_complete_through_at'), now)
    except ValueError as exc:
        entry.update(attestation_status='HELD_CHECK_REJECTED', attestation_detail=_detail(exc))
        return
    entry.update(corporate_action_status=report['status'], corporate_action_check=report)
    if report['status'] != 'CLEAR':
        # Kept in the outcome store (write-once) but never attested, so never clear.
        entry['attestation_status'] = 'HELD_ACTIONS_IN_PATH'
        return
    _attest(entry, identity, record, outcome, attestation_directory, now)


def _produce_one(entry, identity, record, outcome_directory, daily, sessions, assumptions,
                 actions, attestation_directory, now):
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
    if actions is not None and not _check_actions(entry, identity, record, bound, actions, now):
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
    if attestation_directory is not None:
        # Reached only after a CLEAR check (attestation requires actions).
        _attest(entry, identity, record, outcome, attestation_directory, now)


def produce_egx_decision_outcomes(observation_directory, outcome_directory, *, daily, sessions,
                                  assumptions, now=None, actions=None,
                                  attestation_directory=None, attest_held_outcomes=False):
    """Evaluate and retain terminal outcomes for every retained EGX observation.

    ``daily`` maps canonical instrument UUID strings to ``EgxDailyEvidence``;
    ``sessions`` is a tuple of retained ``MarketSession`` rows (Africa/Cairo);
    ``now`` is the producer clock (defaults to real UTC), used as both
    ``evaluated_at``, the outcome-store clock and the corporate-action check's
    ``as_of``. ``actions`` (optional) maps observation identities to
    ``OutcomeActionSelection``; when given, a terminal outcome is retained only
    if the unchanged check reports CLEAR. ``attestation_directory`` (optional,
    requires ``actions``) is the host-provisioned sidecar where each outcome
    this run retains after a CLEAR check gets its write-once attestation.
    ``attest_held_outcomes`` (default False; requires ``attestation_directory``)
    also re-checks each outcome already held in the store at the producer clock
    and attests it only if that check is CLEAR. Returns a
    ``decision-outcome-production-v4`` result with one entry per observation.
    Refusals raise ``DecisionOutcomeProductionError``.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if not _aware(now):
        raise TypeError('now must be a timezone-aware datetime')
    assumptions = _config(observation_directory, outcome_directory, daily, sessions,
                          assumptions, actions, attestation_directory, attest_held_outcomes, now)
    try:
        observations = load_retained_decision_observations(observation_directory, market='EGX')
    except DecisionObservationStoreError as exc:
        raise DecisionOutcomeProductionError('OBSERVATIONS_UNAVAILABLE', exc.reason) from None
    try:
        held = load_retained_decision_outcomes(outcome_directory, market='EGX',
                                               assumptions=assumptions)
    except DecisionOutcomeStoreError as exc:
        raise DecisionOutcomeProductionError('OUTCOMES_UNAVAILABLE', exc.reason) from None
    if attestation_directory is not None:
        # Structure-only read before any evaluation: a missing or damaged
        # sidecar refuses the run before any outcome is written.
        try:
            load_retained_outcome_attestations(attestation_directory)
        except OutcomeAttestationStoreError as exc:
            raise DecisionOutcomeProductionError('ATTESTATIONS_UNAVAILABLE', exc.reason) from None
    held = {record['observation_identity']: record for record in held}
    population = {identity for identity, _ in observations}

    entries, rechecked = [], 0
    for identity, record in observations:
        entry = _entry(identity, record)
        if identity in held:
            outcome = held[identity]
            entry.update(status='ALREADY_RETAINED',
                         outcome_identity=sha256(canonical_bytes(outcome)).hexdigest(),
                         outcome_status=outcome['status'], outcome_reason=outcome['reason'],
                         path_complete_through_at=outcome['path_complete_through_at'])
            if attest_held_outcomes:
                # Requires attestation_directory, which requires actions.
                _attest_held(entry, identity, record, outcome, actions, attestation_directory,
                             now)
                rechecked += 1
        else:
            _produce_one(entry, identity, record, outcome_directory, daily, sessions,
                         assumptions, actions, attestation_directory, now)
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
        'corporate_actions': 'NOT_CHECKED' if actions is None else 'CHECKED_BEFORE_RETENTION',
        'corporate_action_window_rule': None if actions is None else ACTION_WINDOW_RULE,
        'corporate_action_path_rule': None if actions is None else ACTION_PATH_RULE,
        # Selections naming no observation in the store (visible, never used).
        'action_selections_without_observation': (
            None if actions is None else sum(i not in population for i in actions)),
        # decision-outcome-v1 records do not carry the check result.
        'retained_outcome_corporate_actions': 'NOT_RECORDED_IN_OUTCOME',
        # decision-outcome-production-v3: optional sidecar attestation step.
        'outcome_attestation': (
            'NOT_REQUESTED' if attestation_directory is None
            else 'ATTESTED_AFTER_CLEAR_RETENTION'),
        'attestation_counts': (
            None if attestation_directory is None
            else {s: sum(e['attestation_status'] == s for e in entries)
                  for s in ATTESTATION_STATUSES}),
        # decision-outcome-production-v4: outcomes held before this run are
        # re-checked and attested only with the opt-in attest_held_outcomes.
        'held_outcome_attestation': 'NOT_PERFORMED' if not attest_held_outcomes else HELD_RULE,
        'held_outcomes_rechecked': rechecked if attest_held_outcomes else None,
        'price_adjustment': 'NOT_APPLIED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
