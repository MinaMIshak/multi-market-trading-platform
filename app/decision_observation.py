"""Pure per-decision empirical observation record (decision-observation-v1).

Phase G groundwork: ``decision_observation`` captures, for ONE exact
``TradeDecision``, everything later outcome evaluation needs (strategy/setup
id and version, market, symbol, canonical instrument, detection and data
times, the immutable plan terms, risk per share and provenance), plus the
trade-card-event-context-v1 section available at detection time when an
``EventContext`` is supplied. It retains NO outcome: ``outcome_status`` is
fixed NOT_OBSERVED and ``performance_claim`` NONE.

Recording window (fail closed with ValueError): the caller clock
``recorded_at`` must be timezone-aware, at or after the detection time and
strictly before the source ``fresh_until``, ``valid_until`` and ``flat_by``
(when set). A record therefore cannot be written after the decision stopped
being current, so recording cannot be conditioned on later outcomes. The
clock is caller-supplied and is labelled as such; this function provides no
storage, signature or trusted time.

No IO, provider, store, route or caller. ``trade_card``, the plan,
``TradeDecision.event_risk`` and card report bytes are unchanged.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from app.news_event_card import card_event_context
from app.news_event_context import EventContext
from app.paper.shadow_candidate_admission import strategy_candidate_sha256
from app.strategies.contracts import aware
from app.strategies.trade_decision import TradeDecision

CONTRACT_VERSION = 'decision-observation-v1'


def canonical_bytes(value: dict) -> bytes:
    """Canonical JSON bytes (sorted keys, compact, ASCII, no NaN)."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def decision_observation(decision: TradeDecision, context: EventContext | None, *,
                         recorded_at: datetime) -> dict:
    """JSON-ready observation of one decision at recording time; no outcome."""
    if type(decision) is not TradeDecision:
        raise ValueError('exact TradeDecision required')
    if context is not None and type(context) is not EventContext:
        raise ValueError('exact EventContext or None required')
    try:
        aware(recorded_at)
    except ValueError as exc:
        raise ValueError('timezone-aware recorded_at required') from exc
    decision = TradeDecision.model_validate(decision.model_dump(mode='python'))
    c, s, t = decision.candidate, decision.source, decision.terms
    if recorded_at < c.decision_time:
        raise ValueError('observation recorded before detection')
    if recorded_at >= s.fresh_until:
        raise ValueError('observation recorded after source freshness ended')
    if recorded_at >= t.valid_until or (t.flat_by is not None and recorded_at >= t.flat_by):
        raise ValueError('observation recorded after plan expiry')
    section = None if context is None else card_event_context(decision, context)
    payload = decision.model_dump(mode='json')
    return {
        'contract_version': CONTRACT_VERSION,
        'decision_id': decision.identity,
        'decision_contract_version': decision.contract_version,
        'market': s.market,
        'symbol': s.symbol,
        'instrument_id': str(s.instrument_id),
        'strategy_id': c.strategy_id,
        'strategy_version': c.strategy_version,
        'config_id': c.config_id,
        'candidate_state': c.state,
        'candidate_sha256': strategy_candidate_sha256(c),
        'detected_at': payload['candidate']['decision_time'],
        'data_as_of': payload['source']['data_as_of'],
        'available_at': payload['source']['available_at'],
        'fresh_until': payload['source']['fresh_until'],
        'provenance': {'source_id': s.source_id, 'artifact_sha256': s.artifact_sha256,
                       'evidence_ids': list(s.evidence_ids)},
        'terms': payload['terms'],
        'risk_per_share': str(t.entry_reference - t.stop),
        'recorded_at': _utc(recorded_at),
        'recorded_at_clock': 'CALLER_SUPPLIED_UNVERIFIED',
        'event_context': {
            'status': 'NOT_ATTACHED' if section is None else 'ATTACHED',
            'section': section,
            'section_sha256': None if section is None else sha256(canonical_bytes(section)).hexdigest(),
        },
        'outcome_status': 'NOT_OBSERVED',
        'outcome': None,
        'evaluation_status': 'PENDING_OUTCOME',
        'performance_claim': 'NONE',
        'risk_status': decision.risk_status,
        'confidence': decision.confidence,
        'event_risk': decision.event_risk,
        'promotion': decision.promotion,
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
    }
