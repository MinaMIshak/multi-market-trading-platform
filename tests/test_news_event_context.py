from datetime import datetime, timedelta, timezone
from typing import get_args
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.news_event import EventClassification, InstrumentAssociation, NewsEvent, NewsEventSource
from app.news_event_context import EventContext, EventContextPolicy, project_event_context
from app.strategies.trade_decision import TradeDecision


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
CAIRO = timezone(timedelta(hours=2))
TARGET, OTHER = UUID(int=1), UUID(int=2)
POLICY = EventContextPolicy(policy_version='fixture-ctx-1', lookback_seconds=3 * 86400,
                            max_coverage_age_seconds=6 * 3600)


def association(kind, market='EGX', instrument=TARGET):
    if kind == 'MATCHED':
        return InstrumentAssociation(status='MATCHED', market=market, basis='EXACT_SOURCE_IDENTIFIER',
                                     confidence='EXACT', symbol='TEST', instrument_id=instrument,
                                     mapping_evidence_ids=('fixture-map',))
    if kind == 'AMBIGUOUS':
        return InstrumentAssociation(status='AMBIGUOUS', market=market, basis='EXACT_SOURCE_IDENTIFIER',
                                     confidence='NONE', candidate_instrument_ids=(instrument, UUID(int=9)),
                                     mapping_evidence_ids=('fixture-map',))
    return InstrumentAssociation(status=kind, market=market, basis='NONE', confidence='NONE')


def event(eid, kind='MATCHED', market='EGX', instrument=TARGET, category=None,
          published=AT - timedelta(hours=2), observed=None, revision=0, sha='a'):
    classification = (EventClassification(category=category, basis='SOURCE_LABEL', classifier_version='src-1')
                      if category else EventClassification(category='UNKNOWN', basis='UNCLASSIFIED'))
    src = NewsEventSource(source_id='fixture_news', source_event_id=eid, revision=revision,
                          artifact_sha256=sha * 64, published_at=published,
                          observed_at=observed or published + timedelta(minutes=5),
                          event_time_status='UNKNOWN')
    return NewsEvent(market=market, source=src, association=association(kind, market, instrument),
                     classification=classification)


def project(events, decision_time=AT, coverage=AT - timedelta(hours=1), market='EGX', instrument=TARGET):
    return project_event_context(events, market=market, instrument_id=instrument,
                                 decision_time=decision_time, policy=POLICY, coverage_as_of=coverage)


def test_matched_event_found_with_sorted_categories_and_ids():
    a, b = event('a', category='DIVIDEND'), event('b', category='EARNINGS')
    ctx = project([b, a, event('other', instrument=OTHER, category='LEGAL')])
    assert ctx.status == 'EVENT_FOUND'
    assert ctx.relevant_event_ids == tuple(sorted((a.identity, b.identity)))
    assert ctx.categories == ('DIVIDEND', 'EARNINGS')
    assert ctx == project([a, b])  # unrelated instrument is ignored, order independent
    assert ctx == EventContext.model_validate_json(ctx.model_dump_json())


def test_no_event_found_is_never_proof_of_absence():
    ctx = project([event('other', instrument=OTHER)])
    assert ctx.status == 'NO_EVENT_FOUND' and ctx.relevant_event_ids == ()
    assert ctx.absence_is_proof is False
    assert EventContext.model_json_schema()['properties']['absence_is_proof']['const'] is False


def test_unavailable_without_retained_coverage_even_if_events_supplied():
    ctx = project([event('a', category='DIVIDEND')], coverage=None)
    assert ctx.status == 'UNAVAILABLE' and ctx.coverage == 'NOT_CONFIGURED'
    assert ctx.relevant_event_ids == () and ctx.categories == ()


def test_stale_coverage_dominates_but_keeps_found_events_visible():
    a = event('a', category='DIVIDEND')
    edge = project([a], coverage=AT - timedelta(seconds=POLICY.max_coverage_age_seconds))
    assert edge.status == 'EVENT_FOUND'
    stale = project([a], coverage=AT - timedelta(seconds=POLICY.max_coverage_age_seconds + 1))
    assert stale.status == 'STALE' and stale.relevant_event_ids == (a.identity,)
    assert project([], coverage=AT - timedelta(days=2)).status == 'STALE'


