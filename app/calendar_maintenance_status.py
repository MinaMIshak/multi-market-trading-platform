"""Last outcome of the EGX official-index/calendar maintenance job, for SYSTEM.

Reads the job's ``last-run.json`` (resolved as runtime input
``calendar_maintenance``). It reports only what the record states. A run is
CURRENT only when it SUCCEEDED within MAX_AGE; an older success is STALE; a
missing, unreadable or malformed record is UNKNOWN. Calendar evidence is not
a scan, a candidate or source admission.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.runtime_state import resolved_path

MAX_BYTES = 64 * 1024
MAX_AGE = timedelta(hours=30)
STATUSES = ('SUCCESS', 'FAILED', 'LOCKED')
FIELDS = ('outcome', 'error', 'last_completed_session_date', 'newest_official_bar_before',
          'fetch_range', 'snapshot_date', 'verified_sessions_in_window', 'build_revision')


def load_calendar_maintenance_status(*, now=None):
    unknown = {'status': 'UNKNOWN', 'freshness': 'UNKNOWN',
               'reason': 'No calendar maintenance record connected'}
    configured = resolved_path('calendar_maintenance')
    if not configured or not Path(configured).is_absolute():
        return unknown
    try:
        with Path(configured).open('rb') as stream:
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            return {**unknown, 'reason': 'Calendar maintenance record too large'}
        raw = json.loads(payload)
        if (type(raw) is not dict or raw.get('job') != 'egx_official_calendar_maintenance'
                or raw.get('status') not in STATUSES or raw.get('live_money') is not False):
            return {**unknown, 'reason': 'Calendar maintenance record malformed'}
        finished = datetime.fromisoformat(raw['finished_at'])
        now = now or datetime.now(timezone.utc)
        if finished.tzinfo is None or finished > now:
            return {**unknown, 'reason': 'Calendar maintenance time invalid'}
    except (OSError, ValueError, KeyError, TypeError):
        return {**unknown, 'reason': 'Calendar maintenance record unreadable'}
    if raw['status'] != 'SUCCESS':
        freshness = 'FAILED' if raw['status'] == 'FAILED' else 'UNKNOWN'
    else:
        freshness = 'CURRENT' if now - finished <= MAX_AGE else 'STALE'
    return {'status': raw['status'], 'freshness': freshness, 'finished_at': raw['finished_at'],
            **{key: raw.get(key) for key in FIELDS}}
