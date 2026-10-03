"""Read-only system visibility; checkpoint claims are not runtime evidence."""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path

from app.ui.operational import load_operational_state
from app.scheduler_heartbeat import load_heartbeat
from app.egx_scan_history import load_scan_history
from app.data.source_admission import daily_source_summary
from app.ui.today import load_security_master_summary, load_validated_daily_observations
from app.calendar_maintenance_status import load_calendar_maintenance_status
from app.ui.coverage import render_coverage
from app.ui.dashboard import STYLE as DASHBOARD_STYLE
from app.ui.context import load_context, summary as context_summary
from app.ui.macro import load_macro, summary as macro_summary
from app.ui.us import load_us_ranking, summary as us_summary
from app.ui.experiment import load_experiment, render_system as render_experiment_system, summary as experiment_summary
from app.ui.ranking import load_ranking
from app.ui.readiness import render_readiness
from app.runtime_state import runtime_state_report
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
    heartbeat = load_heartbeat()
    ranking = load_ranking()
    product = product_state(operational, scan_history=history, ranking=ranking,
                            security_master=load_security_master_summary(),
                            daily_observations=load_validated_daily_observations(),
                            heartbeat=heartbeat)
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
        'scheduler': heartbeat,
        # Official-index/calendar maintenance job: last recorded outcome only.
        'calendar_maintenance': load_calendar_maintenance_status(),
        'readiness': product['readiness']['EGX'],
        # Identities, universe, data, admission, freshness, scans and candidates
        # as separate evidence levels; shared with /api/product.
        'coverage_breakdown': product['coverage_breakdown'],
        # EGX-RANK-v1 summary (Paper/Shadow research; admission and licensing as recorded).
        'ranking': product['ranking']['EGX'],
        'macro': macro_summary(load_macro()),
        'context': context_summary(load_context()),
        'us': us_summary(load_us_ranking()),
        'experiment': experiment_summary(load_experiment()),
        # Where each reader input came from; snapshot hashes verified, not assumed.
        'runtime_state': runtime_state_report(),
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
                        'symbol', 'status', 'provider', 'source_status', 'decision_at',
                        'valid_until', 'last_verified_session', 'reason')}
                        for item in egx['symbols']]},
            'US': {'configured_universe': None, 'data_ready': None, 'eligible': None,
                   'scanned': None, 'candidates': None, 'status': 'UNKNOWN',
                   'observed_symbols': None, 'status_counts': None,
                   'observed_at': None, 'observation_evidence': None,
                   'reason': 'No configured operational universe connected'}},
        'providers': provider_receipt_summary(egx),
        # Repository declarations, not live source checks; only ADMITTED
        # sources may feed signals or candidates.
        'daily_sources': daily_source_summary(),
    }


def render_pipelines(state):
    """US pipeline and research-context status (summaries of the recorded reports)."""
    def table(label, record):
        rows = ''.join(f'<tr><th scope="row">{escape(str(key))}</th><td>'
                       + escape('UNKNOWN' if value is None else str(value)) + '</td></tr>'
                       for key, value in (record or {'status': 'UNAVAILABLE'}).items())
        return f'<h2>{escape(label)}</h2><table aria-label="{escape(label)}"><tbody>{rows}</tbody></table>'
    from app.context.official import sec_contact, sec_status
    sec = sec_status(sec_contact())
    return (table('US pipeline (US-RANK-v1)', state.get('us'))
            + table('Research context (CONTEXT-v1)', state.get('context'))
            + table('SEC (US filings and fundamentals)', {'status': sec['status'], 'code': sec.get('code'),
                                                          'reason': sec.get('reason')}))


def render_calendar_maintenance(record):
    if not record:
        return ''
    rows = ''.join(f'<tr><th scope="row">{escape(key)}</th><td>'
                   + escape('UNKNOWN' if value is None else str(value)) + '</td></tr>'
                   for key, value in record.items())
    return ('<h2>Calendar maintenance: ' + escape(record['status']) + ' / '
            + escape(record['freshness']) + '</h2>'
            '<p>Daily official-index and session-calendar evidence job. Its last recorded '
            'outcome only: not a scan, a candidate or source admission.</p>'
            '<table aria-label="Calendar maintenance"><tbody>' + rows + '</tbody></table>')


