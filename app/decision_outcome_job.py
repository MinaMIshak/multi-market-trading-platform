"""Trusted host job: retained EGX observations + daily evidence -> terminal outcomes.

    python -m app.decision_outcome_job --db-path /abs/platform.db \
        --raw-root /abs/raw \
        --observation-dir /abs/decision-observations \
        --outcome-dir /abs/decision-outcomes \
        --daily-raw-path <data_ingestions raw_path> [--daily-raw-path ...] \
        --calendar-observation <calendar_observations sha256> [--calendar-observation ...] \
        --assumptions-version <id> --entry-slippage-bps <n> \
        --exit-slippage-bps <n> --cost-bps-per-side <n>

Phase G/H collection entrypoint (decision-outcome-job-v2). It only wires
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
sample. Failures exit 1 with one fixed stage (INPUT, CALENDAR, DAILY_EVIDENCE,
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
import sys

from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_calendar import (
    MAX_OBSERVATIONS, CalendarChainError, chain_egx_calendar_observations)
from app.decision_outcome_calendar_loader import (
    CalendarObservationLoadError, load_egx_calendar_observations)
from app.decision_outcome_daily_evidence import DailyEvidenceLoadError, load_egx_daily_evidence
from app.decision_outcome_producer import (
    DecisionOutcomeProductionError, produce_egx_decision_outcomes)
from app.scanner_v2_card_report_job import (
    CardReportJobError, _absolute, _digest, _existing_database)

CONTRACT_VERSION = 'decision-outcome-job-v2'
JOB_STATUSES = ('PRODUCED', 'REJECTIONS_PRESENT', 'FAILED')
FAILURE_STAGES = ('INPUT', 'CALENDAR', 'DAILY_EVIDENCE', 'PRODUCTION')
# Reasons raised by this module; CALENDAR-stage failures carry the loader's
# LOAD_REASONS or the chain's CHAIN_REASONS unchanged.
JOB_REASONS = ('CONFIG_INVALID',)
EXIT_CODES = {'PRODUCED': 0, 'FAILED': 1, 'REJECTIONS_PRESENT': 2}
REJECTED_ENTRY_STATUSES = ('OBSERVATION_AFTER_CLOCK', 'BINDING_REJECTED', 'EVALUATION_REJECTED')
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
            calendar_observations, assumptions, now):
    if not all(_absolute(v) for v in (db_path, raw_root, observation_directory,
                                      outcome_directory)):
        raise OutcomeJobError('CONFIG_INVALID', 'absolute paths required')
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


def run_egx_decision_outcome_job(*, db_path, raw_root, observation_directory,
                                 outcome_directory, daily_raw_paths, calendar_observations,
                                 assumptions, now=None):
    """Load named retained evidence and retain terminal outcomes for EGX observations.

    ``calendar_observations`` is a tuple of exact ``calendar_observations``
    digests. Returns a ``decision-outcome-job-v2`` result wrapping the
    producer's result. Raises ``OutcomeJobError``/``CardReportJobError``
    (inputs), ``CalendarObservationLoadError``/``CalendarChainError``
    (calendar), ``DailyEvidenceLoadError`` (daily evidence) or
    ``DecisionOutcomeProductionError`` (production). ``now`` is the single job
    clock (default real UTC): the chain's ``as_of`` and the producer clock.
    """
    now = _utc_now() if now is None else now
    _config(db_path, raw_root, observation_directory, outcome_directory, daily_raw_paths,
            calendar_observations, assumptions, now)
    database = _existing_database(db_path)
    calendar = load_egx_calendar_observations(database, calendar_observations)
    chained = chain_egx_calendar_observations(calendar['observations'], as_of=now)
    loaded = load_egx_daily_evidence(database, raw_root, daily_raw_paths)
    production = produce_egx_decision_outcomes(
        observation_directory, outcome_directory, daily=loaded['daily'],
        sessions=chained['sessions'], assumptions=assumptions, now=now)
    rejected = sum(production['status_counts'][s] for s in REJECTED_ENTRY_STATUSES)
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
        'production': production, **SAFETY,
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
            assumptions=assumptions)
    except OutcomeJobError as exc:
        result = _failure(exc.stage, exc)
    except CardReportJobError as exc:
        result = _failure('INPUT', exc)
    except (CalendarObservationLoadError, CalendarChainError) as exc:
        result = _failure('CALENDAR', exc)
    except DailyEvidenceLoadError as exc:
        result = _failure('DAILY_EVIDENCE', exc)
    except DecisionOutcomeProductionError as exc:
        result = _failure('PRODUCTION', exc)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    sys.exit(main())
