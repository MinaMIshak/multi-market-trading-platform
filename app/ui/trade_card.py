"""Read-only Scanner v2 card rendering; callers supply validated records."""
from datetime import datetime
from html import escape

from app.scanner_v2_card_report_loader import card_current_at
from app.strategies.trade_decision import TradeDecision, trade_card

_PAGE_HEAD = (
    '<!doctype html><html><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Scanner v2 EGX Trade Cards</title><style>body{font:16px sans-serif;max-width:1100px;'
    'margin:32px auto;padding:20px;background:#071019;color:#e9f0f5}a{color:#8bd5b0}'
    'table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;'
    'border-bottom:1px solid #344653;overflow-wrap:anywhere}</style></head><body>'
    '<nav><a href="/">TODAY</a> · <a href="/system">SYSTEM</a></nav>'
    '<h1>Scanner v2 EGX Trade Cards</h1>'
    '<p>PAPER/SHADOW only. LIVE_MONEY=DISABLED. A candidate is not a fill. A setup match is '
    'not evidence of edge: risk is not evaluated and confidence is unvalidated.</p>')


def _table(rows) -> str:
    """Escaped two-column table; missing values appear as UNKNOWN."""
    return '<table>' + ''.join(
        '<tr><th scope="row">' + escape(label) + '</th><td>'
        + ('UNKNOWN' if value is None else escape(str(value))) + '</td></tr>'
        for label, value in rows) + '</table>'


def _counts(mapping) -> str:
    return ', '.join(f'{key}={mapping[key]}' for key in sorted(mapping)) or 'NONE'


def render_card_projection(card: dict) -> str:
    """Render one ``trade_card`` projection; labels are as of its ``observed_at``."""
    fact = card['observed_fact']
    inference = card['strategy_inference']
    plan = inference['plan']
    risk = card['risk_decision']
    research = card['research_evidence']
    groups = (
        ('Observed fact', (
            ('Market / symbol', f"{fact['market']} / {fact['symbol']}"),
            ('DATA AS OF', fact['data_as_of']),
            ('Detected at', fact['detected_at']),
            ('Fresh until', fact['provenance']['fresh_until']),
            ('Source', fact['provenance']['source_id']),
            ('Artifact SHA256', fact['provenance']['artifact_sha256']),
            ('Evidence references', ', '.join(fact['provenance']['evidence_ids'])),
        )),
        ('Strategy inference — unvalidated challenger', (
            ('WHY FOUND', inference['why_found']),
            ('SETUP', f"{inference['setup']} v{inference['setup_version']}"),
            ('TRIGGER', inference['trigger']),
            ('ENTRY', f"{plan['entry_low']}–{plan['entry_high']} (reference {plan['entry_reference']})"),
            ('STOP', plan['stop']), ('TARGET', plan['target']),
            ('Valid until', plan['valid_until']),
            ('Max holding sessions', plan['max_holding_sessions']),
            ('Flat by', plan['flat_by'] or 'NOT_SPECIFIED'),
        )),
        ('Risk decision', (
            ('RISK', risk['status']), ('Risk per share', risk['risk_per_share']),
            ('EVENT RISK', risk['event_risk']),
        )),
        ('Research evidence', (
            ('EVIDENCE', research['signal_evidence_json']),
            ('CONFIDENCE', research['confidence']),
            ('Candidate SHA256', research['candidate_sha256']),
        )),
        ('Paper/Shadow status', (
            ('PAPER/SHADOW STATUS', card['mode'] + ' / ' + card['status']),
            ('INVALIDATION REASON', card['invalidation_reason'] or 'NONE_REPORTED'),
            ('Decision ID', card['decision_id']),
        )),
    )
    parts = ['<article><h2>Trade Card: ' + escape(str(fact['symbol'])) + '</h2>'
             '<p>Research candidate only. Candidate != fill. '
             'LIVE_MONEY=DISABLED. Risk admission and execution are not authorized.</p>']
    for title, rows in groups:
        parts.append('<h3>' + escape(title) + '</h3>' + _table(rows))
    return ''.join(parts) + '</article>'


