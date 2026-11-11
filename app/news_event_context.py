"""Per-instrument News/Event context (news-event-context-v1); evidence enrichment only.

``project_event_context`` summarises what retained news-event-v1 evidence was
available for ONE canonical instrument at ONE decision time. It is a pure
function: no IO, provider, storage, route or caller, and it is not attached to
TradeDecision or the Trade Card (``event_risk`` there stays UNKNOWN). It never
edits a trade plan and cannot produce BUY/SELL, admission, risk approval or
execution.

Status precedence (first match wins):

1. UNAVAILABLE    - no retained source coverage for the market;
2. STALE          - the latest coverage at or before decision time is older than
                    the policy allows (any found events are still listed);
3. EVENT_FOUND    - at least one MATCHED event for this instrument in the window;
4. AMBIGUOUS      - an AMBIGUOUS event lists this instrument among candidates;
5. UNKNOWN        - events in the window could not be attributed (UNRESOLVED /
                    IDENTITY_UNAVAILABLE), so absence cannot be stated;
6. NO_EVENT_FOUND - nothing attributable was found. This is NOT proof that no
                    relevant event exists (``absence_is_proof`` is fixed False).

Association is inference, never observed fact; ``association_kind`` says so.
"""
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.news_event import EventCategory, NewsEvent, select_point_in_time
from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'news-event-context-v1'

EventContextStatus = Literal['EVENT_FOUND', 'NO_EVENT_FOUND', 'STALE', 'UNAVAILABLE', 'AMBIGUOUS', 'UNKNOWN']


class EventContextPolicy(Contract):
    """Explicit, versioned window and freshness bounds; no implicit defaults."""
    policy_version: str = Field(min_length=1, pattern=r'^\S+$')
    lookback_seconds: int = Field(ge=1)
    max_coverage_age_seconds: int = Field(ge=1)


class EventContext(Contract):
    contract_version: Literal['news-event-context-v1'] = 'news-event-context-v1'
    market: Literal['EGX', 'US']
    instrument_id: UUID
    decision_time: datetime
    policy: EventContextPolicy
    coverage: Literal['NOT_CONFIGURED', 'RETAINED']
    coverage_as_of: datetime | None = None
    status: EventContextStatus
    relevant_event_ids: tuple[str, ...] = ()
    ambiguous_event_ids: tuple[str, ...] = ()
    unattributed_event_count: int = Field(default=0, ge=0)
    categories: tuple[EventCategory, ...] = ()
    association_kind: Literal['INFERRED'] = 'INFERRED'
    absence_is_proof: Literal[False] = False
    risk_interpretation: Literal['NOT_EVALUATED'] = 'NOT_EVALUATED'
    trading_effect: Literal['NONE'] = 'NONE'
    mode: Literal['SHADOW'] = 'SHADOW'
    live_money: Literal[False] = False
    execution_allowed: Literal[False] = False

    @model_validator(mode='after')
    def check(self):
        aware(self.decision_time)
        EventContextPolicy.model_validate(self.policy.model_dump(mode='python'))
        for ids in (self.relevant_event_ids, self.ambiguous_event_ids):
            if any(not _is_digest(i) for i in ids) or list(ids) != sorted(set(ids)):
                raise ValueError('event ids must be sorted unique SHA-256 digests')
        if list(self.categories) != sorted(set(self.categories)):
            raise ValueError('categories must be sorted and unique')
        if bool(self.categories) != bool(self.relevant_event_ids):
            raise ValueError('categories describe relevant events only')
        if self.coverage == 'NOT_CONFIGURED':
            if (self.coverage_as_of is not None or self.status != 'UNAVAILABLE' or self.relevant_event_ids
                    or self.ambiguous_event_ids or self.unattributed_event_count):
                raise ValueError('without coverage only an empty UNAVAILABLE context is valid')
            return self
        if self.coverage_as_of is None or aware(self.coverage_as_of) > self.decision_time:
            raise ValueError('retained coverage must be observed at or before decision time')
        expected = _status(self.decision_time, self.coverage_as_of, self.policy, self.relevant_event_ids,
                           self.ambiguous_event_ids, self.unattributed_event_count)
        if self.status != expected:
            raise ValueError(f'status must be {expected}')
        return self


def _status(decision_time, coverage_as_of, policy, relevant, ambiguous, unattributed) -> str:
    if decision_time - coverage_as_of > timedelta(seconds=policy.max_coverage_age_seconds):
        return 'STALE'
    if relevant:
        return 'EVENT_FOUND'
    if ambiguous:
        return 'AMBIGUOUS'
    return 'UNKNOWN' if unattributed else 'NO_EVENT_FOUND'


def _is_digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def project_event_context(events, *, market: str, instrument_id: UUID, decision_time: datetime,
                          policy: EventContextPolicy,
                          coverage_as_of: datetime | None) -> EventContext:
    """Context for one instrument from evidence available at ``decision_time``.

    ``coverage_as_of`` is when the platform last retained a source pass for this
    market at or before ``decision_time`` (None: no source configured/retained).
    A later coverage time is rejected, never clipped. Events are first reduced by
    ``select_point_in_time`` (observed_at <= decision_time, latest available
    revision), then kept only if published within the policy lookback.
    """
    cutoff = aware(decision_time)
    if type(instrument_id) is not UUID:
        raise ValueError('canonical instrument UUID required')
    if type(policy) is not EventContextPolicy:
        raise ValueError('exact EventContextPolicy required')
    policy =EventContextPolicy.model_validate(policy.model_dump(mode='python'))
    available = select_point_in_time(events, market=market, decision_time=cutoff)
    base = dict(market=market, instrument_id=instrument_id, decision_time=cutoff, policy=policy)
    if coverage_as_of is None:
        return EventContext(**base, coverage='NOT_CONFIGURED', status='UNAVAILABLE')
    if aware(coverage_as_of) > cutoff:
        raise ValueError('coverage observed after decision time')
    window_start = cutoff - timedelta(seconds=policy.lookback_seconds)
    relevant: list[NewsEvent] = []
    ambiguous: list[str] = []
    unattributed = 0
    for event in available:
        if event.source.published_at < window_start:
            continue
        a = event.association
        if a.status == 'MATCHED':
            if a.instrument_id == instrument_id:
                relevant.append(event)
        elif a.status == 'AMBIGUOUS':
            if instrument_id in a.candidate_instrument_ids:
                ambiguous.append(event.identity)
        else:
            unattributed += 1
    status = _status(cutoff, coverage_as_of, policy, relevant, ambiguous, unattributed)
    return EventContext(
        **base, coverage='RETAINED', coverage_as_of=coverage_as_of, status=status,
        relevant_event_ids=tuple(sorted({e.identity for e in relevant})),
        ambiguous_event_ids=tuple(sorted(set(ambiguous))),
        unattributed_event_count=unattributed,
        categories=tuple(sorted({e.classification.category for e in relevant})))
