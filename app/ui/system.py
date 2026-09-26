"""Read-only system visibility; checkpoint claims are not runtime evidence."""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path

from app.ui.operational import load_operational_state
from app.scheduler_heartbeat import load_heartbeat


CHECKPOINT = Path(__file__).resolve().parents[2] / 'PROGRESS.json'


def load_system_state():
    checkpoint = None
    try:
        raw = json.loads(CHECKPOINT.read_text())
        if isinstance(raw, dict):
            checkpoint = {key: raw.get(key) for key in (
                'milestone', 'phase', 'current_capability', 'head', 'tests',
                'deployment', 'scheduler', 'next_action', 'hard_blocker',
                'capability_blockers')}
    except (OSError, ValueError):
        pass
    operational = load_operational_state()
    observed = datetime.now(timezone.utc).isoformat()
    counts = dict.fromkeys(('data_ready', 'eligible', 'scanned', 'watch',
                            'ready_no_signal', 'not_ready', 'data_stale', 'evidence_blocked'))
    if operational['available']:
        symbols = operational['symbols']
        counts = {key: 0 for key in counts}
        for item in symbols:
            status = item['status']
            if status.lower() in counts:
                counts[status.lower()] += 1
            if status in ('WATCH', 'READY_NO_SIGNAL'):
                for key in ('data_ready', 'eligible', 'scanned'):
                    counts[key] += 1
    return {
        'observed_at': observed, 'live_money': False,
        'api': {'status': 'RESPONDING'},
        'build': {'revision': os.getenv('EGX_BUILD_REVISION') or None,
                  'source': 'deployment-injected EGX_BUILD_REVISION; unverified if absent'},
        'checkpoint': checkpoint,
        'checkpoint_status': 'AVAILABLE' if checkpoint is not None else 'UNAVAILABLE',
        'scheduler': load_heartbeat(),
        'markets': {
            'EGX': {'baseline_universe': 224, 'authoritative_universe': None,
                    'status': operational['status'], **counts,
                    'observed_at': observed,
                    'scope': 'Distinct symbols in configured isolated runtime only; not full-universe coverage',
                    'source': 'load_operational_state: current verified receipts; expired receipts excluded from ready/eligible/scanned',
                    'receipts': [{key: item.get(key) for key in (
                        'symbol', 'status', 'provider', 'decision_at', 'valid_until',
                        'last_verified_session')} for item in operational['symbols']]},
            'US': {'configured_universe': None, 'data_ready': None, 'eligible': None,
                   'scanned': None, 'candidates': None, 'status': 'UNKNOWN',
                   'reason': 'No configured operational universe connected'}},
        'providers': {'status': 'UNKNOWN',
                      'reason': 'Receipt source/freshness is shown per symbol; no live provider health probe connected'},
    }


def render_system(state):
    def block(title, value):
        return '<section><h2>' + escape(title) + '</h2><pre>' + escape(json.dumps(value, indent=2)) + '</pre></section>'
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>System progress</title><style>body{font:16px sans-serif;max-width:1100px;margin:32px auto;padding:20px;'
            'background:#071019;color:#e9f0f5}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8bd5b0}</style>'
            '</head><body><nav><a href="/">TODAY</a> · <a href="/shadow">Paper/Shadow</a> · '
            '<a href="/performance">PERFORMANCE</a></nav><h1>SYSTEM</h1>'
            '<p>LIVE MONEY DISABLED · Candidate != fill · Unknown values appear as null.</p>'
            + block('Runtime observation', {key: state[key] for key in ('observed_at', 'api', 'build', 'scheduler', 'providers')})
            + block('EGX — isolated receipt scope', state['markets']['EGX'])
            + block('US', state['markets']['US'])
            + block('Project checkpoint — reported history, not runtime verification', state['checkpoint'])
            + '</body></html>')
