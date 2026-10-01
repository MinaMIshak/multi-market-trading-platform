"""Create a point-in-time, read-only runtime state snapshot for an isolated UI.

The UI must not read a live SQLite writer directly. This tool opens the source
database read-only (``mode=ro``, ``query_only``) and copies it with SQLite's
online backup API in a single step, i.e. one consistent read transaction:
committed rows only, never a torn page mix. Scheduler heartbeat and scan
history JSON files are copied byte-for-byte; they keep their original
timestamps, so an old heartbeat is reported STALE, never refreshed.

Layout (atomic rename of a private partial directory; never overwrites):

  <out>/platform.db               product DB, receipts, scan ledger
  <out>/scheduler-heartbeat.json  scheduler heartbeat (if supplied)
  <out>/egx-scan-history.json     scan history (if supplied)
  <out>/SNAPSHOT_MANIFEST.json    hashes, integrity check, counts, missing list

Readers select all of these with EGX_RUNTIME_STATE_DIR=<out>
(app/runtime_state.py), which also verifies the manifest hashes.

Nothing here fabricates evidence: absent inputs stay absent in the snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

MAX_JSON_BYTES = 1_048_576
COUNTED_TABLES = ('canonical_instruments', 'daily_canonical_artifacts',
                  'market_sessions', 'scheduled_jobs', 'audit_events')
SNAPSHOT_FILES = {'heartbeat': 'scheduler-heartbeat.json',
                  'scan_history': 'egx-scan-history.json',
                  'calendar_maintenance': 'calendar-maintenance-last-run.json',
                  'ranking': 'egx-ranking.json'}
# Per-file size caps; the ranking report grows with candidate lifecycles.
SIZE_LIMITS = {'ranking': 16 * 1024 * 1024}


class SnapshotError(ValueError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _source(path, name):
    path = Path(path)
    if not path.is_absolute():
        raise SnapshotError(f'{name} path must be absolute')
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(f'{name} must be an existing regular file, not a link')
    return path


def _copy_database(source, destination):
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True,
                                 timeout=5)) as src:
        src.execute('PRAGMA query_only=ON')
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)  # pages=-1: whole database in one read transaction
            dst.execute('PRAGMA journal_mode=DELETE')
            result = dst.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise SnapshotError('snapshot integrity check failed')
            tables = {row[0] for row in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {name: (dst.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                             if name in tables else None) for name in COUNTED_TABLES}
            receipts = (dst.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type='PAPER_SIGNAL_VERIFIED'"
            ).fetchone()[0] if 'audit_events' in tables else None)
    return {'integrity_check': result, 'table_counts': counts,
            'paper_signal_receipts': receipts}


def create_snapshot(*, db, out, heartbeat=None, scan_history=None, now=None,
                    build_revision=None, calendar_maintenance=None, ranking=None):
    db = _source(db, 'database')
    out = Path(out)
    if not out.is_absolute():
        raise SnapshotError('output path must be absolute')
    if out.exists() or out.is_symlink():
        raise SnapshotError('output already exists; snapshots are never overwritten')
    if not out.parent.is_dir():
        raise SnapshotError('output parent directory must exist')
    extras = {key: _source(value, key) for key, value in
              (('heartbeat', heartbeat), ('scan_history', scan_history),
               ('calendar_maintenance', calendar_maintenance), ('ranking', ranking)) if value}
    partial = out.parent / f'.{out.name}.partial-{uuid.uuid4().hex}'
    partial.mkdir(mode=0o700)
    try:
        target = partial / 'platform.db'
        database = _copy_database(db, target)
        manifest = {
            'schema_version': 2,
            'build_revision': build_revision or None,
            'taken_at': (now or datetime.now(timezone.utc)).isoformat(),
            'semantics': ('Point-in-time read-only copy of committed state. Not live '
                          'state; heartbeat/history keep original timestamps.'),
            'database': {'source': str(db), 'sha256': _sha256(target),
                         'byte_size': target.stat().st_size, **database},
            'files': {},
        }
        for key, source in extras.items():
            payload = source.read_bytes()
            if len(payload) > SIZE_LIMITS.get(key, MAX_JSON_BYTES):
                raise SnapshotError(f'{key} file too large')
            copied = partial / SNAPSHOT_FILES[key]
            copied.write_bytes(payload)
            manifest['files'][key] = {'source': str(source), 'name': copied.name,
                                      'sha256': hashlib.sha256(payload).hexdigest()}
        for key in SNAPSHOT_FILES:
            manifest['files'].setdefault(key, None)
        # Absent evidence stays absent; it is listed, never synthesized.
        manifest['missing'] = sorted(key for key, item in manifest['files'].items()
                                     if item is None)
        (partial / 'SNAPSHOT_MANIFEST.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        for item in partial.iterdir():
            item.chmod(0o444)
        os.rename(partial, out)
    except BaseException:
        for item in partial.iterdir():
            item.chmod(0o600)
            item.unlink()
        partial.rmdir()
        raise
    return manifest


def runtime_environment(out, manifest):
    # One variable selects every reader input (app/runtime_state.py). Do not
    # also set per-input variables, or SYSTEM reports MIXED_STATE_SOURCES.
    return {'EGX_RUNTIME_STATE_DIR': str(Path(out))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='absolute source platform.db')
    parser.add_argument('--out', required=True, help='absolute new snapshot directory')
    parser.add_argument('--heartbeat', help='absolute scheduler heartbeat JSON')
    parser.add_argument('--scan-history', help='absolute EGX scan history JSON')
    parser.add_argument('--calendar-maintenance-status',
                        help='absolute calendar maintenance last-run.json')
    parser.add_argument('--ranking-report', help='absolute EGX ranking report JSON')
    parser.add_argument('--build-revision', default=os.getenv('EGX_BUILD_REVISION'),
                        help='git revision of the reading application (recorded only)')
    args = parser.parse_args(argv)
    try:
        manifest = create_snapshot(db=args.db, out=args.out, heartbeat=args.heartbeat,
                                   scan_history=args.scan_history,
                                   calendar_maintenance=args.calendar_maintenance_status,
                                   ranking=args.ranking_report,
                                   build_revision=args.build_revision)
    except (SnapshotError, OSError, sqlite3.Error) as exc:
        print(f'snapshot failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'manifest': manifest,
                      'environment': runtime_environment(args.out, manifest)},
                     indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