def render_trade_card(decision: TradeDecision, *, observed_at: datetime) -> str:
    return render_card_projection(trade_card(decision, observed_at=observed_at))


def render_card_report_page(response: dict) -> str:
    """Render the protected loader's response as a read-only HTML page.

    Cards appear only for an AVAILABLE response. Each keeps the labels the
    producer computed at report time and is preceded by its state at load, so
    an old report is never presented as current.
    """
    report = response['report']
    if response['status'] != 'AVAILABLE' or report is None:
        return (_PAGE_HEAD + '<section><h2>Report: ' + escape(str(response['status'])) + '</h2>'
                '<p>No report is shown. This does not mean that no candidates exist.</p>'
                + _table((('Reason', response['reason']), ('Detail', response['detail']),
                          ('Loaded at', response['loaded_at'])))
                + '</section></body></html>')

    coverage = report['coverage']
    provenance = report['provenance']
    lifecycle = report['lifecycle_evidence']
    binding = provenance['retained_evidence']['binding']
    cards = report['cards']
    stale = response['load_freshness'] != 'WITHIN_RETAINED_WINDOW'
    notice = ('This report is historical and its cards are not current.' if stale else
              'The retained data window still holds at load.')
    parts = [
        _PAGE_HEAD, '<section><h2>Report: ', escape(str(response['load_freshness'])), '</h2><p>',
        notice, ' Card labels below were computed at report time.</p>',
        _table((('Loaded at', response['loaded_at']),
                ('Report observed at', response['report_observed_at']),
                ('Fresh until', binding['fresh_until']),
                ('Cards current at load',
                 f"{response['cards_current_at_load']} of {len(cards)}"),
                ('Card states at report time', _counts(report['card_state_counts'])),
                ('Report SHA256', response['report_sha256']))),
        '</section><section><h2>Coverage</h2><p>Published ranking scope only; '
        'not the authoritative EGX universe.</p>',
        _table((('Scope', coverage['scope_kind']),
                ('Authoritative universe size', coverage['authoritative_universe_size']),
                ('Requested', coverage['requested']),
                ('Candidates', coverage['candidate_count']),
                ('Excluded', coverage['excluded']),
                ('Classifications', _counts(coverage['classification_counts'])),
                ('Exclusions', _counts(coverage['exclusion_counts'])),
                ('Identity exclusions', len(coverage['identity_exclusions'])))),
        '</section><section><h2>Provenance</h2>',
        _table((('Session', provenance['session']),
                ('Ranking generated at', provenance['generated_at']),
                ('Evidence available at', provenance['evidence_available_at']),
                ('Ranking artifact SHA256', provenance['artifact_sha256']),
                ('Rank version', provenance['rank_version']),
                ('Adapter version', provenance['adapter_version']),
                ('Config', provenance['config_id']),
                ('Freshness policy', provenance['freshness_policy']),
                ('Identity admission', provenance['identity_admission']),
                ('Calendar admission', provenance['calendar_admission']))),
        '</section><section><h2>Evidence status</h2><p>No measured performance is bound to '
        'these cards. They are not an expected return.</p>',
        _table((('Lifecycle evidence', lifecycle['status']),
                ('Sample size', lifecycle['sample_size']),
                ('Net result', lifecycle['net_result']),
                ('Research status', report['research_status']),
                ('Risk status', report['risk_status']),
                ('Promotion', report['promotion']))),
        '<p>Limitations: ', escape(', '.join(str(item) for item in report['limitations'])),
        '</p></section>',
    ]
    if not cards:
        parts.append('<section><p>No Trade Cards in this report.</p></section>')
    loaded_at = datetime.fromisoformat(response['loaded_at'])
    fresh_until = datetime.fromisoformat(binding['fresh_until'])
    for card in cards:
        state = 'CURRENT' if card_current_at(card, fresh_until, loaded_at) else 'NOT_CURRENT'
        parts.append('<section><p><strong>At load: ' + state + '</strong></p>'
                     + render_card_projection(card) + '</section>')
    return ''.join(parts) + '</body></html>'