def render_runtime_state(report):
    def cell(value):
        return '<td>' + escape('UNSET' if value is None else str(value)) + '</td>'
    content = ('<h2>Runtime state inputs: ' + escape(report['mode']) + '</h2>'
               '<p>Where each reader input is resolved. A snapshot bundle is a point-in-time '
               'copy, not live state.</p><div class="table-scroll">'
               '<table aria-label="Runtime state inputs"><thead><tr><th scope="col">Input</th>'
               '<th scope="col">Variable</th><th scope="col">Origin</th><th scope="col">Path</th>'
               '</tr></thead><tbody>')
    for name, item in report['inputs'].items():
        content += ('<tr>' + cell(name) + cell(item['variable']) + cell(item['origin'])
                    + cell(item['path']) + '</tr>')
    content += '</tbody></table></div>'
    snapshot = report['snapshot']
    if snapshot is not None:
        content += ('<p>Snapshot: ' + escape(snapshot['status']) + ' · taken at '
                    + escape(str(snapshot['taken_at'] or 'UNKNOWN')) + ' · build '
                    + escape(str(snapshot['build_revision'] or 'UNKNOWN')) + ' · integrity '
                    + escape(str(snapshot['integrity_check'] or 'UNKNOWN')) + '</p><p>Missing: '
                    + escape(', '.join(snapshot['missing']) or 'none') + ' · Hash mismatches: '
                    + escape(', '.join(snapshot['mismatched']) or 'none') + '</p>')
    content += ('<p>Warnings: ' + escape(', '.join(report['warnings']) or 'none') + '</p>')
    return content


def render_daily_sources(sources):
    def cell(value):
        return '<td>' + escape(str(value)) + '</td>'
    content = ('<h2>Daily source admission</h2><p>Repository declarations, not live source '
               'checks. Only ADMITTED sources may feed signals or candidates: either a reviewed '
               'paper/shadow entitlement, or an explicit recorded operator acceptance with NO '
               'contractual licence (see Licensing). Others stay EVIDENCE_BLOCKED while their '
               'observations remain preserved. Undeclared sources are EVIDENCE_BLOCKED.</p>')
    admitted = sum(row['status'] == 'ADMITTED' for row in sources)
    content += f'<p>Admitted daily sources: {admitted} of {len(sources)} declared.</p>'
    content += ('<div class="table-scroll"><table aria-label="Daily source admission"><thead><tr>'
                + ''.join(f'<th scope="col">{h}</th>' for h in (
                    'Provider', 'Market', 'Access', 'Entitlement', 'Licensing', 'Delay', 'Status',
                    'Reason', 'Evidence'))
                + '</tr></thead><tbody>')
    for row in sources:
        content += ('<tr>' + ''.join(cell(row.get(key, 'UNKNOWN')) for key in (
            'provider', 'market', 'access', 'entitlement', 'licensing', 'delay', 'status',
            'reason', 'evidence')) + '</tr>')
    return content + '</tbody></table></div>'


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


