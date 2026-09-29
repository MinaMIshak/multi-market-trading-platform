"""Unified read-only product views over operational receipts, never legacy signals."""
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from app.ui.operational import render_operational
from app.egx_scan_history import valid_summary, valid_reconciliation
from app.financial_services_status import financial_services_status
from app.data.source_admission import daily_source_admission

SECTIONS = ('TODAY', 'LIVE', 'PRE-SURGE', 'SWING', 'PERFORMANCE', 'RESEARCH', 'SYSTEM')
MARKETS = ('EGX', 'US', 'ALL')
RECEIPT_STATUSES = ('WATCH', 'READY_NO_SIGNAL', 'NOT_READY', 'DATA_STALE',
                    'EVIDENCE_BLOCKED', 'UNKNOWN')


def receipt_observation(market_state):
    """Project reader time and per-receipt bounds, never source freshness."""
    if not market_state['available']:
        return None
    try:
        observed = datetime.fromisoformat(market_state['observed_at'])
        if observed.utcoffset() is None or observed > datetime.now(timezone.utc):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    grouped = {}
    for item in market_state['symbols']:
        symbol = item.get('symbol')
        if not isinstance(symbol, str) or not symbol.strip():
            return None
        grouped.setdefault(symbol, []).append(item)
    windows = []
    for symbol, rows in grouped.items():
        bounds = None
        if len(rows) == 1:
            item = rows[0]
            try:
                decision = datetime.fromisoformat(item['decision_at'])
                expiry = datetime.fromisoformat(item['valid_until'])
                status = item.get('status')
                if (decision.utcoffset() is not None and expiry.utcoffset() is not None
                        and decision <= observed and decision < expiry
                        and ((status in ('WATCH', 'READY_NO_SIGNAL') and observed < expiry)
                             or (status == 'DATA_STALE' and observed >= expiry))):
                    bounds = {'decision_at': item['decision_at'], 'valid_until': item['valid_until']}
            except (KeyError, TypeError, ValueError):
                pass
        windows.append({'symbol': symbol, 'verification_window': bounds})
    return {'origin': 'OPERATIONAL_RECEIPT_READER',
            'observed_at': market_state['observed_at'], 'receipt_windows': windows}


def observed_receipt_summary(market_state):
    """Count reader observations only; never infer universe or scan completion."""
    summary = {'scope': 'Observed operational symbols only; not scan coverage',
               'observed_symbols': None, 'status_counts': None,
               'observation_evidence': receipt_observation(market_state)}
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
            or not valid_summary(history.get('run'))):
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


def admitted_daily_observations(rows, market):
    """Annotate each row with registry source admission; row claims are ignored."""
    if rows is None:
        return None
    result = []
    for row in rows:
        admission = daily_source_admission(row.get('provider'), market)
        result.append({**row, 'source_status': admission.status,
                       'source_reason': admission.reason})
    return result


def product_state(operational, market='ALL', section='TODAY', *, scan_history=None,
                  security_master=None, daily_observations=None):
    if market not in MARKETS or section not in SECTIONS:
        raise ValueError('unknown product view')
    # Only EGX has a connected operational receipt reader. Do not imply US coverage.
    egx = {key: operational[key] for key in ('configured', 'available', 'status')}
    egx['observed_at'] = operational.get('observed_at')
    egx['symbols'] = [dict(item) for item in operational['symbols']
                      if item.get('market') == 'EGX'] if egx['available'] else []
    us = {'configured': False, 'available': False, 'status': 'UNKNOWN', 'symbols': []}
    markets = {'EGX': egx, 'US': us}
    selected = MARKETS[:2] if market == 'ALL' else (market,)
    state = {
        'market': market, 'section': section, 'mode': 'PAPER/SHADOW ONLY',
        'live': 'DISABLED', 'markets': {key: markets[key] for key in selected},
        'coverage': {key: {'universe': None, 'data_ready': None, 'scanned': None,
                           'candidates': None,
                           **observed_receipt_summary(markets[key])}
                     for key in selected},
        'scan_runs': {key: scan_run_state(scan_history if key == 'EGX' else None)
                      for key in selected},
        'performance': None,
        # Identity/reference rows only (EGX security master); never coverage,
        # readiness or dated membership. No US identity reader exists.
        'identities': {key: security_master if key == 'EGX' else None
                       for key in selected},
        # Dated VALIDATED daily-canonical artifacts; not freshness or readiness.
        # Source status comes only from the daily source admission registry.
        'daily_observations': {key: admitted_daily_observations(daily_observations, key)
                                    if key == 'EGX' else None
                               for key in selected},
    }
    if section == 'RESEARCH':
        # Notes restate verified EGX receipts only; nothing is generated. US has no reader.
        notes = None
        if 'EGX' in selected and egx['available']:
            from app.research.receipt_notes import receipt_research_notes
            notes = receipt_research_notes(egx)
        state['research'] = {'notes': notes, 'financial_services': financial_services_status()}
    return state