def test_ambiguous_and_unattributed_are_visible_not_guessed():
    amb = event('amb', kind='AMBIGUOUS')
    ctx = project([amb])
    assert ctx.status == 'AMBIGUOUS' and ctx.ambiguous_event_ids == (amb.identity,)
    assert ctx.relevant_event_ids == () and ctx.categories == ()
    assert project([event('amb', kind='AMBIGUOUS', instrument=OTHER)]).status == 'NO_EVENT_FOUND'
    for kind in ('UNRESOLVED', 'IDENTITY_UNAVAILABLE'):
        unknown = project([event('u', kind=kind)])
        assert unknown.status == 'UNKNOWN' and unknown.unattributed_event_count == 1
    both = project([amb, event('m', category='EARNINGS')])
    assert both.status == 'EVENT_FOUND' and both.ambiguous_event_ids == (amb.identity,)


def test_future_events_and_revisions_never_reach_earlier_context():
    late = event('late', category='M&A', published=AT - timedelta(hours=3), observed=AT + timedelta(seconds=1))
    future = event('future', category='LEGAL', published=AT + timedelta(hours=1))
    assert project([late, future]).status == 'NO_EVENT_FOUND'
    later = project([late, future], decision_time=AT + timedelta(hours=2))
    assert later.status == 'EVENT_FOUND' and later.categories == ('LEGAL', 'M&A')
    original = event('a', category='DIVIDEND')
    revised = event('a', category='CAPITAL_RAISE', revision=1, sha='b', observed=AT + timedelta(minutes=1))
    assert project([revised, original]).categories == ('DIVIDEND',)


def test_lookback_window_is_explicit_and_inclusive():
    start = AT - timedelta(seconds=POLICY.lookback_seconds)
    assert project([event('edge', published=start)]).status == 'EVENT_FOUND'
    assert project([event('old', published=start - timedelta(seconds=1))]).status == 'NO_EVENT_FOUND'


def test_fail_closed_inputs():
    with pytest.raises(ValueError):
        project([], decision_time=AT.replace(tzinfo=None))
    with pytest.raises(ValueError):
        project([], coverage=(AT - timedelta(hours=1)).replace(tzinfo=None))
    with pytest.raises(ValueError, match='coverage observed after'):
        project([], coverage=AT + timedelta(seconds=1))
    with pytest.raises(ValueError):
        project([], instrument='TEST')
    with pytest.raises(ValueError):
        project([event('us', market='US')])
    with pytest.raises(ValueError):
        project([], market='ALL')
    with pytest.raises(ValueError):
        project_event_context([], market='EGX', instrument_id=TARGET, decision_time=AT,
                              policy=POLICY.model_dump(), coverage_as_of=AT)
    for bad in (dict(lookback_seconds=0), dict(max_coverage_age_seconds=0), dict(policy_version='')):
        with pytest.raises(ValidationError):
            EventContextPolicy(**(POLICY.model_dump() | bad))


def test_market_separation_us_context():
    us = event('u1', market='US', category='EARNINGS')
    ctx = project([us], market='US')
    assert ctx.market == 'US' and ctx.status == 'EVENT_FOUND'
    assert project([], market='US').status == 'NO_EVENT_FOUND'


def test_offsets_preserved_and_cairo_decision_equivalent():
    a = event('a', category='DIVIDEND')
    cairo = project([a], decision_time=AT.astimezone(CAIRO))
    assert cairo.decision_time.utcoffset() == timedelta(hours=2)
    assert cairo.status == 'EVENT_FOUND' and cairo.relevant_event_ids == (a.identity,)


def test_contract_rejects_inconsistent_or_trading_payloads():
    ctx = project([event('a', category='DIVIDEND')])
    payload = ctx.model_dump(mode='python')
    for update in ({'status': 'NO_EVENT_FOUND'}, {'status': 'STALE'}, {'categories': ()},
                   {'relevant_event_ids': ('x',)}, {'coverage_as_of': AT + timedelta(seconds=1)},
                   {'coverage': 'NOT_CONFIGURED'}, {'live_money': True}, {'trading_effect': 'BUY'},
                   {'execution_allowed': True}, {'absence_is_proof': True}, {'mode': 'LIVE'},
                   {'risk_interpretation': 'LOW'}, {'association_kind': 'OBSERVED'}, {'signal': 'BUY'},
                   {'contract_version': 'news-event-context-v2'}):
        with pytest.raises(ValidationError):
            EventContext.model_validate(payload | update)
    for key, value in (('live_money', True), ('status', 'NO_EVENT_FOUND')):
        with pytest.raises(ValidationError):
            setattr(ctx, key, value)


def test_trade_decision_event_risk_unchanged():
    field = TradeDecision.model_fields['event_risk']
    assert get_args(field.annotation) == ('UNKNOWN',) and field.default == 'UNKNOWN'
