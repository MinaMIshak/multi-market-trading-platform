from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.news_event import EventClassification, InstrumentAssociation, NewsEvent, NewsEventSource
from app.news_event_card import card_event_context
from app.news_event_context import EventContextPolicy, project_event_context
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision, trade_card


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
CAIRO = timezone(timedelta(hours=2))
TARGET, OTHER = UUID(int=1), UUID(int=2)
POLICY = EventContextPolicy(policy_version='fixture-ctx-1', lookback_seconds=3 * 86400,
                            max_coverage_age_seconds=6 * 3600)


def decision(market='EGX', instrument=TARGET):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1', config_id='fixture',
                          symbol='TEST', decision_time=AT, data_cutoff=AT - timedelta(minutes=1),
                          state='READY', entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64, evidence_ids=('fixture-only',),
                            data_as_of=candidate.data_cutoff, available_at=AT,
                            fresh_until=AT + timedelta(hours=1), completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=Decimal('10'),
                          entry_high=Decimal('11'), entry_reference=Decimal('10.5'),
                          stop=Decimal('9'), target=Decimal('13'),
                          valid_until=AT + timedelta(days=1), max_holding_sessions=2)
    return TradeDecision(candidate=candidate, source=source, terms=terms)


def event(eid, kind='MATCHED', market='EGX', category='DIVIDEND',
          published=AT - timedelta(hours=2), observed=None):
    if kind == 'MATCHED':
        assoc = InstrumentAssociation(status='MATCHED', market=market, basis='EXACT_SOURCE_IDENTIFIER',
                                      confidence='EXACT', symbol='TEST', instrument_id=TARGET,
                                      mapping_evidence_ids=('fixture-map',))
    else:
        assoc = InstrumentAssociation(status=kind, market=market, basis='NONE', confidence='NONE')
    src = NewsEventSource(source_id='fixture_news', source_event_id=eid, revision=0,
                          artifact_sha256='a' * 64, published_at=published,
                          observed_at=observed or published + timedelta(minutes=5),
                          event_time_status='UNKNOWN')
    return NewsEvent(market=market, source=src, association=assoc,
                     classification=EventClassification(category=category, basis='SOURCE_LABEL',
                                                        classifier_version='src-1'))


def context(events, decision_time=AT, coverage=AT - timedelta(hours=1), market='EGX', instrument=TARGET):
    return project_event_context(events, market=market, instrument_id=instrument,
                                 decision_time=decision_time, policy=POLICY, coverage_as_of=coverage)


def test_event_found_section_is_labelled_inference_and_bound_to_decision():
    d, a = decision(), event('a')
    ctx = context([a])
    section = card_event_context(d, ctx)
    assert section['decision_id'] == d.identity
    assert section['evidence_layer'] == 'NEWS_EVENT_INFERENCE'
    assert section['observed_market_fact'] is False and section['association_kind'] == 'INFERRED'
    assert section['event_context_status'] == 'EVENT_FOUND'
    assert section['relevant_event_ids'] == [a.identity] and section['categories'] == ['DIVIDEND']
    payload = ctx.model_dump(mode='json')
    # Timestamps are the context's own JSON form (pydantic renders UTC as 'Z').
    assert section['news_event_as_of'] == payload['coverage_as_of'] == '2026-11-05T11:00:00Z'
    assert section['decision_time'] == payload['decision_time'] == '2026-11-05T12:00:00Z'
    assert section['freshness'] == 'WITHIN_POLICY' and section['coverage'] == 'RETAINED'
    assert section['policy'] == POLICY.model_dump(mode='json')
    assert section['context_sha256'] == sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'))
                                               .encode()).hexdigest()
    assert json.loads(json.dumps(section, allow_nan=False)) == section
    assert section == card_event_context(d, context([a]))  # deterministic


