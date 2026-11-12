from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.decision_observation import CONTRACT_VERSION, canonical_bytes, decision_observation
from app.news_event import EventClassification, InstrumentAssociation, NewsEvent, NewsEventSource
from app.news_event_card import card_event_context
from app.news_event_context import EventContextPolicy, project_event_context
from app.paper.shadow_candidate_admission import strategy_candidate_sha256
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision, trade_card


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
CAIRO = timezone(timedelta(hours=2))
TARGET, OTHER = UUID(int=1), UUID(int=2)
POLICY = EventContextPolicy(policy_version='fixture-ctx-1', lookback_seconds=3 * 86400,
                            max_coverage_age_seconds=6 * 3600)


def decision(market='EGX', instrument=TARGET, strategy_version='1', flat_by=None,
             fresh=timedelta(hours=1)):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version=strategy_version,
                          config_id='fixture', symbol='TEST', decision_time=AT,
                          data_cutoff=AT - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64, evidence_ids=('fixture-only',),
                            data_as_of=candidate.data_cutoff, available_at=AT,
                            fresh_until=AT + fresh, completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=Decimal('10'),
                          entry_high=Decimal('11'), entry_reference=Decimal('10.5'),
                          stop=Decimal('9'), target=Decimal('13'),
                          valid_until=AT + timedelta(days=1), max_holding_sessions=2,
                          flat_by=flat_by)
    return TradeDecision(candidate=candidate, source=source, terms=terms)


def event(eid, market='EGX', published=AT - timedelta(hours=2), observed=None):
    assoc = InstrumentAssociation(status='MATCHED', market=market, basis='EXACT_SOURCE_IDENTIFIER',
                                  confidence='EXACT', symbol='TEST', instrument_id=TARGET,
                                  mapping_evidence_ids=('fixture-map',))
    src = NewsEventSource(source_id='fixture_news', source_event_id=eid, revision=0,
                          artifact_sha256='a' * 64, published_at=published,
                          observed_at=observed or published + timedelta(minutes=5),
                          event_time_status='UNKNOWN')
    return NewsEvent(market=market, source=src, association=assoc,
                     classification=EventClassification(category='DIVIDEND', basis='SOURCE_LABEL',
                                                        classifier_version='src-1'))


def context(events, decision_time=AT, market='EGX', instrument=TARGET):
    return project_event_context(events, market=market, instrument_id=instrument,
                                 decision_time=decision_time, policy=POLICY,
                                 coverage_as_of=AT - timedelta(hours=1))


