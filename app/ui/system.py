"""Read-only system visibility; checkpoint claims are not runtime evidence."""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path

from app.ui.operational import load_operational_state
from app.scheduler_heartbeat import load_heartbeat
from app.egx_scan_history import load_scan_history


CHECKPOINT = Path(__file__).resolve().parents[2] / 'PROGRESS.json'


def provider_receipt_summary(operational):
    """Group admitted receipt states; receipt freshness is not provider uptime."""
    groups = {}
    unattributed = 0
    if operational['available']:
        for item in operational['symbols']:
            provider = item.get('provider')
            if not isinstance(provider, str) or not provider.strip():
                unattributed += 1
                continue
            group = groups.setdefault(provider, {
                'market': 'EGX', 'provider': provider, 'health': 'UNKNOWN',
                'current_verified_symbols': 0, 'stale_receipt_symbols': 0,
                'other_symbols': 0,
            })
            status = item['status']
            key = ('current_verified_symbols' if status in ('WATCH', 'READY_NO_SIGNAL')
                   else 'stale_receipt_symbols' if status == 'DATA_STALE'
                   else 'other_symbols')
            group[key] += 1
    return {
        'status': 'UNKNOWN',
        'reason': 'No live provider health probe connected; receipt counts do not establish availability or source rights',
        'receipt_scope': 'Distinct symbols in configured EGX isolated runtime only; no US provider observation',
        'receipt_observation': 'AVAILABLE' if operational['available'] else 'UNAVAILABLE',
        'unattributed_symbols': unattributed if operational['available'] else None,
        'sources': [groups[key] for key in sorted(groups)],
    }


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
        'egx_scan_history': load_scan_history(),
        'markets': {
            'EGX': {'baseline_universe': 224, 'authoritative_universe': None,
                    'status': operational['status'], **counts,
                    'observed_at': observed,
                    'scope': 'Distinct symbols in configured isolated runtime only; not full-universe coverage',
                    'source': 'load_operational_state: current verified receipts; expired receipts excluded from ready/eligible/scanned',
                    'receipts': [{key: item.get(key) for key in (
                        'symbol', 'status', 'provider', 'decision_at', 'valid_until',
                        'last_verified_session', 'reason')} for item in operational['symbols']]},
            'US': {'configured_universe': None, 'data_ready': None, 'eligible': None,
                   'scanned': None, 'candidates': None, 'status': 'UNKNOWN',
                   'reason': 'No configured operational universe connected'}},
        'providers': provider_receipt_summary(operational),
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
            + block('EGX last completed scan — historical scope only', state['egx_scan_history'])
            + block('Project checkpoint — reported history, not runtime verification', state['checkpoint'])
            + '</body></html>')
