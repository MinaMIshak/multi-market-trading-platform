"""Trusted host job: retained EGX observations + daily evidence -> terminal outcomes.

    python -m app.decision_outcome_job --db-path /abs/platform.db \
        --raw-root /abs/raw \
        --observation-dir /abs/decision-observations \
        --outcome-dir /abs/decision-outcomes \
        --daily-raw-path <data_ingestions raw_path> [--daily-raw-path ...] \
        --calendar-observation <calendar_observations sha256> [--calendar-observation ...] \
        --assumptions-version <id> --entry-slippage-bps <n> \
        --exit-slippage-bps <n> --cost-bps-per-side <n> \
        [--check-corporate-actions [--attestation-dir /abs/outcome-attestations]]

Phase G/H collection entrypoint (decision-outcome-job-v4). It only wires
existing pieces together; it adds no calendar, binding, evaluation, record or
store logic:

1. the database must already exist as a regular file and is opened with the
   card-report job's unchanged ``_existing_database`` (never created or
   upgraded; read only);
2. the job clock is sampled once (``now``) and used both as the calendar
   chain's ``as_of`` and as the producer clock, so no calendar observation
   recorded after the evaluation clock can shape an outcome path;
3. the unchanged ``load_egx_calendar_observations`` reads exactly the
   caller-named write-once ``calendar_observations`` rows (nothing searched
   for, ranged or chosen as latest; the mutable ``market_sessions`` table is
   never read), and the unchanged ``chain_egx_calendar_observations``
   re-validates each one at its own observation time and chains them into a
   contiguous session tuple (gaps, duplicates and late observations refused);
4. the unchanged ``load_egx_daily_evidence`` loads exactly the named
   ``data_ingestions`` raw paths;
5. the unchanged ``produce_egx_decision_outcomes`` binds, evaluates and
   retains terminal outcomes with the same clock under the caller's explicit
   ``OutcomeAssumptions`` (every value supplied; no default costs/slippage).

Corporate actions (v3, opt-in ``--check-corporate-actions``). Without the flag
the run is exactly v2 (no selections passed; corporate actions NOT_CHECKED;
``action_evidence`` is None). With it, the job requests the retained
egx-actions-v1 selection for every observation that can reach the producer's
corporate-action gate (ACTION_REQUEST_SCOPE: recorded at or before the job
clock, no retained outcome under these assumptions, daily evidence for its
instrument received at or after detection and not after the clock), with
``path_through`` = the Africa/Cairo date of that daily evidence's receipt
(ACTION_PATH_THROUGH_RULE). That is the last date the producer's
CALENDAR_THROUGH_RECEIPT_DATE_V1 window can offer, so it is never before the
path-completion date the check later uses: coverage is required through the
receipt date (stricter, never looser). The unchanged
``load_egx_outcome_action_evidence_by_request`` selects point-in-time at the
job clock; its selections are passed unchanged to the producer, whose gate
retains a terminal outcome only on CLEAR and reports AWAITING_ACTION_EVIDENCE
for a refused request. The job adds no selection, window or check logic of its
own, and price is never adjusted. Retained decision-outcome-v1 records still
do not carry the check, so the SYSTEM gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED
stays open. More than the loader's MAX_REQUESTS requests refuse the run
(ACTION_EVIDENCE stage) rather than checking a subset.

Attestation (v4, opt-in ``--attestation-dir``, accepted only together with
``--check-corporate-actions``, absolute and distinct from both stores, else
CONFIG_INVALID at INPUT before the database is opened). The directory is passed
unchanged to the producer's decision-outcome-production-v3 attestation step:
each outcome this run retains after a CLEAR check gets one write-once sidecar
attestation built and retained by the unchanged builder and store with the
same job clock. The job builds, reads and verifies no attestation itself. A
missing or damaged sidecar refuses the run before any outcome or attestation is
written (ATTESTATIONS_UNAVAILABLE, PRODUCTION stage; the action-evidence
loader has already run and its labelled audit rows may exist); a sidecar store
refusal aborts it (ATTESTATION_FAILED, PRODUCTION stage). A per-observation builder refusal
(ATTESTATION_REJECTED) or held bytes differing from this run's
(RETAINED_OUTCOME_DIFFERS) leaves a retained but UNATTESTED outcome and counts
as a rejection (exit 2), so the host sees it. Outcomes already held when the
run starts are neither re-checked nor attested, so they stay UNATTESTED (never
clear). Without the flag the run is exactly v3 (``outcome_attestation``
NOT_REQUESTED). The sidecar directory must be provisioned by a host, and
nothing reads attestations for metrics yet, so the SYSTEM gaps
OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED and
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stay open.

decision-outcome-job-v1 read current, upserted ``market_sessions`` rows by
date range; v2 replaces that with retained observations, so the calendar is
labelled CALENDAR_OBSERVATIONS_RETAINED / WRITE_ONCE_RETAINED and the chain
report (with its ``sessions_sha256``) and per-digest provenance are returned.
The observation clock is the repository's own (REPOSITORY_CLOCK_UNVERIFIED)
and calendar admission stays NOT_ESTABLISHED.

Status PRODUCED (exit 0) means no observation was rejected: each one has a
retained terminal outcome or is still waiting (PENDING_PATH,
AWAITING_DAILY_EVIDENCE). REJECTIONS_PRESENT (exit 2) means the run completed
but at least one observation was OBSERVATION_AFTER_CLOCK, BINDING_REJECTED or
EVALUATION_REJECTED, so the host sees it rather than a silently smaller
sample; with the check, ACTION_CHECK_REJECTED (the check failed closed) is
also a rejection, while AWAITING_ACTION_EVIDENCE and ACTIONS_IN_PATH are
reported per observation as waiting / not retained; with attestation,
ATTESTATION_REJECTED and RETAINED_OUTCOME_DIFFERS are rejections too. Failures
exit 1 with one fixed stage (INPUT, CALENDAR, DAILY_EVIDENCE, ACTION_EVIDENCE,
PRODUCTION) and the refusing module's reason; write-once outcomes retained
earlier in a failed run stay, and a rerun is idempotent.

There is no scheduler or cron entry in this repository: SYSTEM keeps the gap
OUTCOME_JOB_NOT_SCHEDULED and both store directories must be provisioned by a
host. Corporate actions NOT_CHECKED; outcomes are hypothetical results from
session bars, not fills and not performance. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import sys

from app.decision_observation_store import (
    DecisionObservationStoreError, _time, load_retained_decision_observations)
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_action_evidence import (
    REQUEST_STATUSES as ACTION_REQUEST_STATUSES, ActionEvidenceLoadError,
    load_egx_outcome_action_evidence_by_request)
from app.decision_outcome_calendar import (
    MAX_OBSERVATIONS, CalendarChainError, chain_egx_calendar_observations)
from app.decision_outcome_calendar_loader import (
    CalendarObservationLoadError, load_egx_calendar_observations)
from app.decision_outcome_daily_evidence import DailyEvidenceLoadError, load_egx_daily_evidence
from app.decision_outcome_producer import (
    DecisionOutcomeProductionError, produce_egx_decision_outcomes)
from app.decision_outcome_store import DecisionOutcomeStoreError, load_retained_decision_outcomes
from app.scanner_v2_card_report_job import (
    CardReportJobError, _absolute, _digest, _existing_database)
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-job-v4'
JOB_STATUSES = ('PRODUCED', 'REJECTIONS_PRESENT', 'FAILED')
FAILURE_STAGES = ('INPUT', 'CALENDAR', 'DAILY_EVIDENCE', 'ACTION_EVIDENCE', 'PRODUCTION')
# Reasons raised by this module; CALENDAR-stage failures carry the loader's
# LOAD_REASONS or the chain's CHAIN_REASONS unchanged, ACTION_EVIDENCE-stage
# failures the action-evidence loader's LOAD_REASONS unchanged.
JOB_REASONS = ('CONFIG_INVALID',)
EXIT_CODES = {'PRODUCED': 0, 'FAILED': 1, 'REJECTIONS_PRESENT': 2}
REJECTED_ENTRY_STATUSES = ('OBSERVATION_AFTER_CLOCK', 'BINDING_REJECTED', 'EVALUATION_REJECTED',
                           'ACTION_CHECK_REJECTED')
# Producer attestation_status values that leave a retained outcome UNATTESTED
# for a reason other than waiting (only counted when attestation is requested).
REJECTED_ATTESTATION_STATUSES = ('ATTESTATION_REJECTED', 'RETAINED_OUTCOME_DIFFERS')
ACTION_REQUEST_SCOPE = 'OBSERVATIONS_REACHABLE_BY_PRODUCER_ACTION_GATE_V1'
ACTION_PATH_THROUGH_RULE = 'DAILY_RECEIPT_DATE_CAIRO_V1'
CALENDAR_SOURCE = 'CALENDAR_OBSERVATIONS_RETAINED'
CALENDAR_RETENTION = 'WRITE_ONCE_RETAINED'
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'corporate_actions': 'NOT_CHECKED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


class OutcomeJobError(RuntimeError):
    """Job input unusable; ``reason`` is one of JOB_REASONS."""

    def __init__(self, reason, detail=None, *, stage='INPUT'):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail, self.stage = reason, detail, stage


def _utc_now():
    return datetime.now(timezone.utc)


def outcome_assumptions(*, assumptions_version, entry_slippage_bps, exit_slippage_bps,
                        cost_bps_per_side):
    """Exact EGX ``OutcomeAssumptions`` from explicit decimal strings, or CONFIG_INVALID."""
    values = (entry_slippage_bps, exit_slippage_bps, cost_bps_per_side)
    if type(assumptions_version) is not str or any(type(v) is not str for v in values):
        raise OutcomeJobError('CONFIG_INVALID', 'explicit assumption strings required')
    try:
        entry, exit_, cost = (Decimal(v) for v in values)
        return OutcomeAssumptions(assumptions_version=assumptions_version, market='EGX',
                                  entry_slippage_bps=entry, exit_slippage_bps=exit_,
                                  cost_bps_per_side=cost)
    except (ArithmeticError, ValueError):
        raise OutcomeJobError('CONFIG_INVALID', 'invalid outcome assumptions') from None


def _config(db_path, raw_root, observation_directory, outcome_directory, daily_raw_paths,
            calendar_observations, assumptions, now, check_corporate_actions,
            attestation_directory):
    if type(check_corporate_actions) is not bool:
        raise OutcomeJobError('CONFIG_INVALID', 'check_corporate_actions must be a bool')
    if not all(_absolute(v) for v in (db_path, raw_root, observation_directory,
                                      outcome_directory)):
        raise OutcomeJobError('CONFIG_INVALID', 'absolute paths required')
    if attestation_directory is not None:
        # Only an outcome retained after a CLEAR check is attestable.
        if not check_corporate_actions:
            raise OutcomeJobError('CONFIG_INVALID',
                                  'attestation directory requires check_corporate_actions')
        if not _absolute(attestation_directory):
            raise OutcomeJobError('CONFIG_INVALID', 'absolute attestation directory required')
        if os.path.normpath(attestation_directory) in {
                os.path.normpath(observation_directory), os.path.normpath(outcome_directory)}:
            raise OutcomeJobError('CONFIG_INVALID',
                                  'attestation directory must differ from both stores')
    if type(daily_raw_paths) is not tuple or not daily_raw_paths:
        raise OutcomeJobError('CONFIG_INVALID', 'daily raw paths required')
    if (type(calendar_observations) is not tuple
            or not 0 < len(calendar_observations) <= MAX_OBSERVATIONS
            or not all(_digest(v) for v in calendar_observations)
            or len(set(calendar_observations)) != len(calendar_observations)):
        raise OutcomeJobError(
            'CONFIG_INVALID',
            f'1..{MAX_OBSERVATIONS} unique lowercase SHA-256 calendar observation digests required')
    if type(assumptions) is not OutcomeAssumptions or assumptions.market != 'EGX':
        raise OutcomeJobError('CONFIG_INVALID', 'exact EGX OutcomeAssumptions required')
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise OutcomeJobError('CONFIG_INVALID', 'timezone-aware job clock required')


def _action_requests(observation_directory, outcome_directory, daily, assumptions, now):
    """ACTION_REQUEST_SCOPE requests, in observation-store order.

    Mirrors only the producer's own preconditions for reaching its gate; the
    store loads are the producer's own and refuse with its reasons.
    """
    try:
        observations = load_retained_decision_observations(observation_directory, market='EGX')
    except DecisionObservationStoreError as exc:
        raise DecisionOutcomeProductionError('OBSERVATIONS_UNAVAILABLE', exc.reason) from None
    try:
        held = load_retained_decision_outcomes(outcome_directory, market='EGX',
                                               assumptions=assumptions)
    except DecisionOutcomeStoreError as exc:
        raise DecisionOutcomeProductionError('OUTCOMES_UNAVAILABLE', exc.reason) from None
    held = {record['observation_identity'] for record in held}
    requests = []
    for identity, record in observations:
        if identity in held or _time(record['recorded_at'], 'recorded_at') > now:
            continue  # ALREADY_RETAINED / OBSERVATION_AFTER_CLOCK: never checked
        evidence = daily.get(record['instrument_id'])
        if (evidence is None or evidence.received_at < _time(record['detected_at'], 'detected_at')
                or evidence.received_at > now):
            continue  # AWAITING_DAILY_EVIDENCE, or the producer refuses the receipt
        # ACTION_PATH_THROUGH_RULE: the last date the producer's window can offer.
        requests.append((identity, record, evidence.received_at.astimezone(CAIRO).date()))
    return tuple(requests)


def _action_evidence(database, raw_root, requests, now):
    """(selections, summary) from the unchanged by-request loader, or none when no request."""
    summary = {'requested': True, 'request_scope': ACTION_REQUEST_SCOPE,
               'path_through_rule': ACTION_PATH_THROUGH_RULE}
    if not requests:
        counts = dict.fromkeys(ACTION_REQUEST_STATUSES, 0)
        return {}, {**summary, 'loader_called': False, 'schema_version': None, 'as_of': None,
                    'request_counts': {'requests': 0, **counts}, 'sources': [],
                    'database_writes': 'NONE'}
    loaded = load_egx_outcome_action_evidence_by_request(database, raw_root, requests, as_of=now)
    return loaded['selections'], {
        **summary, 'loader_called': True,
        **{key: loaded[key] for key in (
            'schema_version', 'as_of', 'request_counts', 'sources', 'refusal_scope',
            'refused_request_effect', 'evidence_selection', 'review_verification',
            'reviewer_authority', 'price_adjustment', 'database_writes')}}


def run_egx_decision_outcome_job(*, db_path, raw_root, observation_directory,
                                 outcome_directory, daily_raw_paths, calendar_observations,
                                 assumptions, now=None, check_corporate_actions=False,
                                 attestation_directory=None):
    """Load named retained evidence and retain terminal outcomes for EGX observations.

    ``calendar_observations`` is a tuple of exact ``calendar_observations``
    digests. Returns a ``decision-outcome-job-v4`` result wrapping the
    producer's result. Raises ``OutcomeJobError``/``CardReportJobError``
    (inputs), ``CalendarObservationLoadError``/``CalendarChainError``
    (calendar), ``DailyEvidenceLoadError`` (daily evidence),
    ``ActionEvidenceLoadError`` (action evidence, only when checking) or
    ``DecisionOutcomeProductionError`` (production). ``now`` is the single job
    clock (default real UTC): the chain's ``as_of``, the action-evidence
    ``as_of`` and the producer clock. ``check_corporate_actions`` (default
    False: v2 behaviour) passes the retained action selections to the
    producer's gate. ``attestation_directory`` (default None: v3 behaviour;
    requires ``check_corporate_actions``) is passed unchanged to the
    producer's opt-in attestation step.
    """
    now = _utc_now() if now is None else now
    _config(db_path, raw_root, observation_directory, outcome_directory, daily_raw_paths,
            calendar_observations, assumptions, now, check_corporate_actions,
            attestation_directory)
    database = _existing_database(db_path)
    calendar = load_egx_calendar_observations(database, calendar_observations)
    chained = chain_egx_calendar_observations(calendar['observations'], as_of=now)
    loaded = load_egx_daily_evidence(database, raw_root, daily_raw_paths)
    actions, action_summary = None, None
    if check_corporate_actions:
        requests = _action_requests(observation_directory, outcome_directory, loaded['daily'],
                                    assumptions, now)
        actions, action_summary = _action_evidence(database, raw_root, requests, now)
    production = produce_egx_decision_outcomes(
        observation_directory, outcome_directory, daily=loaded['daily'],
        sessions=chained['sessions'], assumptions=assumptions, now=now, actions=actions,
        attestation_directory=attestation_directory)
    rejected = sum(production['status_counts'][s] for s in REJECTED_ENTRY_STATUSES)
    if production['attestation_counts'] is not None:
        rejected += sum(production['attestation_counts'][s]
                        for s in REJECTED_ATTESTATION_STATUSES)
    return {
        'schema_version': CONTRACT_VERSION,
        'status': 'PRODUCED' if rejected == 0 else 'REJECTIONS_PRESENT',
        'calendar': {'observations_schema_version': calendar['schema_version'],
                     'selection': calendar['selection'], 'sources': calendar['sources'],
                     'observed_at_clock': calendar['observed_at_clock'],
                     'chain': chained['report'], 'source': CALENDAR_SOURCE,
                     'retention': CALENDAR_RETENTION, 'admission': 'NOT_ESTABLISHED'},
        'daily_evidence': {'schema_version': loaded['schema_version'],
                           'sources': loaded['sources'],
                           'received_at_clock': loaded['received_at_clock'],
                           'market_membership': loaded['market_membership']},
        # None when the check was not requested (v2 behaviour).
        'action_evidence': action_summary,
        'production': production, **SAFETY,
        # The producer's own label: NOT_CHECKED or CHECKED_BEFORE_RETENTION.
        'corporate_actions': production['corporate_actions'],
        # The producer's own label: NOT_REQUESTED or ATTESTED_AFTER_CLEAR_RETENTION.
        'outcome_attestation': production['outcome_attestation'],
    }


def _failure(stage, exc):
    detail = None if exc.detail is None else str(exc.detail)[:200]
    return {'schema_version': CONTRACT_VERSION, 'status': 'FAILED', 'stage': stage,
            'reason': exc.reason, 'detail': detail, **SAFETY}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--db-path', '--raw-root', '--observation-dir', '--outcome-dir',
                 '--assumptions-version', '--entry-slippage-bps', '--exit-slippage-bps',
                 '--cost-bps-per-side'):
        parser.add_argument(name, required=True)
    parser.add_argument('--daily-raw-path', action='append', required=True)
    parser.add_argument('--calendar-observation', action='append', required=True)
    parser.add_argument('--check-corporate-actions', action='store_true')
    parser.add_argument('--attestation-dir', default=None)
    args = parser.parse_args(argv)
    try:
        assumptions = outcome_assumptions(
            assumptions_version=args.assumptions_version,
            entry_slippage_bps=args.entry_slippage_bps,
            exit_slippage_bps=args.exit_slippage_bps,
            cost_bps_per_side=args.cost_bps_per_side)
        result = run_egx_decision_outcome_job(
            db_path=args.db_path, raw_root=args.raw_root,
            observation_directory=args.observation_dir, outcome_directory=args.outcome_dir,
            daily_raw_paths=tuple(args.daily_raw_path),
            calendar_observations=tuple(args.calendar_observation),
            assumptions=assumptions, check_corporate_actions=args.check_corporate_actions,
            attestation_directory=args.attestation_dir)
    except OutcomeJobError as exc:
        result = _failure(exc.stage, exc)
    except CardReportJobError as exc:
        result = _failure('INPUT', exc)
    except (CalendarObservationLoadError, CalendarChainError) as exc:
        result = _failure('CALENDAR', exc)
    except DailyEvidenceLoadError as exc:
        result = _failure('DAILY_EVIDENCE', exc)
    except ActionEvidenceLoadError as exc:
        result = _failure('ACTION_EVIDENCE', exc)
    except DecisionOutcomeProductionError as exc:
        result = _failure('PRODUCTION', exc)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    sys.exit(main())
