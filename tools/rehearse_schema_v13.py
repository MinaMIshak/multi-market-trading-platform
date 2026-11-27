"""Rehearse the schema-13 upgrade on a private COPY of a platform database.

Phase A host evidence harness (schema-13-rehearsal-v1). It never opens the
source database for writing: the source is read with ``mode=ro`` and
``query_only`` through SQLite's online backup API into a new private
directory, and only that copy is upgraded with the unchanged
``Database.initialize(allow_upgrade=True)``.

Checks recorded in ``<out>/REHEARSAL_REPORT.json`` (all must hold for PASS):

- copy ``PRAGMA integrity_check`` is ``ok`` before and after the upgrade;
- ``PRAGMA foreign_key_check`` is empty after the upgrade;
- the copy reaches the application ``SCHEMA_VERSION``;
- the ``canonical_instruments`` (source_provider, instrument_id) set, and so
  its row count, is unchanged by the upgrade;
- backfill reconciliation: for a pre-13 copy, the post-upgrade
  ``security_master_current_members`` (provider, instrument_id) set equals
  the pre-upgrade ``canonical_instruments`` (source_provider, instrument_id)
  set exactly (pre-13 snapshot writes deleted dropped rows);
- no duplicate dated ``egx-universe-v1`` reference artifacts;
- an exact retry (second ``initialize()``) changes neither the schema
  version, the current-member set nor the identity count;
- the source file's SHA-256 is identical before and after the run.

Use a quiescent source (for example a runtime snapshot ``platform.db``); a
live writer that checkpoints during the run makes the last check FAIL
truthfully. This tool writes no production state, performs no migration of
the source, creates no review, publication or market evidence, and is not
scheduled. PAPER/SHADOW only; LIVE_MONEY=false.

Exit codes: 0 PASS, 2 FAIL (report written), 1 refused/error (no report).
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

# The host action invokes this file directly, without an installed package.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.database import SCHEMA_VERSION, Database

CONTRACT = 'schema-13-rehearsal-v1'
REPORT_NAME = 'REHEARSAL_REPORT.json'
COPY_NAME = 'platform-rehearsal.db'
FK_SAMPLE = 20


class RehearsalError(ValueError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _tables(con):
    return {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _schema_version(con, tables):
    if 'schema_meta' not in tables:
        return None
    row = con.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    try:
        return None if row is None else int(row[0])
    except (TypeError, ValueError):
        return None


def _identities(con, tables):
    if 'canonical_instruments' not in tables:
        return None
    return {(row[0], row[1]) for row in con.execute(
        'SELECT source_provider, instrument_id FROM canonical_instruments')}


def _members(con, tables):
    if 'security_master_current_members' not in tables:
        return None
    return {(row[0], row[1]) for row in con.execute(
        'SELECT provider, instrument_id FROM security_master_current_members')}


def _duplicate_universe_dates(con, tables):
    if 'reference_artifacts' not in tables:
        return []
    return [{'effective_date': row[0], 'count': row[1]} for row in con.execute(
        "SELECT effective_date, COUNT(*) FROM reference_artifacts"
        " WHERE contract='egx-universe-v1'"
        " GROUP BY effective_date HAVING COUNT(*) > 1 ORDER BY effective_date")]


def _inspect(path):
    with closing(sqlite3.connect(path)) as con:
        tables = _tables(con)
        fk_rows = con.execute('PRAGMA foreign_key_check').fetchall()
        return {
            'integrity_check': con.execute('PRAGMA integrity_check').fetchone()[0],
            'schema_version': _schema_version(con, tables),
            'identities': _identities(con, tables),
            'members': _members(con, tables),
            'duplicate_universe_dates': _duplicate_universe_dates(con, tables),
            'foreign_key_violations': len(fk_rows),
            'foreign_key_sample': [list(row) for row in fk_rows[:FK_SAMPLE]],
        }


def _count(values):
    return None if values is None else len(values)


class _RehearsalDatabase(Database):
    """Own initialization connections without changing production transactions."""

    def __init__(self, path, connections):
        super().__init__(path)
        self._connections = connections

    def connect(self):
        connection = super().connect()
        self._connections.enter_context(closing(connection))
        return connection


def _upgrade(path, *, allow_upgrade):
    try:
        # initialize's connection context commits/rolls back but does not close.
        # Close even on failure before inspection or switching WAL to DELETE.
        with ExitStack() as connections:
            _RehearsalDatabase(path, connections).initialize(allow_upgrade=allow_upgrade)
    except (RuntimeError, sqlite3.Error) as exc:
        return {'status': 'FAILED', 'error': f'{type(exc).__name__}: {exc}'}
    return {'status': 'COMPLETED', 'error': None}


def _summary(state):
    return {
        'integrity_check': state['integrity_check'],
        'schema_version': state['schema_version'],
        'canonical_instruments': _count(state['identities']),
        'current_members': _count(state['members']),
        'duplicate_universe_dates': state['duplicate_universe_dates'],
        'foreign_key_violations': state['foreign_key_violations'],
        'foreign_key_sample': state['foreign_key_sample'],
    }


def _evaluate(pre, upgrade, post, retry, after_retry, source_before, source_after):
    checks = {
        'pre_integrity_ok': pre['integrity_check'] == 'ok',
        'upgrade_completed': upgrade['status'] == 'COMPLETED',
        'post_integrity_ok': post['integrity_check'] == 'ok',
        'post_foreign_keys_clean': post['foreign_key_violations'] == 0,
        'post_schema_current': post['schema_version'] == SCHEMA_VERSION,
        'identities_unchanged': (pre['identities'] is not None
                                 and post['identities'] == pre['identities']),
        'no_duplicate_dated_universes': (not pre['duplicate_universe_dates']
                                         and not post['duplicate_universe_dates']),
        'retry_completed': retry['status'] == 'COMPLETED',
        'retry_idempotent': (after_retry['schema_version'] == post['schema_version']
                             and after_retry['members'] == post['members']
                             and after_retry['identities'] == post['identities']),
        'source_unchanged': source_before == source_after,
    }
    pre_version = pre['schema_version']
    if pre_version is not None and pre_version < 13:
        expected, actual = pre['identities'] or set(), post['members'] or set()
        backfill = {
            'applies': True,
            'expected': len(expected),
            'retained': len(actual),
            'missing': len(expected - actual),
            'unexpected': len(actual - expected),
        }
        checks['backfill_exact'] = (post['members'] is not None
                                    and pre['identities'] is not None
                                    and actual == expected)
    else:
        # Schema 13+ membership is written by snapshot refreshes, not the
        # upgrade; only referential integrity (foreign keys) is checked.
        backfill = {'applies': False, 'expected': None, 'retained': _count(post['members']),
                    'missing': None, 'unexpected': None}
        checks['backfill_exact'] = pre_version is not None
    return checks, backfill


def rehearse(*, db, out, now=None):
    source = Path(db)
    if not source.is_absolute():
        raise RehearsalError('database path must be absolute')
    if source.is_symlink() or not source.is_file():
        raise RehearsalError('database must be an existing regular file, not a link')
    out = Path(out)
    if not out.is_absolute():
        raise RehearsalError('output path must be absolute')
    if out.exists() or out.is_symlink():
        raise RehearsalError('output already exists; rehearsals are never overwritten')
    if not out.parent.is_dir():
        raise RehearsalError('output parent directory must exist')
    started_at = (now or datetime.now(timezone.utc)).isoformat()
    source_before = _sha256(source)
    partial = out.parent / f'.{out.name}.partial-{uuid.uuid4().hex}'
    partial.mkdir(mode=0o700)
    try:
        copy = partial / COPY_NAME
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True,
                                     timeout=5)) as src:
            src.execute('PRAGMA query_only=ON')
            with closing(sqlite3.connect(copy)) as dst:
                src.backup(dst)  # one consistent read transaction
                dst.execute('PRAGMA journal_mode=DELETE')
        copy_sha256 = _sha256(copy)
        pre = _inspect(copy)
        upgrade = _upgrade(copy, allow_upgrade=True)
        post = _inspect(copy)
        retry = _upgrade(copy, allow_upgrade=False)
        after_retry = _inspect(copy)
        with closing(sqlite3.connect(copy)) as con:
            con.execute('PRAGMA journal_mode=DELETE')
        source_after = _sha256(source)
        checks, backfill = _evaluate(pre, upgrade, post, retry, after_retry,
                                     source_before, source_after)
        report = {
            'contract': CONTRACT,
            'status': 'PASS' if all(checks.values()) else 'FAIL',
            'failed_checks': sorted(name for name, ok in checks.items() if not ok),
            'checks': checks,
            'target': 'COPY_ONLY',
            'application_schema_version': SCHEMA_VERSION,
            'started_at': started_at,
            'source': {'path': str(source), 'open_mode': 'READ_ONLY_QUERY_ONLY',
                       'sha256_before': source_before, 'sha256_after': source_after},
            'copy': {'name': COPY_NAME, 'sha256_before_upgrade': copy_sha256,
                     'sha256_after': _sha256(copy)},
            'pre_upgrade': _summary(pre),
            'upgrade': upgrade,
            'post_upgrade': _summary(post),
            'retry': {**retry, 'state': _summary(after_retry)},
            'backfill_reconciliation': backfill,
            'not_covered': ['publisher retry/idempotency', 'source review creation',
                            'PIT refusal/success around independent review',
                            'source-authority/membership interpretation'],
            'production_db_mutated': False,
            'trading_effect': 'NONE',
            'mode': 'SHADOW',
            'live_money': False,
        }
        (partial / REPORT_NAME).write_text(
            json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        for item in partial.iterdir():
            item.chmod(0o444)
        os.rename(partial, out)
    except BaseException:
        for item in partial.iterdir():
            item.chmod(0o600)
            item.unlink()
        partial.rmdir()
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='absolute quiescent source platform.db')
    parser.add_argument('--out', required=True, help='absolute new rehearsal directory')
    args = parser.parse_args(argv)
    try:
        report = rehearse(db=args.db, out=args.out)
    except (RehearsalError, OSError, sqlite3.Error) as exc:
        print(f'rehearsal refused: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