def render_market_receipts(market, summary):
    """Present scoped receipt classifications without inferring market coverage."""
    def cell(value):
        return escape(str(value)) if value is not None else 'UNKNOWN'

    html = ('<section id="market-' + cell(market) + '"><h2>' + cell(market)
            + ' receipt observations</h2><p>Receipt status: ' + cell(summary['status'])
            + '</p><p>Scope: ' + cell(summary.get('scope'))
            + '</p><p>Historical receipt classifications only; not source freshness or current coverage.</p>')
    if market == 'EGX':
        html += ('<p>Baseline universe target: ' + cell(summary['baseline_universe'])
                 + '. Authoritative universe: ' + cell(summary['authoritative_universe']) + '.</p>')
    else:
        html += '<p>Configured universe: ' + cell(summary.get('configured_universe')) + '.</p>'
    html += '<p>Data-ready / eligible / scanned / candidates: UNKNOWN.</p>'
    evidence = summary.get('observation_evidence')
    html += ('<p>Reader observed at: ' + cell(evidence['observed_at'] if evidence else None)
             + '</p><p>Distinct observed symbols: ' + cell(summary['observed_symbols']) + '</p>')
    counts = summary['status_counts']
    if counts is None:
        return html + '<p>Receipt counts and details: UNKNOWN.</p></section>'
    html += ('<div class="table-scroll"><table><caption>Observed receipt status counts</caption>'
             '<thead><tr><th scope="col">Receipt status</th><th scope="col">Symbols</th></tr></thead><tbody>')
    for status in RECEIPT_STATUSES:
        html += '<tr><th scope="row">' + cell(status) + '</th><td>' + cell(counts[status]) + '</td></tr>'
    html += '</tbody></table></div>'
    if summary['observed_symbols'] == 0:
        return html + '<p>Receipt details: 0 observed symbols.</p></section>'
    windows = {row['symbol']: row['verification_window'] for row in evidence['receipt_windows']} if evidence else {}
    grouped = {}
    for row in summary.get('receipts', []):
        grouped.setdefault(row['symbol'], []).append(row)
    html += ('<div class="table-scroll"><table><caption>Historical receipt details; '
             'verification windows have expiry exclusive. Expired bounds do not imply current validity.</caption>'
             '<thead><tr>' + ''.join('<th scope="col">' + title + '</th>' for title in (
                 'Symbol', 'Receipt status', 'Provider', 'Decision at', 'Valid until',
                 'Last verified session', 'Reason')) + '</tr></thead><tbody>')
    for symbol, rows in grouped.items():
        row = rows[0]
        if len(rows) > 1:
            values = (symbol, 'EVIDENCE_BLOCKED', None, None, None, None, 'Ambiguous duplicate receipt identity')
        else:
            bounds = windows.get(symbol) or {}
            status = row['status'] if row['status'] in RECEIPT_STATUSES else 'UNKNOWN'
            values = (symbol, status, row.get('provider'), bounds.get('decision_at'),
                      bounds.get('valid_until'), row.get('last_verified_session'), row.get('reason'))
        html += '<tr>' + ''.join('<td>' + cell(value) + '</td>' for value in values) + '</tr>'
    return html + '</tbody></table></div></section>'


CHECKPOINT_LABELS = (
    ('milestone', 'Reported milestone'),
    ('phase', 'Reported phase'),
    ('current_capability', 'Reported capability'),
    ('head', 'Checkpoint source HEAD at cycle start (not the deployed revision)'),
    ('tests', 'Reported tests'),
    ('deployment', 'Reported deployment note'),
    ('scheduler', 'Reported scheduler note'),
    ('next_action', 'Reported next action'),
    ('hard_blocker', 'Reported hard blocker'),
)


def render_project_checkpoint(state):
    """Render approved checkpoint fields; this is reported history, not verification."""
    def cell(key, value):
        if isinstance(value, str):
            return escape(value)
        if key == 'hard_blocker' and value is None:
            return 'None reported'
        return 'UNKNOWN'

    disclaimer = ('<p>Reported history only; not runtime verification, not deployment '
                  'verification, not scheduler heartbeat evidence and not the deployed revision.</p>')
    checkpoint = state.get('checkpoint')
    if not isinstance(checkpoint, dict):
        return '<section><h2>Project checkpoint: UNAVAILABLE</h2>' + disclaimer + '</section>'
    html = '<section><h2>Project checkpoint</h2>' + disclaimer
    html += ('<div class="table-scroll"><table><caption>Reported checkpoint fields</caption><tbody>'
             + ''.join('<tr><th scope="row">' + escape(label) + '</th><td>'
                       + cell(key, checkpoint.get(key)) + '</td></tr>'
                       for key, label in CHECKPOINT_LABELS)
             + '</tbody></table></div>')
    blockers = checkpoint.get('capability_blockers')
    if not isinstance(blockers, dict):
        html += '<p>Reported capability blockers: UNKNOWN.</p>'
    elif not blockers:
        html += '<p>Reported capability blockers: none reported.</p>'
    else:
        html += ('<div class="table-scroll"><table><caption>Reported capability blockers</caption><tbody>'
                 + ''.join('<tr><th scope="row">' + escape(str(key)) + '</th><td>'
                           + (escape(value) if isinstance(value, str) else 'UNKNOWN') + '</td></tr>'
                           for key, value in blockers.items())
                 + '</tbody></table></div>')
    return html + '</section>'


