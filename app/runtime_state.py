"""Single resolution point for runtime-state inputs read by the API/UI.

Each reader historically took its own variable, so a runtime could read
identities from one database and receipts from another (or none) without
noticing. Resolution order per input:

1. explicit variable (e.g. ``EGX_DB_PATH``) -- preserved for compatibility;
2. ``EGX_RUNTIME_STATE_DIR`` bundle file (see tools/runtime_state_snapshot.py);
3. the reader's legacy default (only the product DB has one).

``runtime_state_report`` records where each input came from, whether explicit
overrides split state across sources, and verifies a bundle manifest. It never
creates, repairs or refreshes state.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

BUNDLE_VARIABLE = 'EGX_RUNTIME_STATE_DIR'
MANIFEST_NAME = 'SNAPSHOT_MANIFEST.json'
MAX_MANIFEST_BYTES = 256 * 1024
DEFAULT_DB_PATH = '/app/data/platform.db'

# input -> (explicit variable, bundle-relative name, legacy default)
INPUTS = {
    'database': ('EGX_DB_PATH', 'platform.db', DEFAULT_DB_PATH),
    'receipts': ('EGX_PAPER_RUNTIME', '.', None),
    'heartbeat': ('EGX_SCHEDULER_HEARTBEAT_PATH', 'scheduler-heartbeat.json', None),
    'scan_history': ('EGX_SCAN_HISTORY_PATH', 'egx-scan-history.json', None),
    'scan_ledger': ('EGX_SCAN_LEDGER_PATH', 'platform.db', None),
    'calendar_maintenance': ('EGX_CALENDAR_MAINTENANCE_STATUS_PATH',
                             'calendar-maintenance-last-run.json', None),
}


def _bundle():
    value = os.getenv(BUNDLE_VARIABLE)
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def resolve(name):
    """Return (path or None, origin) for one input; origin is explicit/bundle/default/unset."""
    variable, relative, default = INPUTS[name]
    explicit = os.getenv(variable)
    if explicit:
        return explicit, 'explicit'
    bundle = _bundle()
    if bundle is not None:
        candidate = bundle if relative == '.' else bundle / relative
        return str(candidate), 'bundle'
    if default is not None:
        return default, 'default'
    return None, 'unset'


def resolved_path(name):
    return resolve(name)[0]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=16)
def _hash_cached(path, size, mtime_ns):
    return _sha256(Path(path))


def _verify_bundle(bundle):
    """Check manifest hashes against bundle files; never trusts an unverifiable claim."""
    result = {'path': str(bundle), 'status': 'UNAVAILABLE', 'taken_at': None,
              'build_revision': None, 'integrity_check': None,
              'included': [], 'missing': [], 'mismatched': []}
    manifest_path = bundle / MANIFEST_NAME
    try:
        if bundle.is_symlink() or manifest_path.is_symlink():
            raise ValueError('linked bundle')
        with manifest_path.open('rb') as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError('manifest too large')
        manifest = json.loads(payload)
        if type(manifest) is not dict or manifest.get('schema_version') not in (1, 2):
            raise ValueError('unsupported manifest')
        expected = {'platform.db': manifest['database']['sha256']}
        for item in (manifest.get('files') or {}).values():
            if item:
                expected[item['name']] = item['sha256']
        for name, digest in sorted(expected.items()):
            path = bundle / name
            if path.is_symlink() or not path.is_file():
                result['missing'].append(name)
                continue
            stat = path.stat()
            if _hash_cached(str(path), stat.st_size, stat.st_mtime_ns) != digest:
                result['mismatched'].append(name)
            else:
                result['included'].append(name)
        declared_missing = [key for key, item in (manifest.get('files') or {}).items()
                            if item is None]
        result['missing'] += declared_missing
        result.update(taken_at=manifest.get('taken_at'),
                      build_revision=manifest.get('build_revision'),
                      integrity_check=manifest['database'].get('integrity_check'))
        verified = (not result['mismatched'] and 'platform.db' in result['included']
                    and result['integrity_check'] == 'ok')
        result['status'] = 'VERIFIED' if verified else 'INVALID'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


def startup_event(build_revision=None):
    """Structured, secret-free startup record: origins only, never paths or values."""
    report = runtime_state_report()
    snapshot = report['snapshot']
    return {'event': 'api_startup', 'build_revision': build_revision or None,
            'runtime_state_mode': report['mode'],
            'input_origins': {name: item['origin'] for name, item in report['inputs'].items()},
            'snapshot_status': snapshot['status'] if snapshot else None,
            'warnings': report['warnings'], 'live_money': 'DISABLED'}


def runtime_state_report():
    inputs = {}
    for name, (variable, _, _) in INPUTS.items():
        path, origin = resolve(name)
        inputs[name] = {'variable': variable, 'origin': origin, 'path': path}
    bundle = _bundle()
    warnings = []
    if os.getenv(BUNDLE_VARIABLE) and bundle is None:
        warnings.append('RUNTIME_STATE_DIR_NOT_ABSOLUTE')
    if bundle is not None and any(item['origin'] == 'explicit' for item in inputs.values()):
        warnings.append('MIXED_STATE_SOURCES')
    if bundle is None:
        explicit_dbs = {inputs[name]['path'] for name in ('database', 'scan_ledger')
                        if inputs[name]['origin'] == 'explicit'}
        receipts = inputs['receipts']['path']
        if receipts:
            explicit_dbs.add(str(Path(receipts) / 'platform.db'))
        if len(explicit_dbs) > 1:
            warnings.append('MIXED_STATE_SOURCES')
    snapshot = _verify_bundle(bundle) if bundle is not None else None
    if snapshot is not None and snapshot['status'] != 'VERIFIED':
        warnings.append('SNAPSHOT_UNVERIFIED')
    return {'mode': 'SNAPSHOT_BUNDLE' if bundle is not None else 'PER_VARIABLE',
            'inputs': inputs, 'snapshot': snapshot, 'warnings': warnings}
