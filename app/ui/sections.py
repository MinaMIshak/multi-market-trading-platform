"""LIVE and PRE-SURGE product state; truthful about delay and missing inputs.

LIVE is near-current monitoring only when an admitted intraday or delayed
feed exists; none does, so it shows daily observations with their declared
source delay, latest market date and session-derived freshness, never as
real-time. PRE-SURGE exposes the PreSurgeV7Engine contract and its unmet
inputs; it never ranks without attested scorer rows and admitted data.
"""
from html import escape

from app.data.source_admission import daily_source_admission

# Intraday/delayed transports: EGID intraday is not implemented and no
# delayed-quote adapter exists, so near-current monitoring is unavailable.
NEAR_CURRENT_FEED = {'status': 'UNAVAILABLE',
                     'reason': 'No admitted intraday or delayed-quote feed is implemented'}


def source_delay(provider, market):
    declaration = daily_source_admission(provider, market).declaration
    return declaration.delay.value if declaration is not None else 'UNKNOWN'


def live_state(daily_rows):
    if daily_rows is None:
        rows = None
    else:
        rows = [{'symbol': r.get('canonical_symbol'), 'provider': r.get('provider'),
                 'source_delay': r.get('source_delay') or 'UNKNOWN',
                 'latest_market_date': r.get('newest_market_date'),
                 'freshness': r.get('freshness') or 'UNKNOWN',
                 'source_status': r.get('source_status') or 'EVIDENCE_BLOCKED'}
                for r in daily_rows]
    return {'near_current_feed': dict(NEAR_CURRENT_FEED), 'real_time': False,
            'daily_observations': rows}


PRE_SURGE_CONTRACT = {
    'engine': 'PreSurgeV7Engine', 'strategy_version': '7',
    'scorer_contract': 'LEGACY_V5_PARITY_SEED',
    'score': ('rank(p_top10) + 0.25 rank(p_close8) + 0.15 rank(expected_close) '
              '+ 0.05 rank(turnover) - risk_penalty * rank(p_stop5); '
              'average-tie percentile ranks within one signal-date cohort'),
    'eligibility': 'today_return < 0.05; scorer trained strictly before the signal date; '
                   'row available by decision time',
    'model': 'NONE SHIPPED: scorer rows must come from an attested external model',
    'interpretation': 'Research ranking of a cohort (WATCH at most); not a prediction, '
                      'signal, fill or profitability claim',
}


def pre_surge_state(readiness):
    """Report unmet inputs; no scorer-row reader is connected in this revision."""
    blockers = ['NO_ATTESTED_SCORER_ROWS']
    if readiness is None or readiness.get('source_admission') != 'ADMITTED_SOURCE_PRESENT':
        blockers.insert(0, 'NO_ADMITTED_DAILY_SOURCE')
    status = 'EVIDENCE_BLOCKED' if 'NO_ADMITTED_DAILY_SOURCE' in blockers else 'UNAVAILABLE'
    return {'status': status, 'candidates': None, 'scorer_rows_connected': False,
            'blockers': blockers, 'contract': dict(PRE_SURGE_CONTRACT)}


def render_live(live):
    content = ''
    for key, value in live.items():
        if value is None:
            content += f'<p>{key} LIVE monitoring: UNKNOWN (no reader connected).</p>'
            continue
        feed = value['near_current_feed']
        content += (f'<section aria-label="{key} LIVE"><h2>{key} near-current feed: '
                    f'{escape(feed["status"])}</h2><p>{escape(feed["reason"])}. '
                    'Nothing below is real-time; rows are daily observations with their '
                    'declared source delay.</p>')
        rows = value['daily_observations']
        if rows is None:
            content += '<p>Daily observations: UNKNOWN.</p></section>'
            continue
        if not rows:
            content += '<p>Daily observations: none recorded.</p></section>'
            continue
        content += (f'<table aria-label="{key} LIVE observations"><thead><tr>'
                    + ''.join(f'<th scope="col">{h}</th>' for h in (
                        'Symbol', 'Source', 'Source delay', 'Latest market date',
                        'Freshness', 'Source status'))
                    + '</tr></thead><tbody>')
        for row in rows:
            content += '<tr>' + ''.join('<td>' + escape(str(row[k])) + '</td>' for k in (
                'symbol', 'provider', 'source_delay', 'latest_market_date', 'freshness',
                'source_status')) + '</tr>'
        content += '</tbody></table></section>'
    return content


def render_pre_surge(pre_surge):
    content = ''
    for key, value in pre_surge.items():
        if value is None:
            content += f'<p>{key} PRE-SURGE: UNKNOWN (no reader connected).</p>'
            continue
        contract = value['contract']
        content += (f'<section aria-label="{key} PRE-SURGE"><h2>{key} PRE-SURGE: '
                    f'{escape(value["status"])}</h2><p>Blockers: '
                    + escape(', '.join(value['blockers'])) + '. Candidates: UNKNOWN.</p>'
                    '<table aria-label="PRE-SURGE rule contract"><tbody>'
                    + ''.join(f'<tr><th scope="row">{escape(k)}</th><td>{escape(v)}</td></tr>'
                              for k, v in contract.items())
                    + '</tbody></table></section>')
    return content
