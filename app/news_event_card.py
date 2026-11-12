"""Read-only Trade Card event-context section (trade-card-event-context-v1).

``card_event_context`` binds ONE news-event-context-v1 ``EventContext`` to ONE
exact ``TradeDecision`` and returns a separate JSON-ready section for display
beside the Trade Card. It is a pure function: no IO, provider, store, route or
caller. It does not call or change ``trade_card``; the immutable trade plan,
``TradeDecision.event_risk`` (UNKNOWN) and card/report bytes are unchanged.

Binding rules (fail closed with ValueError):

- exact ``TradeDecision`` and ``EventContext`` instances, both revalidated;
- same market and same canonical instrument UUID (symbol alone is never used);
- the context decision time must be the SAME instant as the decision's
  detection time. A context taken later could carry news retained after the
  decision (look-ahead); one taken earlier would understate what was available.

The section is labelled inference, never an observed market fact. Absence of
events is never proof of absence, and it carries no trading effect, risk
approval, plan change or execution permission.
"""
from hashlib import sha256
import json

from app.news_event_context import EventContext
from app.strategies.trade_decision import TradeDecision

CONTRACT_VERSION = 'trade-card-event-context-v1'


def _freshness(context: EventContext) -> str:
    if context.coverage == 'NOT_CONFIGURED':
        return 'NOT_CONFIGURED'
    return 'STALE' if context.status == 'STALE' else 'WITHIN_POLICY'


def card_event_context(decision: TradeDecision, context: EventContext) -> dict:
    """Separate event-context section for one decision; never edits the card."""
    if type(decision) is not TradeDecision:
        raise ValueError('exact TradeDecision required')
    if type(context) is not EventContext:
        raise ValueError('exact EventContext required')
    decision = TradeDecision.model_validate(decision.model_dump(mode='python'))
    context = EventContext.model_validate(context.model_dump(mode='python'))
    s, c = decision.source, decision.candidate
    if context.market != s.market:
        raise ValueError('event context market differs from decision market')
    if context.instrument_id != s.instrument_id:
        raise ValueError('event context instrument differs from decision instrument')
    if context.decision_time != c.decision_time:
        raise ValueError('event context must be taken at the decision detection time')
    payload = context.model_dump(mode='json')
    return {
        'contract_version': CONTRACT_VERSION,
        'decision_id': decision.identity,
        'evidence_layer': 'NEWS_EVENT_INFERENCE',
        'observed_market_fact': False,
        'association_kind': context.association_kind,
        'market': context.market,
        'instrument_id': str(context.instrument_id),
        'decision_time': payload['decision_time'],
        'event_context_status': context.status,
        'news_event_as_of': payload['coverage_as_of'],
        'coverage': context.coverage,
        'freshness': _freshness(context),
        'policy': payload['policy'],
        'categories': list(context.categories),
        'relevant_event_ids': list(context.relevant_event_ids),
        'ambiguous_event_ids': list(context.ambiguous_event_ids),
        'unattributed_event_count': context.unattributed_event_count,
        'absence_is_proof': False,
        'context_contract_version': context.contract_version,
        'context_sha256': sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                            allow_nan=False).encode()).hexdigest(),
        'trade_decision_event_risk': decision.event_risk,
        'risk_interpretation': context.risk_interpretation,
        'trading_effect': 'NONE',
        'plan_effect': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
    }
