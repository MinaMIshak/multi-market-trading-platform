"""Local last-run summary, explicitly not current readiness or universe evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


def write_scan_history(report, path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('scan history path must be absolute')
    summary = {key: report[key] for key in (
        'market', 'scope_reference', 'scope_kind', 'requested', 'scanned',
        'status_counts', 'live_money')}
    summary.update(schema_version=1, completed_at=datetime.now(timezone.utc).isoformat(),
                   symbols=[{key: row[key] for key in ('symbol', 'status', 'scanned', 'reason')}
                            for row in report['symbols']])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(summary, stream)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_scan_history():
    unknown = {'status': 'UNKNOWN', 'reason': 'No valid EGX scan history connected'}
    configured = os.getenv('EGX_SCAN_HISTORY_PATH')
    if not configured or not Path(configured).is_absolute():
        return unknown
    try:
        raw = json.loads(Path(configured).read_text())
        completed = datetime.fromisoformat(raw['completed_at'])
        rows = raw['symbols']
        statuses = ('WATCH', 'READY_NO_SIGNAL', 'EVIDENCE_BLOCKED')
        if (raw['schema_version'] != 1 or raw['market'] != 'EGX'
                or raw['live_money'] is not False
                or raw['scope_kind'] != 'EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE'
                or not isinstance(raw['scope_reference'], str) or not raw['scope_reference'].strip()
                or completed.tzinfo is None or completed > datetime.now(timezone.utc)
                or not isinstance(rows, list) or not rows
                or any(not isinstance(row['symbol'], str) or not row['symbol'].strip()
                       or row['status'] not in statuses or not isinstance(row['reason'], str)
                       or row['scanned'] is not (row['status'] in statuses[:2]) for row in rows)
                or len({row['symbol'] for row in rows}) != len(rows)
                or type(raw['requested']) is not int or raw['requested'] != len(rows)
                or type(raw['scanned']) is not int
                or raw['scanned'] != sum(row['scanned'] for row in rows)
                or raw['status_counts'] != {s: sum(row['status'] == s for row in rows) for s in statuses}):
            return unknown
        return {'status': 'HISTORICAL_RUN',
                'reason': 'Last completed explicit-scope run; not current readiness, fills or universe coverage',
                'run': raw}
    except (OSError, ValueError, KeyError, TypeError):
        return unknown
