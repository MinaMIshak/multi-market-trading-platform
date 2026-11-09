"""Trusted host job: retain the EGX identity/calendar observations a card report needs.

    python -m app.scanner_v2_evidence_retention_job --db-path /abs/platform.db \
        --raw-root /abs/raw --session 2026-11-03 --through 2026-11-04 \
        --identity-raw-path <data_ingestions raw_path of the preserved EGID bytes>

``app.scanner_v2_card_report_job`` only *reads* retained evidence: the retained
EGID admission observation and a retained calendar observation named by its
digest. Until now nothing in the repository invoked the existing producers of
that evidence. This entrypoint only wires them; it adds no evidence store,
scanner, calendar or admission logic:

1. the EGID raw manifest and its exact bytes are resolved exactly as the
   card-report job does (``DataIngestionRepository.get_manifest_by_raw_path`` and
   ``ImmutableRawStore.read_verified``; provider ``egid``, SECURITY_MASTER,
   bounded);
2. the existing ``SecurityMasterRepository.retain_admission_observation`` is
   called for (``egid``, session, manifest SHA-256). It requires an already
   committed admission receipt for that exact triple; none is ever synthesized;
3. the existing ``TradingRepository.retain_calendar_observation`` is called for
   the explicit [session, through] window of committed ``market_sessions`` rows.

Both repositories sample their own post-read clock; no caller timestamp is
accepted, so nothing can be backdated. Exact retries keep the first retained
observation. Point in time is enforced downstream, unchanged: the card-report
loader rejects evidence observed after the ranking ``generated_at``, so this job
is only useful when it runs *before* ranking generation for the session.

The printed ``calendar_sha256`` is the value the card-report job's
``--calendar-sha256`` expects; the host passes it on explicitly, nothing is
searched for or defaulted. The database must already exist; it is never created,
initialized or upgraded. If the calendar step fails after the identity step,
the identity observation stays retained (it is an immutable true observation)
and an identical rerun is idempotent. Retained observations establish no
admission: identity stays internal-snapshot evidence and calendar admission
stays NOT_ESTABLISHED. There is no scheduler or cron entry in this repository.
Nothing is admitted, filled or promoted. LIVE_MONEY=false.
"""
import argparse
from datetime import date
import json
import re
import sqlite3
import sys

from app.scanner_v2_card_report_job import (
    CardReportJobError, _absolute, _existing_database, _identity)
from app.scanner_v2_retained_evidence import IDENTITY_PROVIDER
from app.storage import SecurityMasterRepository, TradingRepository

JOB_SCHEMA = 'scanner-v2-evidence-retention-job-v1'
RETENTION_REASONS = (
    'CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'IDENTITY_MANIFEST_UNAVAILABLE',
    'IDENTITY_MANIFEST_REJECTED', 'IDENTITY_ARTIFACT_UNREADABLE',
    'IDENTITY_ADMISSION_UNAVAILABLE', 'IDENTITY_OBSERVATION_REJECTED',
    'CALENDAR_OBSERVATION_REJECTED')
SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}
_ISO_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


class EvidenceRetentionJobError(RuntimeError):
    """Retention failed; ``reason`` is one of RETENTION_REASONS, ``stage`` names the step."""

    def __init__(self, stage, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.stage, self.reason, self.detail = stage, reason, detail


def _date(value):
    if type(value) is not str or _ISO_DATE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def run_egx_evidence_retention_job(*, db_path, raw_root, session, through, identity_raw_path):
    """Retain the EGID admission and calendar observations for one EGX session.

    ``session``/``through`` are ISO ``YYYY-MM-DD`` strings. Returns a
    ``scanner-v2-evidence-retention-job-v1`` result. Raises
    ``EvidenceRetentionJobError`` with stage INPUT, IDENTITY or CALENDAR.
    """
    session_day, through_day = _date(session), _date(through)
    if not (_absolute(db_path) and _absolute(raw_root) and session_day is not None
            and through_day is not None and type(identity_raw_path) is str
            and identity_raw_path and '\x00' not in identity_raw_path):
        raise EvidenceRetentionJobError('INPUT', 'CONFIG_INVALID')

    try:
        database = _existing_database(db_path)
        manifest, _ = _identity(database, raw_root, identity_raw_path)
    except CardReportJobError as exc:
        raise EvidenceRetentionJobError('INPUT', exc.reason, exc.detail) from None

    try:
        identity = SecurityMasterRepository(database).retain_admission_observation(
            provider=IDENTITY_PROVIDER, snapshot_date=session_day,
            artifact_sha256=manifest.sha256)
    except sqlite3.Error:
        raise EvidenceRetentionJobError('IDENTITY', 'DATABASE_UNAVAILABLE') from None
    except KeyError:
        raise EvidenceRetentionJobError('IDENTITY', 'IDENTITY_ADMISSION_UNAVAILABLE') from None
    except (ValueError, TypeError) as exc:
        raise EvidenceRetentionJobError(
            'IDENTITY', 'IDENTITY_OBSERVATION_REJECTED', str(exc)) from None

    try:
        calendar = TradingRepository(database).retain_calendar_observation(
            session=session_day, through=through_day)
    except sqlite3.Error:
        raise EvidenceRetentionJobError('CALENDAR', 'DATABASE_UNAVAILABLE') from None
    except (ValueError, TypeError) as exc:
        raise EvidenceRetentionJobError(
            'CALENDAR', 'CALENDAR_OBSERVATION_REJECTED', str(exc)) from None

    return {
        'schema_version': JOB_SCHEMA, 'status': 'RETAINED',
        'session': session_day.isoformat(),
        'identity_raw_path': manifest.raw_path,
        'identity_artifact_sha256': manifest.sha256,
        'identity_observed_at': identity['observed_at'],
        'identity_receipt_sha256': identity['receipt_sha256'],
        'calendar_sha256': calendar.artifact_sha256,
        'calendar_observed_at': calendar.observed_at.isoformat(),
        'calendar_next_session': calendar.next_session.isoformat(),
        'calendar_admission': calendar.admission,
        **SAFETY,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--db-path', '--raw-root', '--session', '--through', '--identity-raw-path'):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_egx_evidence_retention_job(
            db_path=args.db_path, raw_root=args.raw_root, session=args.session,
            through=args.through, identity_raw_path=args.identity_raw_path)
    except EvidenceRetentionJobError as exc:
        detail = None if exc.detail is None else str(exc.detail)[:200]
        result = {'schema_version': JOB_SCHEMA, 'status': 'FAILED', 'stage': exc.stage,
                  'reason': exc.reason, 'detail': detail, **SAFETY}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'RETAINED' else 1


if __name__ == '__main__':
    sys.exit(main())
