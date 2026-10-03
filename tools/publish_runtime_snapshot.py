"""Publish a verified runtime-state snapshot by switching an atomic pointer file.

    python tools/publish_runtime_snapshot.py publish --db /abs/platform.db \
        --snapshots-root /abs/snapshots --pointer /abs/current-bundle.json \
        [--heartbeat F] [--scan-history F] [--calendar-maintenance-status F] \
        [--build-revision SHA]
    python tools/publish_runtime_snapshot.py rollback --pointer /abs/current-bundle.json

A runtime started with ``EGX_RUNTIME_STATE_POINTER=<pointer>`` (and no
``EGX_RUNTIME_STATE_DIR``) serves whatever bundle the pointer names. It
re-reads the pointer when the file changes, so a refresh needs no restart.

publish takes a new snapshot (tools/runtime_state_snapshot.py: online backup,
integrity check, hashes, read-only, never overwritten), re-verifies it the way
the runtime does, and switches the pointer only when the snapshot is VERIFIED
and the database holds instruments. Otherwise the pointer is left untouched
and the command exits 1. Optional inputs that do not exist are omitted, so
the manifest lists them as missing. Every switch is appended to
``<pointer>.history.jsonl``. rollback points back to the previous bundle,
which must still verify. Nothing here edits a database, a process or nginx.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from app.runtime_state import _verify_bundle  # noqa: E402
from tools.runtime_state_snapshot import SnapshotError, create_snapshot  # noqa: E402


class PublishError(RuntimeError):
    pass


def _history_path(pointer):
    return pointer.with_name(pointer.name + '.history.jsonl')


def _write_pointer(pointer, document):
    temporary = pointer.with_name(pointer.name + '.tmp')
    temporary.write_text(json.dumps(document, sort_keys=True) + '\n')
    os.replace(temporary, pointer)
    with _history_path(pointer).open('a') as history:
        history.write(json.dumps(document, sort_keys=True) + '\n')


def _require_publishable(bundle):
    verified = _verify_bundle(bundle)
    if verified['status'] != 'VERIFIED':
        raise PublishError(f"snapshot not VERIFIED: {verified['status']}")
    manifest = json.loads((bundle / 'SNAPSHOT_MANIFEST.json').read_text())
    instruments = manifest['database'].get('table_counts', {}).get('canonical_instruments')
    if not instruments:
        raise PublishError('snapshot holds no canonical instruments')
    return verified


def publish(*, db, snapshots_root, pointer, build_revision=None, heartbeat=None,
            scan_history=None, calendar_maintenance=None, ranking=None, macro=None, context=None,
            us_ranking=None, now=None):
    for label, path in (('snapshots root', snapshots_root), ('pointer', pointer)):
        if not Path(path).is_absolute():
            raise PublishError(f'{label} must be absolute')
    snapshots_root, pointer = Path(snapshots_root), Path(pointer)
    if not snapshots_root.is_dir() or pointer.is_symlink():
        raise PublishError('snapshots root must exist and the pointer must not be a link')
    now = now or datetime.now(timezone.utc)
    out = snapshots_root / ('published-' + now.strftime('%Y%m%dT%H%M%SZ'))
    optional = {name: value for name, value in (
        ('heartbeat', heartbeat), ('scan_history', scan_history),
        ('calendar_maintenance', calendar_maintenance), ('ranking', ranking), ('macro', macro),
        ('context', context), ('us_ranking', us_ranking))
        if value and Path(value).is_file()}
    try:
        create_snapshot(db=db, out=str(out), build_revision=build_revision, now=now, **optional)
    except (SnapshotError, OSError, sqlite3.Error) as exc:
        raise PublishError(f'snapshot failed: {exc}') from exc
    verified = _require_publishable(out)
    document = {'bundle': str(out), 'published_at': now.isoformat(),
                'build_revision': build_revision or None, 'action': 'publish',
                'missing': verified['missing']}
    _write_pointer(pointer, document)
    return document


def rollback(*, pointer):
    pointer = Path(pointer)
    history = _history_path(pointer)
    if not pointer.is_absolute() or not history.is_file():
        raise PublishError('no publication history to roll back')
    entries = [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
    current = json.loads(pointer.read_text())['bundle'] if pointer.is_file() else None
    previous = next((entry for entry in reversed(entries) if entry['bundle'] != current), None)
    if previous is None:
        raise PublishError('no earlier bundle to roll back to')
    _require_publishable(Path(previous['bundle']))
    document = {**previous, 'action': 'rollback',
                'published_at': datetime.now(timezone.utc).isoformat()}
    _write_pointer(pointer, document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('publish')
    make.add_argument('--db', required=True)
    make.add_argument('--snapshots-root', required=True)
    make.add_argument('--pointer', required=True)
    make.add_argument('--build-revision', default=os.getenv('EGX_BUILD_REVISION'))
    make.add_argument('--heartbeat')
    make.add_argument('--scan-history')
    make.add_argument('--calendar-maintenance-status')
    make.add_argument('--ranking-report')
    make.add_argument('--macro-report')
    make.add_argument('--context-report')
    make.add_argument('--us-ranking-report')
    back = commands.add_parser('rollback')
    back.add_argument('--pointer', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'publish':
            document = publish(db=args.db, snapshots_root=args.snapshots_root,
                               pointer=args.pointer, build_revision=args.build_revision,
                               heartbeat=args.heartbeat, scan_history=args.scan_history,
                               calendar_maintenance=args.calendar_maintenance_status,
                               ranking=args.ranking_report, macro=args.macro_report,
                               context=args.context_report, us_ranking=args.us_ranking_report)
        else:
            document = rollback(pointer=args.pointer)
    except PublishError as exc:
        print(json.dumps({'status': 'NOT_PUBLISHED', 'reason': str(exc)}))
        return 1
    print(json.dumps({'status': 'PUBLISHED', **document}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
