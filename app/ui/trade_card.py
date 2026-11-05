"""Read-only Scanner v2 card fragment; callers supply validated decision records."""
from datetime import datetime
from html import escape

from app.strategies.trade_decision import TradeDecision, trade_card


def render_trade_card(decision: TradeDecision, *, observed_at: datetime) -> str:
    card = trade_card(decision, observed_at=observed_at)
    fact = card['observed_fact']
    inference = card['strategy_inference']
    plan = inference['plan']
    risk = card['risk_decision']
    research = card['research_evidence']
    groups = (
        ('Observed fact', (
            ('Market / symbol', f"{fact['market']} / {fact['symbol']}"),
            ('DATA AS OF', fact['data_as_of']),
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
    parts = ['<article><h2>Trade Card</h2><p>Research candidate only. Candidate != fill. '
             'LIVE_MONEY=DISABLED. Risk admission and execution are not authorized.</p>']
    for title, rows in groups:
        parts.append('<h3>' + escape(title) + '</h3><table>')
        for label, value in rows:
            parts.append('<tr><th scope="row">' + escape(label) + '</th><td>'
                         + escape(str(value)) + '</td></tr>')
        parts.append('</table>')
    return ''.join(parts) + '</article>'