def render_scan_runs(scan_runs):
    """Display validated historical completion separately from worker health."""
    content = ''
    for key, history in scan_runs.items():
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
    return content


def render_research(research):
    """Research intelligence status; never an execution or order path."""
    status = research['financial_services']
    upstream = status['upstream']
    content = ('<h2>Financial Services integration: ' + escape(status['status']) + '</h2>'
               '<p>Research layer only; no trade execution authority. Upstream '
               + escape(upstream['repository']) + ' pinned at ' + escape(upstream['commit'][:7])
               + ' (' + escape(upstream['license']) + ', evaluated ' + escape(upstream['evaluated_on'])
               + '). Recorded evaluation, not a live connector check.</p>'
               '<table><caption>Evaluated plugins (not installed)</caption>'
               '<tr><th>Plugin</th><th>Version</th><th>MCP config</th></tr>')
    for row in status['plugins']:
        content += ('<tr><td>' + escape(row['name']) + '</td><td>' + escape(row['version'])
                    + '</td><td>' + escape(row['mcp_config']) + '</td></tr>')
    content += '</table><table><caption>Connectors</caption><tr><th>Connector</th><th>Status</th></tr>'
    for row in status['connectors']:
        content += '<tr><td>' + escape(row['name']) + '</td><td>' + escape(row['status']) + '</td></tr>'
    content += ('</table><p>EGX coverage: ' + escape(status['egx_coverage']) + '. '
                + escape(status['data_policy']) + '</p>')
    notes = research['notes']
    if notes is None:
        return content + '<p>Sourced research notes: UNKNOWN. No verified receipt reader is available.</p>'
    content += ('<h2>Sourced research notes</h2><p>Restated from verified operational receipts; '
                'no generated claims, no trade levels, execution authority NONE.</p>')
    if not notes:
        content += '<p>No EGX symbols observed by the receipt reader.</p>'
    for note in notes:
        content += ('<table><caption>' + escape(note['market']) + ' ' + escape(note['subject'])
                    + ' (generated ' + escape(note['generated_at']) + ')</caption>'
                    '<tr><th>Kind</th><th>Statement</th><th>Basis</th></tr>')
        for row in note['statements']:
            if row['kind'] == 'SOURCE_FACT':
                source = row['provenance']
                basis = (source['source_id'] + ' · ' + source['locator'] + ' · as of '
                         + source['as_of'] + ' · observed ' + source['observed_at'])
            elif row['kind'] == 'UNKNOWN':
                basis = row['reason']
            else:
                basis = row.get('method') or row.get('model')
                basis += ' · from ' + ', '.join(row['inputs'])
            content += ('<tr><td>' + escape(row['kind']) + '</td><td>' + escape(row['text'])
                        + '</td><td>' + escape(basis) + '</td></tr>')
        content += '</table>'
    return content


