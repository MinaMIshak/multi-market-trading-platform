"""Scanner v2 reporting contract; no risk admission, execution or alpha claim.

Market adapters must supply admitted provenance and explicit decimal plan terms.
This boundary checks internal consistency, not provider authority or calendars.
"""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.models import TradePlan
from app.paper.shadow_candidate_admission import strategy_candidate_sha256
from app.strategies.contracts import Candidate, Contract, aware


class DecisionSource(Contract):
    market: Literal['EGX', 'US']
    symbol: str = Field(min_length=1, pattern=r'^\S+$')
    instrument_id: UUID
    source_id: str = Field(min_length=1, pattern=r'\S')
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    data_as_of: datetime
    available_at: datetime
    fresh_until: datetime
    completed_bar: Literal[True]

    @model_validator(mode='after')
    def check(self):
        if not (aware(self.data_as_of) <= aware(self.available_at) < aware(self.fresh_until)):
            raise ValueError('invalid source observation window')
        if any(not item.strip() for item in self.evidence_ids):
            raise ValueError('nonempty evidence references required')
        return self


class DecisionTerms(Contract):
    entry_trigger: Literal['LONG_ENTRY_ZONE_TOUCH_V1']
    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    entry_reference: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    valid_until: datetime
    max_holding_sessions: int = Field(gt=0)
    flat_by: datetime | None = None


class TradeDecision(Contract):
    contract_version: Literal['trade-decision-v1'] = 'trade-decision-v1'
    candidate: Candidate
    source: DecisionSource
    terms: DecisionTerms
    mode: Literal['SHADOW'] = 'SHADOW'
    live_money: Literal[False] = False
    execution_allowed: Literal[False] = False
    risk_status: Literal['NOT_EVALUATED'] = 'NOT_EVALUATED'
    confidence: Literal['UNVALIDATED'] = 'UNVALIDATED'
    event_risk: Literal['UNKNOWN'] = 'UNKNOWN'
    promotion: Literal['NOT_AUTHORIZED'] = 'NOT_AUTHORIZED'

    @model_validator(mode='after')
    def check(self):
        # Revalidate nested instances too: model_copy/model_construct bypass checks.
        c = Candidate.model_validate(self.candidate.model_dump(mode='python'))
        s = DecisionSource.model_validate(self.source.model_dump(mode='python'))
        t = DecisionTerms.model_validate(self.terms.model_dump(mode='python'))
        if c.state not in ('READY', 'CONTINUATION_CONFIRMED', 'REVERSAL_CONFIRMED'):
            raise ValueError('candidate is not a confirmed setup')
        if c.symbol != s.symbol or c.data_cutoff != s.data_as_of:
            raise ValueError('candidate/source identity or cutoff mismatch')
        if not (s.available_at <= c.decision_time < s.fresh_until):
            raise ValueError('source unavailable or stale at detection')
        if t.flat_by is not None and not (c.decision_time < aware(t.flat_by) <= aware(t.valid_until)):
            raise ValueError('invalid flat-by window')
        # Reuse canonical geometry; never convert float references to plan prices.
        TradePlan(signal_id=UUID(int=0), trade_plan_id=UUID(int=0), symbol=c.symbol,
                  entry_low=t.entry_low, entry_high=t.entry_high,
                  entry_reference=t.entry_reference, stop_price=t.stop, target_1=t.target,
                  created_at=c.decision_time, valid_until=aware(t.valid_until))
        return self

    @property
    def identity(self) -> str:
        payload = self.model_dump(mode='json')
        return sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                 allow_nan=False).encode()).hexdigest()


def trade_card(decision: TradeDecision, *, observed_at: datetime) -> dict:
    """JSON-ready read-only projection; expired plans stay immutable and labeled."""
    if type(decision) is not TradeDecision:
        raise ValueError('exact TradeDecision required')
    decision = TradeDecision.model_validate(decision.model_dump(mode='python'))
    c, s, t = decision.candidate, decision.source, decision.terms
    now = aware(observed_at)
    if now < c.decision_time:
        raise ValueError('card observation precedes detection')
    invalidation = None
    if now >= t.valid_until or (t.flat_by is not None and now >= t.flat_by):
        invalidation = 'PLAN_EXPIRED'
    elif now >= s.fresh_until:
        invalidation = 'DATA_STALE'
    return {
        'contract_version': 'trade-card-v1', 'decision_id': decision.identity,
        'observed_at': now.astimezone(timezone.utc).isoformat(),
        'observed_fact': {'market': s.market, 'symbol': s.symbol,
                          'data_as_of': s.data_as_of.isoformat(),
                          'detected_at': c.decision_time.isoformat(),
                          'provenance': s.model_dump(mode='json')},
        'strategy_inference': {'why_found': c.state, 'setup': c.strategy_id,
                               'setup_version': c.strategy_version, 'config_id': c.config_id,
                               'trigger': t.entry_trigger, 'plan': t.model_dump(mode='json')},
        'risk_decision': {'status': decision.risk_status,
                          'risk_per_share': str(t.entry_reference - t.stop),
                          'event_risk': decision.event_risk},
        'research_evidence': {'status': 'UNVALIDATED', 'confidence': decision.confidence,
                              'candidate_sha256': strategy_candidate_sha256(c),
                              'signal_evidence_json': c.evidence_json},
        'mode': decision.mode, 'live_money': False, 'execution_allowed': False,
        'promotion': decision.promotion, 'invalidation_reason': invalidation,
        'status': 'EXCLUDED' if invalidation else 'RESEARCH_CANDIDATE',
    }
