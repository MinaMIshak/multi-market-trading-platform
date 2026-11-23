"""Trusted host job: retained EGX stores -> attested-CLEAR-only metrics at one as_of.

    python -m app.decision_outcome_metrics_job \
        --observation-dir /abs/decision-observations \
        --outcome-dir /abs/decision-outcomes \
        --attestation-dir /abs/outcome-attestations \
        --as-of 2026-11-20T12:00:00Z \
        --assumptions-version <id> --entry-slippage-bps <n> \
        --exit-slippage-bps <n> --cost-bps-per-side <n> [--by-event-state]

Phase G/H/I/K reporting entrypoint (decision-outcome-metrics-job-v2). It is
read-only and only wires existing pieces together; it adds no metric, partition,
attestation, segmentation or outcome logic:

1. the three host-provisioned directories must be absolute and distinct, and
   ``as_of`` must be an explicit timezone-aware instant at or before the job
   clock (a future ``as_of`` is refused, so a report cannot be presented before
   its records could exist). The outcome assumptions are the outcome job's own
   explicit ``outcome_assumptions`` (every value supplied; no defaults);
2. the unchanged store loaders read every retained EGX observation, every
   retained outcome under exactly these assumptions and every retained
   attestation (each loader fails closed on any damaged or unexpected entry;
   partial content is never used);
3. point-in-time selection SELECTION_RULE: an observation is in the population
   only if its ``recorded_at`` is at or before ``as_of``, and an outcome is
   supplied only if its ``evaluated_at`` is at or before ``as_of``. Excluded
   records are not dropped silently: their identities are listed in the result,
   and an observation whose outcome was evaluated later stays in the population
   and is counted NOT_EVALUATED. The times are the records' own clocks, not
   their retention times (the stores do not record retention time), so the
   selection is labelled RECORD_TIMES_NOT_RETENTION_TIMES. Attestations are
   all passed through: the caller applies its own CHECK_AS_OF rule, and an
   attestation whose outcome was not supplied is listed by the partition;
4. the unchanged pure ``aggregate_egx_checked_decision_outcomes`` computes the
   report; any integrity refusal fails the run (METRICS stage);
5. opt-in (``--by-event-state``; v2): the unchanged pure
   ``aggregate_egx_checked_decision_outcomes_by_event_state`` is run over
   exactly the same selected population, supplied outcomes and loaded
   attestations at the same ``as_of`` and embedded as ``event_state_report``.
   It needs no registered policy. Any refusal fails the whole run (METRICS
   stage); no partial report is emitted. Without the flag the field is
   EVENT_STATE_NOT_REQUESTED and the rest of the result is unchanged from v1.
   The event state is the detection-time inference retained in each
   observation (producer observations are NOT_ATTACHED); it is not an observed
   market fact, interprets no risk and has no cross-state headline. Period,
   data-age and sample reports need caller-supplied policies and are not
   offered here (no policy is registered).

The job writes nothing (no store, file or database; no database is opened),
schedules nothing and is not scheduled: SYSTEM keeps the gap
OUTCOME_METRICS_JOB_NOT_SCHEDULED, and because no store is provisioned and no
scheduled job retains attestations, OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays
open. Population and attestation completeness remain the host's assertion.
Sample size is exposed and not assessed. Outcomes are hypothetical results from
session bars: not fills, not performance. PAPER/SHADOW only; LIVE_MONEY=false.

Status REPORTED exits 0. Failures exit 1 with one fixed stage (INPUT, STORES,
METRICS) and the refusing module's reason.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sys

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import (
    DecisionObservationStoreError, _time, load_retained_decision_observations)
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_attestation_store import (
    OutcomeAttestationStoreError, load_retained_outcome_attestations)
from app.decision_outcome_checked_event_states import (
    aggregate_egx_checked_decision_outcomes_by_event_state)
from app.decision_outcome_checked_metrics import aggregate_egx_checked_decision_outcomes
from app.decision_outcome_job import OutcomeJobError, outcome_assumptions
from app.decision_outcome_store import DecisionOutcomeStoreError, load_retained_decision_outcomes
from app.scanner_v2_card_report_job import _absolute

CONTRACT_VERSION = 'decision-outcome-metrics-job-v2'
EVENT_STATE_NOT_REQUESTED = 'NOT_REQUESTED'
JOB_STATUSES = ('REPORTED', 'FAILED')
FAILURE_STAGES = ('INPUT', 'STORES', 'METRICS')
# Reasons raised by this module; STORES-stage failures carry the refusing
# store loader's STORE_REASONS unchanged.
JOB_REASONS = ('CONFIG_INVALID', 'METRICS_REJECTED')
EXIT_CODES = {'REPORTED': 0, 'FAILED': 1}
SELECTION_RULE = 'RECORDED_AND_EVALUATED_AT_OR_BEFORE_AS_OF_V1'
SELECTION_TIME_BASIS = 'RECORD_TIMES_NOT_RETENTION_TIMES'
ATTESTATION_SELECTION = 'ALL_LOADED_CHECK_AS_OF_RULE_APPLIED_BY_METRICS'
SAFETY = {'writes': 'NONE', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
          'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


class MetricsJobError(RuntimeError):
    """Job input or metrics refused; ``reason`` is one of JOB_REASONS."""

    def __init__(self, reason, detail=None, *, stage='INPUT'):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail, self.stage = reason, detail, stage


def _utc_now():
    return datetime.now(timezone.utc)


def _aware(value):
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None)


def parse_as_of(text):
    """Explicit timezone-aware ISO-8601 instant, or CONFIG_INVALID (naive refused)."""
    if type(text) is not str or not text:
        raise MetricsJobError('CONFIG_INVALID', 'explicit as_of required')
    value = text[:-1] + '+00:00' if text.endswith('Z') else text
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise MetricsJobError('CONFIG_INVALID', 'as_of is not ISO-8601') from None
    if not _aware(parsed):
        raise MetricsJobError('CONFIG_INVALID', 'timezone-aware as_of required')
    return parsed


def _config(observation_directory, outcome_directory, attestation_directory, assumptions,
            as_of, now, by_event_state):
    if type(by_event_state) is not bool:
        raise MetricsJobError('CONFIG_INVALID', 'by_event_state must be a bool')
    directories = (observation_directory, outcome_directory, attestation_directory)
    if not all(_absolute(v) for v in directories):
        raise MetricsJobError('CONFIG_INVALID', 'absolute paths required')
    if len({os.path.normpath(v) for v in directories}) != len(directories):
        raise MetricsJobError('CONFIG_INVALID', 'store directories must be distinct')
    if type(assumptions) is not OutcomeAssumptions or assumptions.market != 'EGX':
        raise MetricsJobError('CONFIG_INVALID', 'exact EGX OutcomeAssumptions required')
    if not _aware(as_of):
        raise MetricsJobError('CONFIG_INVALID', 'timezone-aware as_of required')
    if not _aware(now):
        raise MetricsJobError('CONFIG_INVALID', 'timezone-aware job clock required')
    if as_of > now:
        raise MetricsJobError('CONFIG_INVALID', 'as_of after the job clock')


def run_egx_decision_outcome_metrics_job(*, observation_directory, outcome_directory,
                                         attestation_directory, assumptions, as_of, now=None,
                                         by_event_state=False):
    """Load the retained stores and report attested-CLEAR-only metrics at ``as_of``.

    Returns a ``decision-outcome-metrics-job-v2`` result wrapping the unchanged
    decision-outcome-checked-metrics-v1 report and, only when ``by_event_state``
    is True, the unchanged decision-outcome-checked-event-state-metrics-v1
    report over the same inputs (else EVENT_STATE_NOT_REQUESTED). Raises
    ``MetricsJobError`` (inputs, metrics) or the store loaders'
    ``DecisionObservationStoreError`` / ``DecisionOutcomeStoreError`` /
    ``OutcomeAttestationStoreError``. ``now`` is the job clock (default real
    UTC), used only to refuse a future ``as_of``. Writes nothing.
    """
    now = _utc_now() if now is None else now
    _config(observation_directory, outcome_directory, attestation_directory, assumptions,
            as_of, now, by_event_state)
    observations = load_retained_decision_observations(observation_directory, market='EGX')
    outcomes = load_retained_decision_outcomes(outcome_directory, market='EGX',
                                               assumptions=assumptions)
    attestations = load_retained_outcome_attestations(attestation_directory)

    # SELECTION_RULE: records not yet recorded/evaluated at as_of are excluded
    # visibly; an excluded outcome's observation stays in the population.
    population, observations_after = [], []
    for identity, record in observations:
        if _time(record['recorded_at'], 'recorded_at') > as_of:
            observations_after.append(identity)
        else:
            population.append((identity, record))
    supplied, outcomes_after = [], []
    for outcome in outcomes:
        if _time(outcome['evaluated_at'], 'evaluated_at') > as_of:
            outcomes_after.append(sha256(canonical_bytes(outcome)).hexdigest())
        else:
            supplied.append(outcome)

    try:
        report = aggregate_egx_checked_decision_outcomes(
            tuple(population), tuple(supplied), tuple(attestations),
            assumptions=assumptions, as_of=as_of)
        # Same selected inputs and as_of as the report above; all or nothing.
        event_state_report = (aggregate_egx_checked_decision_outcomes_by_event_state(
            tuple(population), tuple(supplied), tuple(attestations),
            assumptions=assumptions, as_of=as_of)
            if by_event_state else EVENT_STATE_NOT_REQUESTED)
    except (ValueError, TypeError, RecursionError) as exc:
        raise MetricsJobError('METRICS_REJECTED', str(exc), stage='METRICS') from None
    return {
        'schema_version': CONTRACT_VERSION,
        'status': 'REPORTED',
        'as_of': _utc(as_of),
        'job_clock': _utc(now),
        'selection': {
            'rule': SELECTION_RULE,
            'time_basis': SELECTION_TIME_BASIS,
            'observations_loaded': len(observations),
            'observations_in_population': len(population),
            'observations_recorded_after_as_of': sorted(observations_after),
            'outcomes_loaded': len(outcomes),
            'outcomes_supplied': len(supplied),
            'outcomes_evaluated_after_as_of': sorted(outcomes_after),
            'attestations_loaded': len(attestations),
            'attestation_selection': ATTESTATION_SELECTION,
        },
        'report': report,
        'event_state_report': event_state_report,
        **SAFETY,
    }


def _failure(stage, exc):
    detail = None if exc.detail is None else str(exc.detail)[:200]
    return {'schema_version': CONTRACT_VERSION, 'status': 'FAILED', 'stage': stage,
            'reason': exc.reason, 'detail': detail, **SAFETY}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--observation-dir', '--outcome-dir', '--attestation-dir', '--as-of',
                 '--assumptions-version', '--entry-slippage-bps', '--exit-slippage-bps',
                 '--cost-bps-per-side'):
        parser.add_argument(name, required=True)
    parser.add_argument('--by-event-state', action='store_true')
    args = parser.parse_args(argv)
    try:
        assumptions = outcome_assumptions(
            assumptions_version=args.assumptions_version,
            entry_slippage_bps=args.entry_slippage_bps,
            exit_slippage_bps=args.exit_slippage_bps,
            cost_bps_per_side=args.cost_bps_per_side)
        result = run_egx_decision_outcome_metrics_job(
            observation_directory=args.observation_dir, outcome_directory=args.outcome_dir,
            attestation_directory=args.attestation_dir, assumptions=assumptions,
            as_of=parse_as_of(args.as_of), by_event_state=args.by_event_state)
    except MetricsJobError as exc:
        result = _failure(exc.stage, exc)
    except OutcomeJobError as exc:
        result = _failure('INPUT', exc)
    except (DecisionObservationStoreError, DecisionOutcomeStoreError,
            OutcomeAttestationStoreError) as exc:
        result = _failure('STORES', exc)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    sys.exit(main())
