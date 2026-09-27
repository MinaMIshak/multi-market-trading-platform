"""Local last-run summary, explicitly not current readiness or universe evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from app.egx_scope import SCAN_STATUSES, valid_scope_symbol


def write_scan_history(report, path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('scan history path must be absolute')
    summary = {key: report[key] for key in (
        'market', 'scope_reference', 'scope_kind', 'requested', 'scanned',
        'status_counts', 'live_money')}
    summary.update(schema_version=2, completed_at=datetime.now(timezone.utc).isoformat(),
                   symbols=[{key: row[key] for key in ('symbol', 'status', 'scanned', 'reason')}
                            for row in report['symbols']])
    if not _valid_summary(summary):
        raise ValueError('invalid EGX scan history summary')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(summary, stream)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _valid_summary(raw):
    """Share admission between persistence and operator-visible history."""
    try:
        completed = datetime.fromisoformat(raw['completed_at'])
        rows = raw['symbols']
        statuses = SCAN_STATUSES if raw['schema_version'] == 2 else SCAN_STATUSES[:3]
        if (type(raw['schema_version']) is not int or raw['schema_version'] not in (1, 2)
                or raw['market'] != 'EGX'
                or raw['live_money'] is not False
                or raw['scope_kind'] != 'EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE'
                or not isinstance(raw['scope_reference'], str) or not raw['scope_reference'].strip()
                or completed.tzinfo is None or completed > datetime.now(timezone.utc)
                or not isinstance(rows, list) or not rows
                or any(not valid_scope_symbol(row['symbol'])
                       or row['status'] not in statuses or not isinstance(row['reason'], str)
                       or row['scanned'] is not (row['status'] in statuses[:2]) for row in rows)
                or len({row['symbol'] for row in rows}) != len(rows)
                or type(raw['requested']) is not int or raw['requested'] != len(rows)
                or type(raw['scanned']) is not int
                or raw['scanned'] != sum(row['scanned'] for row in rows)
                or not isinstance(raw['status_counts'], dict)
                or any(type(raw['status_counts'].get(s)) is not int for s in statuses)
                or raw['status_counts'] != {s: sum(row['status'] == s for row in rows) for s in statuses}):
            return False
        return True
    except (ValueError, KeyError, TypeError):
        return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate history field')
        result[key] = value
    return result


def load_scan_history():
    unknown = {'status': 'UNKNOWN', 'reason': 'No valid EGX scan history connected'}
    configured = os.getenv('EGX_SCAN_HISTORY_PATH')
    if not configured or not Path(configured).is_absolute():
        return unknown
    try:
        path = Path(configured)
        if any(part.is_symlink() for part in (path, *path.parents)):
            return unknown
        # Bound the operator-facing read even when configuration points at a bad file.
        with path.open('rb') as stream:
            payload = stream.read(1_048_577)
        if len(payload) > 1_048_576:
            return unknown
        raw = json.loads(payload, object_pairs_hook=_unique_object)
        if not _valid_summary(raw):
            return unknown
        return {'status': 'HISTORICAL_RUN',
                'reason': 'Last completed explicit-scope run; not current readiness, fills or universe coverage',
                'run': raw}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return unknown
