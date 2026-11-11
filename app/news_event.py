"""News/Event evidence contract (news-event-v1); evidence enrichment only.

A NewsEvent records that a retained external publication was observed. It keeps
four layers apart and never presents one as another:

- ``source``: source facts (who published, when, which retained bytes);
- ``association``: inferred relevance to one market instrument (exact
  identifiers only, no fuzzy matching; ambiguity and unresolved entities stay
  visible and carry no instrument);
- ``classification``: derived category, UNKNOWN when unclassified;
- ``risk_interpretation``: fixed NOT_EVALUATED.

It cannot produce BUY/SELL, admission, risk approval or execution:
``trading_effect`` is fixed to NONE, mode SHADOW, LIVE_MONEY=false. There is no
IO, provider, route or caller. ``select_point_in_time`` is the only read rule:
an event is usable for a decision only if it was *observed* by the platform at
or before the decision time, so later publications and later revisions never
leak into earlier decisions.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal, get_args
from uuid import UUID

from pydantic import Field, model_validator

from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'news-event-v1'

Market = Literal['EGX', 'US']
EventCategory = Literal['EARNINGS', 'GUIDANCE', 'CORPORATE_ACTION', 'DIVIDEND', 'CAPITAL_RAISE',
                        'M&A', 'MANAGEMENT_CHANGE', 'REGULATORY', 'LEGAL', 'TRADING_HALT',
                        'MATERIAL_OPERATIONAL_EVENT', 'MACRO', 'MARKET_STRUCTURE', 'OTHER', 'UNKNOWN']
EVENT_CATEGORIES = get_args(EventCategory)


def _digest(payload) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


class NewsEventSource(Contract):
    """Source facts only. ``observed_at`` is when this platform retained the bytes."""
    source_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,63}$')
    source_event_id: str = Field(min_length=1, max_length=256, pattern=r'^\S+$')
    revision: int = Field(ge=0)
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    published_at: datetime
    observed_at: datetime
    event_time_status: Literal['REPORTED', 'UNKNOWN']
    event_at: datetime | None = None

    @model_validator(mode='after')
    def check(self):
        if aware(self.published_at) > aware(self.observed_at):
            raise ValueError('observation precedes publication')
        if (self.event_time_status == 'REPORTED') != (self.event_at is not None):
            raise ValueError('event time status must match event_at presence')
        if self.event_at is not None:
            aware(self.event_at)
        return self


class InstrumentAssociation(Contract):
    """Inferred relevance; never a guessed identity."""
    status: Literal['MATCHED', 'AMBIGUOUS', 'UNRESOLVED', 'IDENTITY_UNAVAILABLE']
    market: Market
    basis: Literal['EXACT_SOURCE_IDENTIFIER', 'NONE']
    confidence: Literal['EXACT', 'NONE']
    symbol: str | None = Field(default=None, min_length=1, pattern=r'^\S+$')
    instrument_id: UUID | None = None
    candidate_instrument_ids: tuple[UUID, ...] = ()
    mapping_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def check(self):
        if any(not item.strip() for item in self.mapping_evidence_ids):
            raise ValueError('nonempty mapping evidence references required')
        if len(set(self.candidate_instrument_ids)) != len(self.candidate_instrument_ids):
            raise ValueError('duplicate candidate instruments')
        if self.status == 'MATCHED':
            if (self.symbol is None or self.instrument_id is None or self.candidate_instrument_ids
                    or self.basis != 'EXACT_SOURCE_IDENTIFIER' or self.confidence != 'EXACT'
                    or not self.mapping_evidence_ids):
                raise ValueError('MATCHED requires exact identifier, symbol, instrument and evidence')
            return self
        if self.instrument_id is not None or self.symbol is not None or self.confidence != 'NONE':
            raise ValueError('only MATCHED may name an instrument or claim confidence')
        if self.status == 'AMBIGUOUS':
            if len(self.candidate_instrument_ids) < 2 or not self.mapping_evidence_ids:
                raise ValueError('AMBIGUOUS requires two or more candidates and evidence')
        elif self.candidate_instrument_ids or self.basis != 'NONE':
            raise ValueError('unresolved association carries no candidates or basis')
        return self


class EventClassification(Contract):
    """Derived classification; UNKNOWN means not classified, not 'no event'."""
    category: EventCategory
    basis: Literal['SOURCE_LABEL', 'RULE', 'UNCLASSIFIED']
    classifier_version: str | None = Field(default=None, min_length=1, pattern=r'^\S+$')

    @model_validator(mode='after')
    def check(self):
        if (self.category == 'UNKNOWN') != (self.basis == 'UNCLASSIFIED'):
            raise ValueError('UNKNOWN category iff UNCLASSIFIED basis')
        if (self.basis == 'UNCLASSIFIED') != (self.classifier_version is None):
            raise ValueError('classified events require a classifier version')
        return self


class NewsEvent(Contract):
    contract_version: Literal['news-event-v1'] = 'news-event-v1'
    market: Market
    source: NewsEventSource
    association: InstrumentAssociation
    classification: EventClassification
    ingestion_status: Literal['RETAINED'] = 'RETAINED'
    risk_interpretation: Literal['NOT_EVALUATED'] = 'NOT_EVALUATED'
    trading_effect: Literal['NONE'] = 'NONE'
    mode: Literal['SHADOW'] = 'SHADOW'
    live_money: Literal[False] = False
    execution_allowed: Literal[False] = False

    @model_validator(mode='after')
    def check(self):
        # Revalidate nested instances: model_copy/model_construct bypass checks.
        NewsEventSource.model_validate(self.source.model_dump(mode='python'))
        a = InstrumentAssociation.model_validate(self.association.model_dump(mode='python'))
        EventClassification.model_validate(self.classification.model_dump(mode='python'))
        if a.market != self.market:
            raise ValueError('association market differs from event market')
        return self

    @property
    def dedup_key(self) -> str:
        """Same publication across revisions; markets never share a key."""
        s = self.source
        return _digest({'contract_version': self.contract_version, 'market': self.market,
                        'source_id': s.source_id, 'source_event_id': s.source_event_id})

    @property
    def identity(self) -> str:
        """Digest of this exact record, revision included."""
        return _digest(self.model_dump(mode='json'))


def select_point_in_time(events, *, market: str, decision_time: datetime) -> tuple[NewsEvent, ...]:
    """Events usable at ``decision_time`` for one market, latest available revision each.

    Availability is ``observed_at <= decision_time``; publication time alone is
    not availability. Only available records are inspected for conflicts, so
    records observed later cannot change the result. Output order is
    deterministic: published_at (UTC), source_id, source_event_id.
    """
    cutoff = aware(decision_time)
    if market not in ('EGX', 'US'):
        raise ValueError('unknown market')
    available: dict[tuple[str, int], NewsEvent] = {}
    for event in events:
        if type(event) is not NewsEvent:
            raise ValueError('exact NewsEvent required')
        event = NewsEvent.model_validate(event.model_dump(mode='python'))
        if event.market != market:
            raise ValueError('event market differs from requested market')
        if event.source.observed_at > cutoff:
            continue
        slot = (event.dedup_key, event.source.revision)
        held = available.setdefault(slot, event)
        if held.identity != event.identity:
            raise ValueError('conflicting records for the same event revision')
    latest: dict[str, NewsEvent] = {}
    for (key, revision), event in available.items():
        if key not in latest or revision > latest[key].source.revision:
            latest[key] = event
    return tuple(sorted(latest.values(), key=lambda e: (
        e.source.published_at.astimezone(timezone.utc), e.source.source_id, e.source.source_event_id)))
