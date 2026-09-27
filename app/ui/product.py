"""Unified read-only product views over operational receipts, never legacy signals."""
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from app.ui.operational import render_operational
from app.egx_scan_history import _valid_summary, valid_reconciliation

SECTIONS = ('TODAY', 'LIVE', 'PRE-SURGE', 'SWING', 'PERFORMANCE', 'RESEARCH', 'SYSTEM')
MARKETS = ('EGX', 'US', 'ALL')
RECEIPT_STATUSES = ('WATCH', 'READY_NO_SIGNAL', 'NOT_READY', 'DATA_STALE',
                    'EVIDENCE_BLOCKED', 'UNKNOWN')


def observed_receipt_summary(market_state):
    """Count reader observations only; never infer universe or scan completion."""
    summary = {'scope': 'Observed operational symbols only; not scan coverage',
               'observed_symbols': None, 'status_counts': None}
    if not market_state['available']:
        return summary
    # The reader emits one row per symbol. Ambiguous duplicates must not inflate
    # counts or select a favorable status; preserve them as blocked evidence.
    statuses = {}
    for item in market_state['symbols']:
        symbol = item.get('symbol')
        if not isinstance(symbol, str) or not symbol.strip():
            return summary
        status = item.get('status')
        status = status if status in RECEIPT_STATUSES else 'UNKNOWN'
        statuses[symbol] = 'EVIDENCE_BLOCKED' if symbol in statuses else status
    counts = dict.fromkeys(RECEIPT_STATUSES, 0)
    for status in statuses.values():
        counts[status] += 1
    return summary | {'observed_symbols': len(statuses), 'status_counts': counts}


def scan_run_state(history):
    unknown = {'status': 'UNKNOWN', 'run': None, 'scheduler_completion': None,
               'completion_evidence': None}
    if (not isinstance(history, dict) or history.get('status') != 'HISTORICAL_RUN'
            or not _valid_summary(history.get('run'))):
        return unknown
    raw = history['run']
    # Legacy classification has no scheduler result. Version 3 includes a
    # recorded ledger snapshot, not a live query of current scheduler health.
    run = {key: raw[key] for key in ('completed_at', 'scope_reference', 'scope_kind',
                                    'requested', 'scanned')}
    run['status_counts'] = dict(raw['status_counts'])
    run['symbols'] = [{key: row[key] for key in ('symbol', 'status', 'scanned')}
                      for row in raw['symbols']]
    attempt = dict(raw['scheduler_attempt']) if raw['schema_version'] == 3 else None
    run['scheduler_attempt'] = attempt
    completion = attempt if attempt and attempt['status'] == 'SUCCEEDED' else None
    origin = 'RECORDED_HISTORY' if completion else None
    reconciled = history.get('reconciled_scheduler_completion')
    if completion is None and valid_reconciliation(raw, reconciled):
        completion = dict(reconciled)
        origin = 'READ_ONLY_LEDGER'
    evidence = None
    if completion:
        try:
            observed = datetime.fromisoformat(history['observed_at'])
            if (observed.tzinfo is None or not
                    max(datetime.fromisoformat(raw['completed_at']),
                        datetime.fromisoformat(completion['finished_at']))
                    <= observed <= datetime.now(timezone.utc)):
                raise ValueError('invalid observation time')
            evidence = {'origin': origin, 'observed_at': history['observed_at']}
        except (KeyError, TypeError, ValueError):
            completion = None
    return {'status': 'HISTORICAL_RUN', 'run': run, 'scheduler_completion': completion,
            'completion_evidence': evidence}


def product_state(operational, market='ALL', section='TODAY', *, scan_history=None):
    if market not in MARKETS or section not in SECTIONS:
        raise ValueError('unknown product view')
    # Only EGX has a connected operational receipt reader. Do not imply US coverage.
    egx = {key: operational[key] for key in ('configured', 'available', 'status')}
    egx['symbols'] = [dict(item) for item in operational['symbols']
                      if item.get('market') == 'EGX'] if egx['available'] else []
    us = {'configured': False, 'available': False, 'status': 'UNKNOWN', 'symbols': []}
    markets = {'EGX': egx, 'US': us}
    selected = MARKETS[:2] if market == 'ALL' else (market,)
    return {
        'market': market, 'section': section, 'mode': 'PAPER/SHADOW ONLY',
        'live': 'DISABLED', 'markets': {key: markets[key] for key in selected},
        'coverage': {key: {'universe': None, 'data_ready': None, 'scanned': None,
                           'candidates': None,
                           **observed_receipt_summary(markets[key])}
                     for key in selected},
        'scan_runs': {key: scan_run_state(scan_history if key == 'EGX' else None)
                      for key in selected},
        'performance': None,
    }


