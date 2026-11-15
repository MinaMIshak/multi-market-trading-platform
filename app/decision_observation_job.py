"""Trusted host job: retained EGX ranking -> write-once decision observations.

    python -m app.decision_observation_job --db-path /abs/platform.db \
        --raw-root /abs/raw --ranking-report /abs/egx-ranking.json \
        --ranking-sha256 <sha256 from the verified snapshot manifest> \
        --identity-raw-path <data_ingestions raw_path of the preserved EGID bytes> \
        --calendar-sha256 <retained calendar observation digest> \
        --output-dir /abs/decision-observations

Phase G collection entrypoint. It only wires existing pieces together; it adds
no evidence, binding, record, store or producer logic:

1. the configuration is checked exactly as ``app.scanner_v2_card_report_job``
   checks it, and the EGID manifest/bytes, the ranking bytes and the database
   are read with that job's unchanged helpers (existing regular database file,
   never created/initialized/upgraded; verified raw bytes; bounded no-follow
   ranking read);
2. the unchanged ``load_retained_egx_rank_binding`` builds the retained binding
   at the ranking generation time, with the same arguments the card-report job
   passes, so both jobs bind the same retained evidence;
3. the unchanged ``produce_egx_decision_observations`` re-derives the decisions
   and retains one NOT_ATTACHED observation per open-window decision with its
   own producer clock as ``recorded_at``.

The caller supplies every path and digest; nothing is guessed, searched for or
defaulted. Status PRODUCED means every bound decision was RETAINED or
ALREADY_RETAINED (a ranking with no candidates is PRODUCED with nothing
retained). WINDOW_CLOSED_PRESENT means the run completed but at least one
decision's recording window had already closed, so it was reported and not
retained; the CLI exits 2 so a late run is visible to the host rather than
silently shrinking the population. Failures exit 1 with one fixed stage
(INPUT, EVIDENCE, PRODUCTION) and reason; write-once records retained earlier
in a failed run stay, and an identical rerun inside the window is idempotent.

There is no scheduler or cron entry in this repository: SYSTEM keeps the
empirical ``scheduled`` stage NOT_SCHEDULED_IN_REPOSITORY and the gap
DECISION_OBSERVATION_JOB_NOT_SCHEDULED; the output directory must be
provisioned by a host. Records carry no outcome; performance_claim NONE;
nothing is admitted, filled or promoted; SHADOW; LIVE_MONEY=false.
"""
import argparse
import json
import sqlite3
import sys

from app.decision_observation_producer import (
    DecisionObservationProductionError, produce_egx_decision_observations)
from app.scanner_v2_card_report_job import (
    CardReportJobError, _absolute, _digest, _existing_database, _identity, _read_ranking)
from app.scanner_v2_retained_evidence import (
    RetainedEvidenceError, load_retained_egx_rank_binding)
from app.storage import SecurityMasterRepository, TradingRepository

CONTRACT_VERSION = 'decision-observation-job-v1'
JOB_STATUSES = ('PRODUCED', 'WINDOW_CLOSED_PRESENT', 'FAILED')
FAILURE_STAGES = ('INPUT', 'EVIDENCE', 'PRODUCTION')
EXIT_CODES = {'PRODUCED': 0, 'FAILED': 1, 'WINDOW_CLOSED_PRESENT': 2}
SAFETY = {'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


def run_egx_decision_observation_job(*, db_path, raw_root, ranking_path, ranking_sha256,
                                     identity_raw_path, calendar_sha256, directory,
                                     now=None):
    """Load retained evidence for one ranking and retain its decision observations.

    Returns a ``decision-observation-job-v1`` result wrapping the producer's
    result. Raises ``CardReportJobError`` (inputs), ``RetainedEvidenceError``
    (evidence) or ``DecisionObservationProductionError`` (production). ``now``
    is passed to the producer as its clock (default real UTC).
    """
    if not (all(_absolute(v) for v in (db_path, raw_root, ranking_path, directory))
            and _digest(ranking_sha256) and _digest(calendar_sha256)
            and type(identity_raw_path) is str and identity_raw_path
            and '\x00' not in identity_raw_path):
        raise CardReportJobError('CONFIG_INVALID')

    database = _existing_database(db_path)
    manifest, identity_bytes = _identity(database, raw_root, identity_raw_path)
    artifact = _read_ranking(ranking_path)
    try:
        binding = load_retained_egx_rank_binding(
            artifact, expected_sha256=ranking_sha256, identity_artifact=identity_bytes,
            identity_manifest=manifest, security_master=SecurityMasterRepository(database),
            trading=TradingRepository(database), calendar_sha256=calendar_sha256)
    except sqlite3.Error:
        raise CardReportJobError('DATABASE_UNAVAILABLE') from None
    production = produce_egx_decision_observations(
        directory, artifact, expected_sha256=ranking_sha256, evidence=binding, now=now)
    closed = production['status_counts']['WINDOW_CLOSED']
    return {'schema_version': CONTRACT_VERSION,
            'status': 'PRODUCED' if closed == 0 else 'WINDOW_CLOSED_PRESENT',
            'ranking_sha256': ranking_sha256, 'identity_artifact_sha256': manifest.sha256,
            'calendar_sha256': calendar_sha256, 'production': production, **SAFETY}


def _failure(stage, exc):
    detail = None if exc.detail is None else str(exc.detail)[:200]
    return {'schema_version': CONTRACT_VERSION, 'status': 'FAILED', 'stage': stage,
            'reason': exc.reason, 'detail': detail, **SAFETY}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--db-path', '--raw-root', '--ranking-report', '--ranking-sha256',
                 '--identity-raw-path', '--calendar-sha256', '--output-dir'):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_egx_decision_observation_job(
            db_path=args.db_path, raw_root=args.raw_root, ranking_path=args.ranking_report,
            ranking_sha256=args.ranking_sha256, identity_raw_path=args.identity_raw_path,
            calendar_sha256=args.calendar_sha256, directory=args.output_dir)
    except CardReportJobError as exc:
        result = _failure('INPUT', exc)
    except RetainedEvidenceError as exc:
        result = _failure('EVIDENCE', exc)
    except DecisionObservationProductionError as exc:
        result = _failure('PRODUCTION', exc)
    print(json.dumps(result, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    sys.exit(main())