def render_runtime_observation(state):
    """Render approved runtime fields; a heartbeat is poll-loop evidence, not job success."""
    def cell(value):
        return escape(str(value)) if value is not None else 'UNKNOWN'

    scheduler = state['scheduler']
    status = scheduler.get('status')
    status = status if status in ('RECENT_POLL', 'STALE') else 'UNKNOWN'
    polled = status != 'UNKNOWN'
    rows = (('API status', state['api'].get('status')),
            ('API response timestamp', state['observed_at']),
            ('Deployment-injected revision', state['build'].get('revision')),
            ('EGX scheduler heartbeat status', status),
            ('EGX scheduler mode', scheduler.get('mode') if polled else None),
            ('Worker poll observed at', scheduler.get('observed_at') if polled else None),
            ('Poll evidence valid until', scheduler.get('valid_until') if polled else None))
    html = ('<section id="runtime-observation"><h2>Runtime observation</h2>'
            '<div class="table-scroll"><table><caption>API response and local scheduler heartbeat</caption><tbody>'
            + ''.join('<tr><th scope="row">' + title + '</th><td>' + cell(value) + '</td></tr>'
                      for title, value in rows)
            + '</tbody></table></div>')
    if status == 'RECENT_POLL':
        html += ('<p>EGX worker poll evidence: recent completed EGX poll-loop iteration only; '
                 'valid-until expiry is exclusive. Job outcomes, scan completion, source health '
                 'and provider health: UNKNOWN (job outcomes are recorded in the job ledger).</p>')
    elif status == 'STALE':
        html += ('<p>Worker health: UNKNOWN. Heartbeat expired (valid-until expiry exclusive); '
                 'historical poll only. A stopped, crashed or delayed worker cannot be distinguished.</p>')
    else:
        html += '<p>Worker health: UNKNOWN. No valid scheduler heartbeat evidence available.</p>'
    return html + ('<p>Heartbeats do not establish scan completion, job success, source health '
                   'or provider health. US worker health: UNKNOWN (no US scheduler observation). '
                   'Revision is unverified and UNKNOWN when not injected at deployment.</p></section>')


def render_system(state):
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>System progress</title><style>body{font:16px sans-serif;max-width:1100px;margin:32px auto;padding:20px;'
            'background:#071019;color:#e9f0f5}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8bd5b0}'
            '.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}'
            'th,td{text-align:left;padding:10px;border-bottom:1px solid #344653;overflow-wrap:anywhere}'
            'caption{text-align:left;margin-bottom:12px}' + DASHBOARD_STYLE
            + 'pre{white-space:pre-wrap;overflow-wrap:anywhere}td{overflow-wrap:anywhere}</style>'
            '</head><body><nav><a href="/">TODAY</a> · <a href="/shadow">Paper/Shadow</a> · '
            '<a href="/performance">PERFORMANCE</a></nav><h1>SYSTEM</h1>'
            '<p>LIVE MONEY DISABLED · A candidate is not a fill · Unknown values appear as UNKNOWN (null in the API).</p>'
            + render_runtime_observation(state)
            + (render_runtime_state(state['runtime_state']) if state.get('runtime_state') else '')
            + render_calendar_maintenance(state.get('calendar_maintenance'))
            + render_pipelines(state)
            + render_experiment_system(load_experiment())
            + (render_coverage(state['coverage_breakdown']) if state.get('coverage_breakdown') else '')
            + (render_readiness({'EGX': state['readiness']}) if state.get('readiness') else '')
            + render_provider_receipts(state['providers'])
            + render_daily_sources(state.get('daily_sources') or [])
            + render_market_receipts('EGX', state['markets']['EGX'])
            + render_market_receipts('US', state['markets']['US'])
            + render_scan_runs(state['scan_runs'])
            + render_project_checkpoint(state)
            + '</body></html>')