def test_section_carries_no_trading_effect_and_leaves_plan_and_card_unchanged():
    d = decision()
    before_decision, before_card = d.model_dump_json(), trade_card(d, observed_at=AT)
    section = card_event_context(d, context([event('a', category='LEGAL')]))
    assert section['trading_effect'] == section['plan_effect'] == 'NONE'
    assert section['live_money'] is section['execution_allowed'] is False
    assert section['mode'] == 'SHADOW' and section['risk_interpretation'] == 'NOT_EVALUATED'
    assert section['trade_decision_event_risk'] == 'UNKNOWN'
    assert d.model_dump_json() == before_decision
    assert trade_card(d, observed_at=AT) == before_card
    assert before_card['risk_decision']['event_risk'] == 'UNKNOWN'
    for key in ('plan', 'entry_low', 'stop', 'target', 'signal', 'status', 'risk_decision'):
        assert key not in section


def test_unknown_and_unavailable_are_not_invented():
    d = decision()
    none = card_event_context(d, context([], coverage=None))
    assert none['event_context_status'] == 'UNAVAILABLE' and none['freshness'] == 'NOT_CONFIGURED'
    assert none['news_event_as_of'] is None and none['coverage'] == 'NOT_CONFIGURED'
    empty = card_event_context(d, context([]))
    assert empty['event_context_status'] == 'NO_EVENT_FOUND' and empty['absence_is_proof'] is False
    unknown = card_event_context(d, context([event('u', kind='UNRESOLVED')]))
    assert unknown['event_context_status'] == 'UNKNOWN' and unknown['unattributed_event_count'] == 1
    stale = card_event_context(d, context([event('a')], coverage=AT - timedelta(days=1)))
    assert stale['event_context_status'] == 'STALE' and stale['freshness'] == 'STALE'


def test_future_news_never_appears_on_earlier_card():
    d = decision()
    late = event('late', published=AT - timedelta(hours=3), observed=AT + timedelta(seconds=1))
    section = card_event_context(d, context([late]))
    assert section['event_context_status'] == 'NO_EVENT_FOUND' and section['relevant_event_ids'] == []
    later_ctx = context([late], decision_time=AT + timedelta(hours=1))
    assert later_ctx.status == 'EVENT_FOUND'
    with pytest.raises(ValueError, match='detection time'):
        card_event_context(d, later_ctx)  # a later context cannot be attached to an earlier decision
    with pytest.raises(ValueError, match='detection time'):
        card_event_context(d, context([], decision_time=AT - timedelta(seconds=1),
                                      coverage=AT - timedelta(hours=1)))


def test_same_instant_in_other_offset_is_accepted():
    section = card_event_context(decision(), context([event('a')], decision_time=AT.astimezone(CAIRO)))
    assert section['decision_time'] == AT.astimezone(CAIRO).isoformat()
    assert section['event_context_status'] == 'EVENT_FOUND'


def test_market_and_instrument_separation_fail_closed():
    with pytest.raises(ValueError, match='market'):
        card_event_context(decision('US'), context([]))
    with pytest.raises(ValueError, match='market'):
        card_event_context(decision(), context([], market='US'))
    with pytest.raises(ValueError, match='instrument'):
        card_event_context(decision(), context([], instrument=OTHER))
    us = card_event_context(decision('US'), context([event('u', market='US')], market='US'))
    assert us['market'] == 'US' and us['event_context_status'] == 'EVENT_FOUND'


def test_exact_types_required_and_tampered_inputs_rejected():
    d, ctx = decision(), context([event('a')])
    with pytest.raises(ValueError):
        card_event_context(d.model_dump(), ctx)
    with pytest.raises(ValueError):
        card_event_context(d, ctx.model_dump())
    with pytest.raises(ValueError):
        card_event_context(d, ctx.model_copy(update={'status': 'NO_EVENT_FOUND'}))
    with pytest.raises(ValueError):
        card_event_context(d, ctx.model_copy(update={'live_money': True}))
    with pytest.raises(ValueError):
        card_event_context(d.model_copy(update={'live_money': True}), ctx)
