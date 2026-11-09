"""Trusted host job: retained evidence -> published Scanner v2 EGX card report.

    python -m app.scanner_v2_card_report_job --db-path /abs/platform.db \
        --raw-root /abs/raw --ranking-report /abs/egx-ranking.json \
        --ranking-sha256 <sha256 from the verified snapshot manifest> \
        --identity-raw-path <data_ingestions raw_path of the preserved EGID bytes> \
        --calendar-sha256 <retained calendar observation digest> \
        --output-dir /abs/scanner-v2-card-reports

This entrypoint only wires existing pieces together; it adds no evidence store,
scanner, risk or admission logic:

1. the EGID raw manifest is looked up with the existing
   ``DataIngestionRepository.get_manifest_by_raw_path`` and its exact bytes are
   read with ``ImmutableRawStore.read_verified`` (size and SHA-256 checked);
2. the ranking bytes are read from one absolute path without following a final
   symlink, regular files only, bounded;
3. ``load_retained_egx_rank_binding`` builds the retained binding from read-only
   repository methods at the ranking generation time;
4. ``publish_egx_card_report`` projects, gates and publishes with its own clock.

The caller supplies the digests (ranking SHA-256 from its verified snapshot
manifest, retained calendar digest); nothing is guessed, searched for or
defaulted. The database file must already exist; it is never created, initialized
or upgraded, and only read methods are called. Failures raise with one fixed
reason per stage and leave the current publication unchanged unless the
publisher's own atomic steps already completed. There is no scheduler or cron
entry in this repository, so ``feature_status()`` is unchanged: a host must
provision the output directory and schedule this job, and only host evidence may
later update the runtime producer status. Nothing is admitted, filled or
promoted. LIVE_MONEY=false.
"""
import argparse
import json
import os
import re
import sqlite3
import stat
import sys

from app.data.ingestion_repository import DataIngestionRepository
from app.data.models import DataAssetType
from app.data.raw_store import ImmutableRawStore
from app.scanner_v2_card_report_publisher import (
    CardReportPublicationError, publish_egx_card_report)
from app.scanner_v2_identity import MAX_BYTES
from app.scanner_v2_retained_evidence import (
    IDENTITY_PROVIDER, RetainedEvidenceError, load_retained_egx_rank_binding)
from app.storage import Database, SecurityMasterRepository, TradingRepository

JOB_SCHEMA = 'scanner-v2-card-report-job-v1'
JOB_REASONS = (
    'CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'RANKING_UNREADABLE',
    'IDENTITY_MANIFEST_UNAVAILABLE', 'IDENTITY_MANIFEST_REJECTED',
    'IDENTITY_ARTIFACT_UNREADABLE')
SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}
_HEX64 = re.compile(r'[0-9a-f]{64}')
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)


class CardReportJobError(RuntimeError):
    """Job input unusable; ``reason`` is one of JOB_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _absolute(value):
    return type(value) is str and os.path.isabs(value) and '\x00' not in value


def _digest(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _read_ranking(path):
    """Exact bytes of a regular, non-symlink file, at most ``MAX_BYTES + 1``."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        raise CardReportJobError('RANKING_UNREADABLE') from None
    try:
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise CardReportJobError('RANKING_UNREADABLE', 'regular file required')
            # Oversize is rejected by ranking_scope (RANKING_INVALID).
            return source.read(MAX_BYTES + 1)
    except OSError:
        raise CardReportJobError('RANKING_UNREADABLE') from None


def _existing_database(path):
    try:
        info = os.lstat(path)
    except OSError:
        raise CardReportJobError('DATABASE_UNAVAILABLE') from None
    if not stat.S_ISREG(info.st_mode):
        raise CardReportJobError('DATABASE_UNAVAILABLE', 'regular file required')
    # Never initialize/upgrade: the job reads retained evidence only.
    return Database(path)


def _identity(database, raw_root, raw_path):
    try:
        manifest = DataIngestionRepository(database).get_manifest_by_raw_path(raw_path)
    except sqlite3.Error:
        raise CardReportJobError('DATABASE_UNAVAILABLE') from None
    except (ValueError, TypeError) as exc:
        raise CardReportJobError('IDENTITY_MANIFEST_REJECTED', type(exc).__name__) from None
    if manifest is None:
        raise CardReportJobError('IDENTITY_MANIFEST_UNAVAILABLE')
    if (manifest.provider != IDENTITY_PROVIDER
            or manifest.asset_type != DataAssetType.SECURITY_MASTER
            or manifest.byte_size > MAX_BYTES):
        raise CardReportJobError('IDENTITY_MANIFEST_REJECTED')
    try:
        payload = ImmutableRawStore(raw_root).read_verified(manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        raise CardReportJobError('IDENTITY_ARTIFACT_UNREADABLE', type(exc).__name__) from None
    return manifest, payload


def run_egx_card_report_job(*, db_path, raw_root, ranking_path, ranking_sha256,
                            identity_raw_path, calendar_sha256, directory, now=None):
    """Load retained evidence for one ranking and publish its card report.

    Returns a ``scanner-v2-card-report-job-v1`` result wrapping the publisher's
    result. Raises ``CardReportJobError`` (inputs), ``RetainedEvidenceError``
    (evidence) or ``CardReportPublicationError`` (publication). ``now`` is passed
    to the publisher as its producer clock (default real UTC).
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
    publication = publish_egx_card_report(
        directory, artifact, expected_sha256=ranking_sha256, evidence=binding, now=now)
    return {'schema_version': JOB_SCHEMA, 'status': publication['status'],
            'ranking_sha256': ranking_sha256, 'identity_artifact_sha256': manifest.sha256,
            'calendar_sha256': calendar_sha256, 'publication': publication, **SAFETY}


def _failure(stage, exc):
    detail = None if exc.detail is None else str(exc.detail)[:200]
    return {'schema_version': JOB_SCHEMA, 'status': 'FAILED', 'stage': stage,
            'reason': exc.reason, 'detail': detail, **SAFETY}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('--db-path', '--raw-root', '--ranking-report', '--ranking-sha256',
                 '--identity-raw-path', '--calendar-sha256', '--output-dir'):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_egx_card_report_job(
            db_path=args.db_path, raw_root=args.raw_root, ranking_path=args.ranking_report,
            ranking_sha256=args.ranking_sha256, identity_raw_path=args.identity_raw_path,
            calendar_sha256=args.calendar_sha256, directory=args.output_dir)
    except CardReportJobError as exc:
        result = _failure('INPUT', exc)
    except RetainedEvidenceError as exc:
        result = _failure('EVIDENCE', exc)
    except CardReportPublicationError as exc:
        result = _failure('PUBLICATION', exc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('PUBLISHED', 'ALREADY_CURRENT') else 1


if __name__ == '__main__':
    sys.exit(main())
