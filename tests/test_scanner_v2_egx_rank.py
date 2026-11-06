"""Artificial EGX-RANK-v1 report fixtures only; no market, edge or execution evidence."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.scanner_v2_egx_rank import (
    CONFIG_ID, MAX_HOLDING_SESSIONS, TRIGGER, EgxRankBinding, bind_egx_rank_report)
from app.strategies.trade_decision import trade_card


UTC = timezone.utc
CLOSE = datetime(2026, 11, 3, 12, 30, tzinfo=UTC)          # 14:30 Africa/Cairo
GENERATED = '2026-11-04T06:39:32.427600+00:00'
NEXT_CLOSE = datetime(2026, 11, 4, 12, 30, tzinfo=UTC)


def plan(reference='10.00', stop='9.40', t1='10.90', t2='11.80'):
    low = str(Decimal(reference) * Decimal('0.995'))
    high = str(Decimal(reference) * Decimal('1.005'))
    return {'entry_zone': [low, high], 'entry_reference': reference, 'stop': stop,
            'target_1': t1, 'target_2': t2, 'risk_per_share': '0.60',
            'risk_reward_t1': '1.5', 'risk_reward_t2': '3',
            'entry_timing': 'NEXT_ELIGIBLE_SESSION_ONLY'}


def record(ticker, classification, **extra):
    base = {'ticker': ticker, 'classification': classification, 'rank_version': 'EGX-RANK-v1',
            'rejection_reasons': [], 'freshness': 'CURRENT', 'last_market_session': '2026-11-03',
            'score': 70.0, 'data_warnings': ['SPLIT_ADJUSTED_SERIES'], 'licensing': 'NO_CONTRACTUAL',
            'artifact_id': f'artifact-{ticker}'}
    if classification in ('STRONG_CANDIDATE', 'CANDIDATE'):
        base.update(plan())
    if classification == 'NO_TRADE':
        base['rejection_reasons'] = ['LOW_SCORE']
    base.update(extra)
    return base


def report(records=None, **extra):
    records = records if records is not None else [
        record('AAAA', 'CANDIDATE'), record('BBBB', 'STRONG_CANDIDATE'),
        record('CCCC', 'WATCHLIST'), record('DDDD', 'NO_TRADE'), record('EEEE', 'CANDIDATE')]
    counts = {}
    for r in records:
        counts[r.get('classification')] = counts.get(r.get('classification'), 0) + 1
    doc = {'schema': 'egx-ranking-report-v1', 'rank_version': 'EGX-RANK-v1',
           'generated_at': GENERATED, 'based_on_session': '2026-11-03',
           'next_expected_session': '2026-11-04', 'next_session_basis': 'EXPECTED: not verified',
           'provider': 'tradingview_tvdatafeed_egx', 'admission': 'ADMITTED',
           'live_money': False, 'counts': counts, 'symbols_ranked': len(records),
           'symbols': records}
    doc.update(extra)
    return json.dumps(doc, sort_keys=True).encode()


def binding(**extra):
    args = dict(session=date(2026, 11, 3), next_session=date(2026, 11, 4), data_as_of=CLOSE,
                fresh_until=NEXT_CLOSE, valid_until=NEXT_CLOSE,
                next_session_open=datetime(2026, 11, 4, 8, tzinfo=UTC),
                instrument_ids=(('AAAA', UUID(int=1)), ('BBBB', UUID(int=2)),
                                ('CCCC', UUID(int=3))),
                calendar_evidence_id='calendar-fixture', identity_evidence_id='identity-fixture')
    args.update(extra)
    return EgxRankBinding(**args)


def bind(artifact=None, **kwargs):
    artifact = artifact if artifact is not None else report()
    return bind_egx_rank_report(artifact, expected_sha256=sha256(artifact).hexdigest(),
                                binding=kwargs.pop('binding', None) or binding(), **kwargs)


def test_binds_only_confirmed_champion_classes_with_exact_published_terms():
    result = bind()
    assert [o['symbol'] for o in result['symbols']] == ['AAAA', 'BBBB', 'CCCC', 'DDDD', 'EEEE']
    assert [o['reason'] for o in result['symbols']] == [
        None, None, 'SETUP_NOT_CONFIRMED', 'NO_TRADE', 'IDENTITY_UNBOUND']
    assert result['candidate_count'] == 2 and result['excluded'] == 3
    assert result['exclusion_counts'] == dict(NO_TRADE=1, SETUP_NOT_CONFIRMED=1,
                                              IDENTITY_UNBOUND=1, TERMS_INVALID=0)
    assert result['authoritative_universe_size'] is None
    assert result['live_money'] is result['execution_allowed'] is False
    assert result['promotion'] == 'NOT_AUTHORIZED' and result['risk_status'] == 'NOT_EVALUATED'
    d = result['decisions'][0]
    assert d.source.market == 'EGX' and d.source.instrument_id == UUID(int=1)
    assert d.candidate.state == 'READY' and d.candidate.strategy_id == 'EGX-RANK-v1'
    assert d.candidate.config_id == CONFIG_ID
    assert d.candidate.entry_reference is None and d.candidate.stop_reference is None
    assert d.terms.entry_trigger == TRIGGER
    assert (d.terms.entry_reference, d.terms.stop, d.terms.target) == (
        Decimal('10.00'), Decimal('9.40'), Decimal('10.90'))
    assert d.terms.max_holding_sessions == MAX_HOLDING_SESSIONS == 11
    assert d.terms.valid_until == NEXT_CLOSE and d.terms.flat_by is None
    evidence = json.loads(d.candidate.evidence_json)
    assert evidence['target_2'] == '11.80'
    assert evidence['report_next_expected_session'] == '2026-11-04'
    assert evidence['data_warnings'] == ['SPLIT_ADJUSTED_SERIES']
    assert d.risk_status == 'NOT_EVALUATED' and d.confidence == 'UNVALIDATED'


def test_deterministic_and_bound_to_artifact_hash():
    first, second = bind(), bind()
    assert [d.identity for d in first['decisions']] == [d.identity for d in second['decisions']]
    artifact = report()
    for d in first['decisions']:
        assert d.source.artifact_sha256 == sha256(artifact).hexdigest()
        assert f'egx-ranking-sha256:{d.source.artifact_sha256}' in d.source.evidence_ids
    with pytest.raises(ValueError, match='hash mismatch'):
        bind_egx_rank_report(artifact, expected_sha256='0' * 64, binding=binding())
    with pytest.raises(ValueError):
        bind_egx_rank_report(bytearray(artifact), expected_sha256=sha256(artifact).hexdigest(),
                             binding=binding())


def test_card_projection_labels_research_only_and_provenance():
    d = bind()['decisions'][0]
    card = trade_card(d, observed_at=datetime.fromisoformat(GENERATED))
    assert card['status'] == 'RESEARCH_CANDIDATE'
    assert card['strategy_inference']['trigger'] == TRIGGER
    assert card['risk_decision']['risk_per_share'] == '0.60'
    assert card['research_evidence']['status'] == 'UNVALIDATED'
    assert card['live_money'] is card['execution_allowed'] is False
    assert trade_card(d, observed_at=NEXT_CLOSE)['invalidation_reason'] == 'PLAN_EXPIRED'


@pytest.mark.parametrize('overrides', [
    dict(stop='10.20'),                      # stop above entry
    dict(target_1='9.90'),                   # target below entry zone
    dict(target_2='10.50'),                  # T2 not above T1
    dict(stop=None), dict(entry_zone=['9.95']), dict(entry_reference=10.0),
    dict(stop='NaN'), dict(target_1='abc'),
])
def test_invalid_terms_are_visibly_excluded_not_repaired(overrides):
    records = [record('AAAA', 'CANDIDATE', **overrides), record('BBBB', 'CANDIDATE')]
    result = bind(report(records), binding=binding(instrument_ids=binding().instrument_ids[:2]))
    assert result['symbols'][0]['reason'] == 'TERMS_INVALID'
    assert [d.source.symbol for d in result['decisions']] == ['BBBB']


@pytest.mark.parametrize('records,extra', [
    (None, dict(rank_version='EGX-RANK-v2')),
    (None, dict(live_money=True)),
    (None, dict(admission='NOT_ADMITTED')),
    (None, dict(based_on_session='2026-11-02')),
    (None, dict(schema='other')),
    (None, dict(counts={'CANDIDATE': 9})),
    (None, dict(symbols_ranked=99)),
    ([record('AAAA', 'CANDIDATE', rejection_reasons=['ILLIQUID'])], {}),
    ([record('AAAA', 'CANDIDATE', freshness='STALE')], {}),
    ([record('AAAA', 'CANDIDATE', last_market_session='2026-11-02')], {}),
    ([record('AAAA', 'CANDIDATE'), record('AAAA', 'WATCHLIST')], {}),
    ([record('AAAA', 'UNKNOWN_CLASS')], {}),
    ([record('AA AA', 'WATCHLIST')], {}),
])
def test_report_drift_fails_closed(records, extra):
    with pytest.raises(ValueError):
        bind(report(records, **extra), binding=binding(instrument_ids=()))


def test_no_lookahead_or_stale_window():
    # Ranking generated before the bound close would use unavailable data.
    early = report(generated_at='2026-11-03T12:00:00+00:00')
    with pytest.raises(ValueError, match='observation window'):
        bind(early)
    late = report(generated_at='2026-11-04T12:30:00+00:00')
    with pytest.raises(ValueError, match='observation window'):
        bind(late)
    naive = report(generated_at='2026-11-04T06:39:32')
    with pytest.raises(ValueError):
        bind(naive)


@pytest.mark.parametrize('extra', [
    dict(data_as_of=datetime(2026, 11, 2, 12, 30, tzinfo=UTC)),       # not the bound session
    dict(valid_until=datetime(2026, 11, 5, 12, 30, tzinfo=UTC)),      # not the next session
    dict(next_session=date(2026, 11, 3)),
    dict(fresh_until=CLOSE),
    dict(instrument_ids=(('AAAA', UUID(int=1)), ('AAAA', UUID(int=2)))),
    dict(calendar_evidence_id=''),
    dict(data_as_of=CLOSE.replace(tzinfo=None)),
])
def test_binding_assertions_fail_closed(extra):
    with pytest.raises(ValidationError):
        binding(**extra)


def test_binding_must_stay_inside_ranking_scope():
    with pytest.raises(ValueError, match='outside ranking scope'):
        bind(binding=binding(instrument_ids=(('ZZZZ', UUID(int=9)),)))


def test_trigger_vocabulary_is_explicit_and_closed():
    from app.strategies.trade_decision import DecisionTerms
    schema = DecisionTerms.model_json_schema()['properties']['entry_trigger']
    assert set(schema['enum']) == {'LONG_ENTRY_ZONE_TOUCH_V1', TRIGGER}


def test_rules_are_pinned_and_drift_requires_versioning(monkeypatch):
    from app.scanner_v2_egx_rank import RULES
    assert CONFIG_ID == 'ca917d171004210a6b313dbc1bf8f70cda059c36e2c285e1bbeb0d2cb806fceb'
    assert sha256(json.dumps(RULES, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == CONFIG_ID
    monkeypatch.setitem(RULES, 'entry_band', '0.01')
    with pytest.raises(ValueError, match='rules changed'):
        bind()


@pytest.mark.parametrize('component', ['Candidate', 'DecisionSource', 'TradeDecision'])
def test_non_term_validation_failures_propagate(monkeypatch, component):
    import app.scanner_v2_egx_rank as adapter
    def fail(**kwargs):
        raise ValueError('non-term failure')
    monkeypatch.setattr(adapter, component, fail)
    with pytest.raises(ValueError, match='non-term failure'):
        bind()


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_non_finite_json_is_report_failure_even_without_identity(value):
    artifact = report().replace(b'70.0', value.encode())
    with pytest.raises(ValueError):
        bind(artifact, binding=binding(instrument_ids=()))


def test_duplicate_json_keys_fail_closed():
    artifact = report().replace(b'"live_money": false', b'"live_money": true, "live_money": false')
    with pytest.raises(ValueError, match='duplicate JSON key'):
        bind(artifact)


@pytest.mark.parametrize('generated', ['2026-11-04T08:00:00+00:00', '2026-11-04T09:00:00+00:00'])
def test_generation_must_precede_admitted_open(generated):
    with pytest.raises(ValueError, match='entry window'):
        bind(report(generated_at=generated), binding=binding(instrument_ids=()))


@pytest.mark.parametrize('opened', [CLOSE, NEXT_CLOSE, datetime(2026, 11, 3, 15, tzinfo=UTC),
                                  datetime(2026, 11, 4, 8)])
def test_next_session_open_binding_is_aware_ordered_and_same_date(opened):
    with pytest.raises(ValueError):
        binding(next_session_open=opened)


@pytest.mark.parametrize('overrides', [dict(stop='9.95'), dict(target_1='10.05'),
                                     dict(entry_zone=['10.00', '10.00'])])
def test_zone_boundary_geometry_is_excluded(overrides):
    result = bind(report([record('AAAA', 'CANDIDATE', **overrides)]),
                  binding=binding(instrument_ids=binding().instrument_ids[:1]))
    assert result['exclusion_counts']['TERMS_INVALID'] == 1
    assert result['candidate_count'] == 0


def test_both_classes_preserve_daily_and_card_provenance():
    for decision, ticker in zip(bind()['decisions'], ('AAAA', 'BBBB')):
        assert f'daily-artifact:artifact-{ticker}' in decision.source.evidence_ids
        card = trade_card(decision, observed_at=datetime.fromisoformat(GENERATED))
        assert card['observed_fact']['provenance']['evidence_ids'] == list(decision.source.evidence_ids)
        assert card['risk_decision']['event_risk'] == 'UNKNOWN'
    assert json.loads(bind()['decisions'][1].candidate.evidence_json)['classification'] == 'STRONG_CANDIDATE'
    records = [record('AAAA', 'CANDIDATE', artifact_id=None)]
    decision = bind(report(records), binding=binding(instrument_ids=binding().instrument_ids[:1]))['decisions'][0]
    assert not any(e.startswith('daily-artifact:') for e in decision.source.evidence_ids)
    assert len(decision.source.evidence_ids) == 3  # ranking hash + caller calendar/identity


@pytest.mark.parametrize('scale,expected', [('1', 1), ('0.001', 0)])
def test_real_classifier_quantized_plans(scale, expected):
    from datetime import timedelta
    from app.strategies.egx_ranking import Bar, classify
    factor = Decimal(scale)
    bars = []
    for i in range(80):
        close = (Decimal('10') + Decimal('0.2') * i) * factor
        bars.append(Bar((date(2026, 11, 3) - timedelta(days=79-i)).isoformat(),
                        close - Decimal('0.02') * factor, close + Decimal('0.1') * factor,
                        close - Decimal('0.1') * factor, close,
                        Decimal(3_000_000 if i == 79 else 1_000_000) / factor))
    r = classify(ticker='AAAA', company=None, isin=None, source='tradingview_tvdatafeed_egx',
                 licensing='FIXTURE', admitted=True, freshness='CURRENT',
                 session='2026-11-03', bars=bars)
    assert r['classification'] == 'STRONG_CANDIDATE'
    result = bind(report([r]), binding=binding(instrument_ids=binding().instrument_ids[:1]))
    assert result['candidate_count'] == expected
    if expected:
        d = result['decisions'][0]
        assert str(d.terms.target) == r['target_1']
        assert str(d.terms.entry_reference) == r['entry_reference']
    else:
        assert result['exclusion_counts']['TERMS_INVALID'] == 1


def test_observation_window_with_no_bound_candidates_still_rejects():
    for generated in ('2026-11-03T12:00:00+00:00', '2026-11-04T12:30:00+00:00'):
        with pytest.raises(ValueError, match='observation window'):
            bind(report(generated_at=generated), binding=binding(instrument_ids=()))
