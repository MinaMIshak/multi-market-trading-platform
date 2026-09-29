"""Optional local scheduler liveness evidence, never proof of scan success."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

from app.runtime_state import resolved_path

MAX_HEARTBEAT_BYTES = 64 * 1024


def write_heartbeat(*, mode, poll_seconds, now=None):
    configured = os.getenv('EGX_SCHEDULER_HEARTBEAT_PATH')
    if not configured:
        return
    path = Path(configured)
    if not path.is_absolute():
        raise ValueError('heartbeat path must be absolute')
    if mode not in ('observe', 'paper_refresh'):
        raise ValueError('unsupported heartbeat mode')
    if type(poll_seconds) is not int or poll_seconds <= 0:
        raise ValueError('heartbeat poll interval must be a positive integer')
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('heartbeat timestamp must be timezone aware')
    payload = {'schema_version': 1, 'market': 'EGX', 'mode': mode,
               'observed_at': now.isoformat(),
               'valid_until': (now + timedelta(seconds=max(60, poll_seconds * 3))).isoformat()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(payload, stream)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_heartbeat(*, now=None):
    unknown = {'status': 'UNKNOWN', 'reason': 'No valid scheduler heartbeat connected'}
    configured = resolved_path('heartbeat')
    if not configured or not Path(configured).is_absolute():
        return unknown
    try:
        # Bound the operator-facing read; a valid heartbeat is well under this.
        with Path(configured).open('rb') as stream:
            payload = stream.read(MAX_HEARTBEAT_BYTES + 1)
        if len(payload) > MAX_HEARTBEAT_BYTES:
            return unknown
        raw = json.loads(payload)
        if (type(raw['schema_version']) is not int or raw['schema_version'] != 1
                or raw['market'] != 'EGX'
                or raw['mode'] not in ('observe', 'paper_refresh')):
            return unknown
        observed = datetime.fromisoformat(raw['observed_at'])
        expires = datetime.fromisoformat(raw['valid_until'])
        now = now or datetime.now(timezone.utc)
        if observed.tzinfo is None or expires.tzinfo is None or not observed <= now or expires <= observed:
            return unknown
        return {'status': 'RECENT_POLL' if now < expires else 'STALE',
                'market': 'EGX', 'mode': raw['mode'],
                'observed_at': observed.isoformat(), 'valid_until': expires.isoformat(),
                'reason': 'Completed worker poll; does not establish job success or scan coverage'}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return unknown