def render_identities(identities):
    def known(value):
        return 'UNKNOWN' if value is None else escape(str(value))
    html = ''
    for key, summary in identities.items():
        if summary is None:
            html += f'<p>{key} security-master identities: UNKNOWN.</p>'
            continue
        types = ''.join(f'<li>{escape(str(kind))}: {escape(str(count))}</li>'
                        for kind, count in summary['by_type'].items())
        providers = ', '.join(escape(str(p)) for p in summary['source_providers']) or 'none'
        html += (f'<section aria-label="{key} security-master identities">'
                 f'<h2>{key} security-master identities</h2>'
                 '<p>Identity/reference data only: ticker, name and provider alias mapping. '
                 'NOT price, NOT a trading signal, NOT dated exchange membership or index '
                 'constituency, and not scan coverage.</p>'
                 f'<p>{escape(str(summary["total_instruments"]))} identity records from '
                 f'source(s): {providers}.</p><ul>{types}</ul>'
                 f'<p>Latest identity snapshot capture: {known(summary["latest_snapshot_updated_at"])}. '
                 f'Latest source market date: {known(summary["latest_source_market_date"])}.</p>'
                 '</section>')
    return html


def render_daily_observations(observations):
    html = ''
    for key, rows in observations.items():
        if rows is None:
            html += f'<p>{key} validated daily observations: UNKNOWN.</p>'
            continue
        if not rows:
            html += f'<p>{key} validated daily observations: none recorded.</p>'
            continue
        body = ''.join(
            f'<tr><td>{escape(str(r["canonical_symbol"]))}</td>'
            f'<td>{escape(str(r["provider"]))}</td>'
            f'<td>{escape(str(r["oldest_market_date"]))} to {escape(str(r["newest_market_date"]))}</td>'
            f'<td>{escape(str(r["valid_bar_count"]))}</td>'
            f'<td>{escape(str(r["quarantined_bar_count"]))}</td>'
            f'<td>{escape(str(r["source_snapshot_date"]))}</td>'
            f'<td>{escape(str(r.get("freshness") or "UNKNOWN"))}</td>'
            f'<td>{escape(str(r.get("source_status") or "EVIDENCE_BLOCKED"))}</td>'
            f'<td>{escape(str(r.get("source_reason") or "source admission unavailable"))}</td></tr>'
            for r in rows)
        html += (f'<section aria-label="{key} validated daily observations">'
                 f'<h2>{key} validated daily observations</h2>'
                 '<p>Dated historical daily bars that passed canonical validation. '
                 'Freshness is derived only from VERIFIED exchange sessions, through the '
                 'prior Cairo calendar day (today\'s session is not evaluated): STALE means '
                 'a verified trading session is missing; CURRENT means every intervening day '
                 'is a verified non-trading day; UNKNOWN means freshness NOT ESTABLISHED. '
                 'Source usage rights NOT ESTABLISHED unless Source status is ADMITTED '
                 '(reviewed paper/shadow entitlement); EVIDENCE_BLOCKED rows are not '
                 'admitted trading inputs. Not a signal, candidate or fill.</p>'
                 f'<table aria-label="{key} validated daily observations">'
                 '<thead><tr><th scope="col">Symbol</th><th scope="col">Source</th>'
                 '<th scope="col">Market dates</th><th scope="col">Valid bars</th>'
                 '<th scope="col">Quarantined bars</th><th scope="col">Source snapshot</th>'
                 '<th scope="col">Freshness</th><th scope="col">Source status</th>'
                 '<th scope="col">Source admission</th>'
                 f'</tr></thead><tbody>{body}</tbody></table></section>')
    return html


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
        evidence = state['coverage'][key]['observation_evidence']
        content += '<p>Receipt reader observation: ' + escape(
            evidence['observed_at'] if evidence else 'UNKNOWN') + (
            '. Classification time only; not source freshness or current coverage.</p>')
        if evidence:
            for row in evidence['receipt_windows']:
                bounds = row['verification_window']
                window = (bounds['decision_at'] + ' through ' + bounds['valid_until']
                          + ' (expiry exclusive)' if bounds else 'UNKNOWN')
                content += '<p>' + escape(row['symbol']) + ' verification window: ' + escape(window) + '</p>'
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
        content += render_identities(state['identities'])
        content += render_daily_observations(state['daily_observations'])
        for key, value in state['markets'].items():
            content += render_operational(value, fragment=True,
                                          heading=f'{key} operational Paper/Shadow')
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
        content += render_scan_runs(state['scan_runs'])
    if section == 'RESEARCH':
        content += render_research(state['research'])
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