def render_product(state):
    market, section = state['market'], state['section']
    def link(label, selected_market, selected_section):
        query = escape(urlencode({'market': selected_market, 'section': selected_section}), quote=True)
        current = ' aria-current="page"' if (selected_market, selected_section) == (market, section) else ''
        return f'<a href="/?{query}"{current}>{label}</a>'
    nav = '<nav aria-label="Product sections">' + ' '.join(link(s, market, s) for s in SECTIONS) + '</nav>'
    nav += '<nav aria-label="Markets">' + ' '.join(link(m, m, section) for m in MARKETS) + '</nav>'
    content = f'<h1>{section} · {market}</h1><p>LIVE MONEY DISABLED · Candidate != fill</p>'
    for key, value in state['markets'].items():
        observed = state['coverage'][key]['observed_symbols']
        content += (f'<article><h2>{key}</h2><p>Status: {escape(str(value["status"]))}</p>'
                    f'<p>Observed symbols: {observed if observed is not None else "UNKNOWN"}. '
                    'Universe / data-ready / scanned / candidates: UNKNOWN.</p></article>')
        counts = state['coverage'][key]['status_counts']
        if counts is not None:
            content += (f'<table aria-label="{key} observed receipt statuses">'
                        '<caption>Observed operational symbols only; not scan coverage</caption>'
                        '<thead><tr><th scope="col">Receipt status</th>'
                        '<th scope="col">Symbols</th></tr></thead><tbody>')
            for status, count in counts.items():
                content += f'<tr><th scope="row">{status}</th><td>{count}</td></tr>'
            content += '</tbody></table>'
    if section in ('TODAY', 'SWING'):
        content += '<p>Verified operational receipts only. Observed symbols do not establish scan coverage.</p>'
        for value in state['markets'].values():
            content += render_operational(value, fragment=True)
    else:
        messages = {
            'LIVE': 'Current scanner activity UNKNOWN. Historical classification does not prove scheduler completion.',
            'PRE-SURGE': 'Pre-surge opportunities UNKNOWN. No validated operational feed is connected.',
            'PERFORMANCE': 'Performance UNKNOWN. Authentic Paper/Shadow lifecycle evidence is required.',
            'RESEARCH': 'Research results do not authorize operational use.',
            'SYSTEM': 'Open SYSTEM for the detailed operator checkpoint and capability blockers.',
        }
        content += '<p>' + messages[section] + '</p>'
    if section == 'LIVE':
        for key, history in state['scan_runs'].items():
            content += f'<h2>{key} scan history: {history["status"]}</h2>'
            run = history['run']
            if run is None:
                continue
            completion = history['scheduler_completion']
            evidence = history['completion_evidence']
            prefix = ('Recorded SUCCEEDED' if evidence and evidence['origin'] == 'RECORDED_HISTORY'
                      else 'Ledger-reconciled SUCCEEDED')
            label = (prefix + ' · ' + completion['market_date'] + ' · '
                     + completion['checkpoint'] + ' · attempt ' + str(completion['attempt_count'])
                     if completion else 'UNKNOWN')
            content += ('<p>Historical explicit selection only; not current readiness, universe coverage or fills. '
                        'Scheduler completion: ' + escape(label) + '.</p><p>Completed at: '
                        + escape(run['completed_at']) + ' · Scope: ' + escape(run['scope_reference'])
                        + f' · Requested: {run["requested"]} · Verified scans: {run["scanned"]}</p>')
            if evidence:
                content += ('<p>Completion evidence: ' + escape(evidence['origin'])
                            + ' · Observed at: ' + escape(evidence['observed_at'])
                            + '. Historical observation only; current worker health UNKNOWN.</p>')
            content += '<table><caption>Historical per-target outcomes</caption><tr><th>Symbol</th><th>Status</th><th>Scanned</th></tr>'
            for row in run['symbols']:
                content += (f'<tr><td>{escape(row["symbol"])}</td><td>{escape(row["status"])}</td>'
                            f'<td>{row["scanned"]}</td></tr>')
            content += '</table>'
    content += ('<footer><a href="/system">SYSTEM details</a> · '
                '<a href="/shadow">Audited collection</a> · '
                '<a href="/performance">Performance evidence</a></footer>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>EGX + US Paper/Shadow</title><style>'
            'body{background:#071019;color:#e9f0f5;font:16px Arial,sans-serif;max-width:1100px;margin:auto;padding:24px}'
            'nav{display:flex;flex-wrap:wrap;gap:16px;margin:20px 0}a{color:#8bd5b0}'
            '[aria-current]{font-weight:bold;text-decoration-thickness:3px}'
            'article{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:20px;margin:20px 0}'
            'p{line-height:1.6}footer{margin-top:32px}</style></head><body>'
            + nav + '<main>' + content + '</main></body></html>')
