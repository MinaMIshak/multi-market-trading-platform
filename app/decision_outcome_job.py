"""Trusted host job: retained EGX observations + daily evidence -> terminal outcomes.

    python -m app.decision_outcome_job --db-path /abs/platform.db \
        --raw-root /abs/raw \
        --observation-dir /abs/decision-observations \
        --outcome-dir /abs/decision-outcomes \
        --daily-raw-path <data_ingestions raw_path> [--daily-raw-path ...] \
        --calendar-from YYYY-MM-DD --calendar-through YYYY-MM-DD \
        --assumptions-version <id> --entry-slippage-bps <n> \
        --exit-slippage-bps <n> --cost-bps-per-side <n>

Phase G/H collection entrypoint (decision-outcome-job-v1). It only wires
existing pieces together; it adds no binding, evaluation, record or store logic:

1. the database must already exist as a regular file and is opened with the
   card-report job's unchanged ``_existing_database`` (never created or
   upgraded; read methods only);
2. every calendar date from ``--calendar-from`` through ``--calendar-through``
   is read with the unchanged ``TradingRepository.get_market_session``; a
   missing date (CALENDAR_INCOMPLETE) or a row that does not match its date or
   is not Africa/Cairo (CALENDAR_ROW_REJECTED) refuses the run, so no gap is
   skipped or filled;
3. the unchanged ``load_egx_daily_evidence`` loads exactly the named
   ``data_ingestions`` raw paths (nothing searched for or chosen as latest);
4. the unchanged ``produce_egx_decision_outcomes`` binds, evaluates and
   retains terminal outcomes with its own clock under the caller's explicit
   ``OutcomeAssumptions`` (every value supplied; no default costs/slippage).

Calendar caveat: ``market_sessions`` rows are current, mutable rows (they are
upserted), not write-once retained observations. The job therefore labels the
calendar MARKET_SESSIONS_CURRENT_ROWS / MUTABLE_NOT_RETAINED and returns the
SHA-256 of the exact session JSON it read, so the run's calendar is
inspectable; SYSTEM keeps the gap OUTCOME_CALENDAR_FROM_MUTABLE_SESSIONS.

Status PRODUCED (exit 0) means no observation was rejected: each one has a
retained terminal outcome or is still waiting (PENDING_PATH,
AWAITING_DAILY_EVIDENCE). REJECTIONS_PRESENT (exit 2) means the run completed
but at least one observation was OBSERVATION_AFTER_CLOCK, BINDING_REJECTED or
EVALUATION_REJECTED, so the host sees it rather than a silently smaller
sample. Failures exit 1 with one fixed stage (INPUT, CALENDAR, DAILY_EVIDENCE,
PRODUCTION) and reason; write-once outcomes retained earlier in a failed run
stay, and a rerun is idempotent.

There is no scheduler or cron entry in this repository: SYSTEM keeps the gap
OUTCOME_JOB_NOT_SCHEDULED and both store directories must be provisioned by a
host. Corporate actions NOT_CHECKED; outcomes are hypothetical results from
session bars, not fills and not performance. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
import argparse
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
import sys

from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_daily_evidence import DailyEvidenceLoadError, load_egx_daily_evidence
from app.decision_outcome_producer import (
    DecisionOutcomeProductionError, produce_egx_decision_outcomes)
from app.domain.models import MarketSession
from app.scanner_v2_card_report_job import CardReportJobError, _absolute, _existing_database
from app.storage import TradingRepository

CONTRACT_VERSION = 'decision-outcome-job-v1'
JOB_STATUSES = ('PRODUCED', 'REJECTIONS_PRESENT', 'FAILED')
FAILURE_STAGES = ('INPUT', 'CALENDAR', 'DAILY_EVIDENCE', 'PRODUCTION')
JOB_REASONS = ('CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'CALENDAR_INCOMPLETE',
               'CALENDAR_ROW_REJECTED')
EXIT_CODES = {'PRODUCED': 0, 'FAILED': 1, 'REJECTIONS_PRESENT': 2}
REJECTED_ENTRY_STATUSES = ('OBSERVATION_AFTER_CLOCK', 'BINDING_REJECTED', 'EVALUATION_REJECTED')
MAX_CALENDAR_DAYS = 400
CALENDAR_SOURCE = 'MARKET_SESSIONS_CURRENT_ROWS'
CALENDAR_RETENTION = 'MUTABLE_NOT_RETAINED'
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'corporate_actions': 'NOT_CHECKED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


class OutcomeJobError(RuntimeError):
    """Job input or calendar unusable; ``reason`` is one of JOB_REASONS."""

    def __init__(self, reason, detail=None, *, stage='INPUT'):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail, self.stage = reason, detail, stage


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
            calendar_from, calendar_through, assumptions):
    if not all(_absolute(v) for v in (db_path, raw_root, observation_directory,
                                      outcome_directory)):
        raise OutcomeJobError('CONFIG_INVALID', 'absolute paths required')
    if type(daily_raw_paths) is not tuple or not daily_raw_paths:
        raise OutcomeJobError('CONFIG_INVALID', 'daily raw paths required')
    if type(calendar_from) is not date or type(calendar_through) is not date:
        raise OutcomeJobError('CONFIG_INVALID', 'calendar dates required')
    if not 0 <= (calendar_through - calendar_from).days < MAX_CALENDAR_DAYS:
        raise OutcomeJobError('CONFIG_INVALID', f'calendar range of 1..{MAX_CALENDAR_DAYS} days required')
    if type(assumptions) is not OutcomeAssumptions or assumptions.market != 'EGX':
        raise OutcomeJobError('CONFIG_INVALID', 'exact EGX OutcomeAssumptions required')


def _calendar(database, first, last):
    """Every date first..last via the unchanged accessor; no gap skipped or filled."""
    trading, sessions = TradingRepository(database), []
    day = first
    while day <= last:
        try:
            item = trading.get_market_session(day)
        except sqlite3.Error:
            raise OutcomeJobError('DATABASE_UNAVAILABLE', stage='CALENDAR') from None
        except ValueError:
            raise OutcomeJobError('CALENDAR_ROW_REJECTED', day.isoformat(),
                                  stage='CALENDAR') from None
        if item is None:
            raise OutcomeJobError('CALENDAR_INCOMPLETE', day.isoformat(), stage='CALENDAR')
        if (type(item) is not MarketSession or item.market_date != day
                or item.timezone != 'Africa/Cairo'):
            raise OutcomeJobError('CALENDAR_ROW_REJECTED', day.isoformat(), stage='CALENDAR')
        sessions.append(item)
        day += timedelta(days=1)
    return tuple(sessions)


def _calendar_sha256(sessions):
    payload = '[' + ','.join(item.model_dump_json() for item in sessions) + ']'
    return sha256(payload.encode('utf-8')).hexdigest()


def run_egx_decision_outcome_job(*, db_path, raw_root, observation_directory,
                                 outcome_directory, daily_raw_paths, calendar_from,
                                 calendar_through, assumptions, now=None):
    """Load named retained evidence and retain terminal outcomes for EGX observations.

    Returns a ``decision-outcome-job-v1`` result wrapping the producer's result.
    Raises ``OutcomeJobError``/``CardReportJobError`` (inputs or calendar),
    ``DailyEvidenceLoadError`` (daily evidence) or
    ``DecisionOutcomeProductionError`` (production). ``now`` is passed to the
    producer as its clock (default real UTC).
    """
    _config(db_path, raw_root, observation_directory, outcome_directory, daily_raw_paths,
            calendar_from, calendar_through, assumptions)
    database = _existing_database(db_path)
    sessions = _calendar(database, calendar_from, calendar_through)
    loaded = load_egx_daily_evidence(database, raw_root, daily_raw_paths)
    production = produce_egx_decision_outcomes(
        observation_directory, outcome_directory, daily=loaded['daily'], sessions=sessions,
        assumptions=assumptions, now=now)
    rejected = sum(production['status_counts'][s] for s in REJECTED_ENTRY_STATUSES)
    return {
        'schema_version': CONTRACT_VERSION,
        'status': 'PRODUCED' if rejected == 0 else 'REJECTIONS_PRESENT',
        'calendar': {'from': calendar_from.isoformat(), 'through': calendar_through.isoformat(),
                     'days': len(sessions), 'sessions_sha256': _calendar_sha256(sessions),
                     'source': CALENDAR_SOURCE, 'retention': CALENDAR_RETENTION},
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


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise OutcomeJobError('CONFIG_INVALID', 'ISO calendar date required') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--db-path', '--raw-root', '--observation-dir', '--outcome-dir',
                 '--calendar-from', '--calendar-through', '--assumptions-version',
                 '--entry-slippage-bps', '--exit-slippage-bps', '--cost-bps-per-side'):
        parser.add_argument(name, required=True)
    parser.add_argument('--daily-raw-path', action='append', required=True)
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
            calendar_from=_date(args.calendar_from),
            calendar_through=_date(args.calendar_through),
            assumptions=assumptions)
    except OutcomeJobError as exc:
        result = _failure(exc.stage, exc)
    except CardReportJobError as exc:
        result = _failure('INPUT', exc)
    except DailyEvidenceLoadError as exc:
        result = _failure('DAILY_EVIDENCE', exc)
    except DecisionOutcomeProductionError as exc:
        result = _failure('PRODUCTION', exc)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    sys.exit(main())