def test_record_retains_decision_terms_provenance_and_no_outcome():
    d = decision()
    rec = decision_observation(d, None, recorded_at=AT + timedelta(minutes=5))
    assert rec['contract_version'] == CONTRACT_VERSION == 'decision-observation-v1'
    assert rec['decision_id'] == d.identity
    assert (rec['market'], rec['symbol'], rec['instrument_id']) == ('EGX', 'TEST', str(TARGET))
    assert (rec['strategy_id'], rec['strategy_version'], rec['config_id']) == \
        ('fixture-challenger', '1', 'fixture')
    assert rec['candidate_sha256'] == strategy_candidate_sha256(d.candidate)
    assert rec['detected_at'] == '2026-11-05T12:00:00Z' and rec['data_as_of'] == '2026-11-05T11:59:00Z'
    assert rec['fresh_until'] == '2026-11-05T13:00:00Z'
    assert rec['terms'] == d.terms.model_dump(mode='json')
    assert rec['terms']['entry_reference'] == '10.5' and rec['terms']['stop'] == '9'
    assert rec['terms']['target'] == '13' and rec['terms']['valid_until'] == '2026-11-06T12:00:00Z'
    assert rec['risk_per_share'] == '1.5'
    assert rec['provenance'] == {'source_id': 'fixture', 'artifact_sha256': 'a' * 64,
                                 'evidence_ids': ['fixture-only']}
    assert rec['recorded_at'] == '2026-11-05T12:05:00Z'
    assert rec['recorded_at_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert rec['outcome_status'] == 'NOT_OBSERVED' and rec['outcome'] is None
    assert rec['evaluation_status'] == 'PENDING_OUTCOME' and rec['performance_claim'] == 'NONE'
    assert rec['event_context'] == {'status': 'NOT_ATTACHED', 'section': None, 'section_sha256': None}


def test_record_is_deterministic_canonical_json():
    d, ctx = decision(), context([event('a')])
    rec = decision_observation(d, ctx, recorded_at=AT)
    assert json.loads(canonical_bytes(rec)) == rec
    assert canonical_bytes(rec) == canonical_bytes(decision_observation(decision(), context([event('a')]),
                                                                        recorded_at=AT))
    # The same instant in another offset gives identical bytes.
    assert canonical_bytes(rec) == canonical_bytes(decision_observation(d, ctx,
                                                                        recorded_at=AT.astimezone(CAIRO)))


def test_attached_event_context_is_the_exact_card_section_at_detection():
    d, ctx = decision(), context([event('a')])
    rec = decision_observation(d, ctx, recorded_at=AT + timedelta(minutes=1))
    section = card_event_context(d, ctx)
    assert rec['event_context']['status'] == 'ATTACHED'
    assert rec['event_context']['section'] == section
    assert rec['event_context']['section_sha256'] == sha256(
        json.dumps(section, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert section['event_context_status'] == 'EVENT_FOUND'


def test_future_news_and_later_context_cannot_be_recorded():
    d = decision()
    late = event('late', published=AT - timedelta(hours=3), observed=AT + timedelta(seconds=1))
    rec = decision_observation(d, context([late]), recorded_at=AT + timedelta(minutes=5))
    assert rec['event_context']['section']['event_context_status'] == 'NO_EVENT_FOUND'
    assert rec['event_context']['section']['relevant_event_ids'] == []
    with pytest.raises(ValueError, match='detection time'):
        decision_observation(d, context([late], decision_time=AT + timedelta(minutes=5)),
                             recorded_at=AT + timedelta(minutes=5))


def test_recording_window_fails_closed():
    d = decision()
    with pytest.raises(ValueError, match='before detection'):
        decision_observation(d, None, recorded_at=AT - timedelta(seconds=1))
    with pytest.raises(ValueError, match='freshness'):
        decision_observation(d, None, recorded_at=AT + timedelta(hours=1))
    with pytest.raises(ValueError, match='timezone-aware'):
        decision_observation(d, None, recorded_at=AT.replace(tzinfo=None))
    flat = decision(flat_by=AT + timedelta(minutes=30), fresh=timedelta(hours=2))
    decision_observation(flat, None, recorded_at=AT + timedelta(minutes=29))
    with pytest.raises(ValueError, match='plan expiry'):
        decision_observation(flat, None, recorded_at=AT + timedelta(minutes=30))


def test_market_instrument_and_version_separation():
    with pytest.raises(ValueError, match='market'):
        decision_observation(decision('US'), context([]), recorded_at=AT)
    with pytest.raises(ValueError, match='instrument'):
        decision_observation(decision(), context([], instrument=OTHER), recorded_at=AT)
    us = decision_observation(decision('US'), context([event('u', market='US')], market='US'),
                              recorded_at=AT)
    assert us['market'] == 'US'
    v1 = decision_observation(decision(), None, recorded_at=AT)
    v2 = decision_observation(decision(strategy_version='2'), None, recorded_at=AT)
    assert v1['strategy_version'] == '1' and v2['strategy_version'] == '2'
    assert v1['decision_id'] != v2['decision_id']


def test_no_trading_effect_and_decision_and_card_unchanged():
    d = decision()
    before, card = d.model_dump_json(), trade_card(d, observed_at=AT)
    rec = decision_observation(d, context([event('a')]), recorded_at=AT)
    assert rec['mode'] == 'SHADOW' and rec['live_money'] is False and rec['execution_allowed'] is False
    assert rec['promotion'] == 'NOT_AUTHORIZED' and rec['risk_status'] == 'NOT_EVALUATED'
    assert rec['confidence'] == 'UNVALIDATED' and rec['event_risk'] == 'UNKNOWN'
    assert d.model_dump_json() == before and trade_card(d, observed_at=AT) == card


def test_exact_types_required_and_tampered_inputs_rejected():
    d, ctx = decision(), context([event('a')])
    with pytest.raises(ValueError):
        decision_observation(d.model_dump(), None, recorded_at=AT)
    with pytest.raises(ValueError):
        decision_observation(d, ctx.model_dump(), recorded_at=AT)
    with pytest.raises(ValueError):
        decision_observation(d.model_copy(update={'live_money': True}), None, recorded_at=AT)
    with pytest.raises(ValueError):
        decision_observation(d, ctx.model_copy(update={'status': 'NO_EVENT_FOUND'}), recorded_at=AT)
