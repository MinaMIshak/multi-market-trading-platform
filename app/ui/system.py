"""Read-only system visibility; checkpoint claims are not runtime evidence."""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path

from app.ui.operational import load_operational_state
from app.scheduler_heartbeat import load_heartbeat
from app.egx_scan_history import load_scan_history
from app.ui.product import product_state, render_scan_runs, observed_receipt_summary, RECEIPT_STATUSES


CHECKPOINT = Path(__file__).resolve().parents[2] / 'PROGRESS.json'


def provider_receipt_summary(operational):
    """Group admitted receipt states; receipt freshness is not provider uptime."""
    groups = {}
    unattributed = 0
    summary = observed_receipt_summary(operational)
    evidence = summary['observation_evidence']
    admitted = (evidence is not None and summary['observed_symbols'] is not None
                and len(operational['symbols']) == summary['observed_symbols'])
    if admitted:
        windows = {row['symbol']: row['verification_window']
                   for row in evidence['receipt_windows']}
        for item in operational['symbols']:
            provider = item.get('provider')
            if not isinstance(provider, str) or not provider.strip():
                unattributed += 1
                continue
            group = groups.setdefault(provider, {
                'market': 'EGX', 'provider': provider, 'health': 'UNKNOWN',
                'observed_at': evidence['observed_at'],
                'verified_symbols_at_observation': 0, 'stale_receipt_symbols': 0,
                'other_symbols': 0,
            })
            status = item.get('status')
            window = windows[item['symbol']]
            key = ('verified_symbols_at_observation'
                   if window and status in ('WATCH', 'READY_NO_SIGNAL')
                   else 'stale_receipt_symbols' if window and status == 'DATA_STALE'
                   else 'other_symbols')
            group[key] += 1
        for group in groups.values():
            # Compatibility alias: this is historical classification, not health.
            group['current_verified_symbols'] = group['verified_symbols_at_observation']
    return {
        'status': 'UNKNOWN',
        'reason': 'No live provider health probe connected; receipt counts do not establish availability or source rights',
        'receipt_scope': 'Distinct symbols in configured EGX isolated runtime only; no US provider observation',
        'count_semantics': 'Receipt classifications at reader observation time; not current provider health or coverage',
        'observation_evidence': evidence if admitted else None,
        'receipt_observation': 'AVAILABLE' if admitted else 'UNAVAILABLE',
        'unattributed_symbols': unattributed if admitted else None,
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
    history = load_scan_history()
    observed = datetime.now(timezone.utc).isoformat()
    product = product_state(operational, scan_history=history)
    egx = product['markets']['EGX']
    summary = product['coverage']['EGX']
    # Preserve legacy field names, but coverage requires independent evidence.
    counts = dict.fromkeys(('data_ready', 'eligible', 'scanned'))
    counts.update({status.lower(): value for status, value in
                   (summary['status_counts'] or dict.fromkeys(RECEIPT_STATUSES)).items()})
    return {
        'observed_at': observed, 'live_money': False,
        'api': {'status': 'RESPONDING'},
        'build': {'revision': os.getenv('EGX_BUILD_REVISION') or None,
                  'source': 'deployment-injected EGX_BUILD_REVISION; unverified if absent'},
        'checkpoint': checkpoint,
        'checkpoint_status': 'AVAILABLE' if checkpoint is not None else 'UNAVAILABLE',
        'scheduler': load_heartbeat(),
        'egx_scan_history': history,
        'scan_runs': product['scan_runs'],
        'markets': {
            'EGX': {'baseline_universe': 224, 'authoritative_universe': None,
                    'status': egx['status'], **counts,
                    'observed_symbols': summary['observed_symbols'],
                    'status_counts': summary['status_counts'],
                    'observed_at': (summary['observation_evidence']['observed_at']
                                    if summary['observation_evidence'] else None),
                    'observation_evidence': summary['observation_evidence'],
                    'scope': summary['scope'],
                    'source': 'load_operational_state: receipt classifications only; data_ready/eligible/scanned require independent evidence',
                    'receipts': [{key: item.get(key) for key in (
                        'symbol', 'status', 'provider', 'decision_at', 'valid_until',
                        'last_verified_session', 'reason')} for item in egx['symbols']]},
            'US': {'configured_universe': None, 'data_ready': None, 'eligible': None,
                   'scanned': None, 'candidates': None, 'status': 'UNKNOWN',
                   'observed_symbols': None, 'status_counts': None,
                   'observed_at': None, 'observation_evidence': None,
                   'reason': 'No configured operational universe connected'}},
        'providers': provider_receipt_summary(egx),
    }


def render_provider_receipts(providers):
    """Render the validated summary, retaining historical observation semantics."""
    def cell(value):
        return escape(str(value)) if value is not None else 'UNKNOWN'

    html = ('<section id="provider-receipts"><h2>Provider receipts</h2>'
            '<p>Historical receipt classifications at reader observation time. '
            'Provider health: UNKNOWN. Counts do not establish current coverage, '
            'source freshness or source rights.</p>'
            '<p>Scope: configured EGX isolated runtime. US provider observation: UNKNOWN.</p>')
    if providers['receipt_observation'] != 'AVAILABLE':
        return html + '<p>Receipt attribution: UNAVAILABLE. Counts and reader observation time: UNKNOWN.</p></section>'
    html += ('<p>Reader observed at: ' + cell(providers['observation_evidence']['observed_at'])
             + '</p><p>Symbols without provider attribution: '
             + cell(providers['unattributed_symbols']) + '</p>')
    if not providers['sources']:
        return html + '<p>Attributed providers: 0 at reader observation time.</p></section>'
    html += ('<div class="table-scroll"><table><caption>Symbols by provider at reader observation time</caption>'
             '<thead><tr><th scope="col">Market</th><th scope="col">Provider</th>'
             '<th scope="col">Verified at observation</th><th scope="col">Stale at observation</th>'
             '<th scope="col">Other at observation</th></tr></thead><tbody>')
    for source in providers['sources']:
        html += '<tr>' + ''.join('<td>' + cell(source[key]) + '</td>' for key in (
            'market', 'provider', 'verified_symbols_at_observation',
            'stale_receipt_symbols', 'other_symbols')) + '</tr>'
    return html + '</tbody></table></div></section>'


def render_system(state):
    def block(title, value):
        return '<section><h2>' + escape(title) + '</h2><pre>' + escape(json.dumps(value, indent=2)) + '</pre></section>'
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>System progress</title><style>body{font:16px sans-serif;max-width:1100px;margin:32px auto;padding:20px;'
            'background:#071019;color:#e9f0f5}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8bd5b0}'
            '.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}'
            'th,td{text-align:left;padding:10px;border-bottom:1px solid #344653;overflow-wrap:anywhere}'
            'caption{text-align:left;margin-bottom:12px}</style>'
            '</head><body><nav><a href="/">TODAY</a> · <a href="/shadow">Paper/Shadow</a> · '
            '<a href="/performance">PERFORMANCE</a></nav><h1>SYSTEM</h1>'
            '<p>LIVE MONEY DISABLED · Candidate != fill · Unknown values appear as null.</p>'
            + block('Runtime observation', {key: state[key] for key in ('observed_at', 'api', 'build', 'scheduler')})
            + render_provider_receipts(state['providers'])
            + block('EGX — isolated receipt scope', state['markets']['EGX'])
            + block('US', state['markets']['US'])
            + render_scan_runs(state['scan_runs'])
            + block('Project checkpoint — reported history, not runtime verification', state['checkpoint'])
            + '</body></html>')
