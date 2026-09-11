"""Synthetic engineering acceptance only; network blocked by conftest."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.data.intraday import IntradayBar, available_bars, cumulative_vwap
from app.strategies.contracts import Candidate, candidate
from app.strategies.eod import PreSurgeConfig, PreSurgeV7Engine, ScorerRow, SwingConfig, SwingEngine
from app.strategies.intraday import (
    PriorBias, First15Config, First15Engine, ORBConfig, OpeningRangeBreakoutEngine,
    VWAPConfig, VWAPPullbackEngine, MomentumConfig, MomentumContinuationEngine,
)
from test_m3_point_in_time import repo, ready, cutoff, DAY, bars as daily_bars
from app.data.point_in_time import PointInTimeDailyRepository

AT = datetime(2020, 1, 2, 9, tzinfo=timezone.utc)


def bar(i, close=10., volume=100., minutes=5, **changes):
    data = dict(symbol='SYNTH', interval_start=AT + timedelta(minutes=(i-1)*minutes),
                interval_end=AT + timedelta(minutes=i*minutes),
                available_at=AT + timedelta(minutes=i*minutes), open=10.,
                high=max(10., close)+1, low=min(10., close)-1, close=close, volume=volume,
                traded_value=volume*10, session_id='synthetic-session', market_date=DAY,
                sequence=i, session_phase='CONTINUOUS', is_final=True,
                source_id='synthetic-v1', provenance_id='fixture-snapshot')
    return IntradayBar(**(data | changes))


def orb(**changes):
    return OpeningRangeBreakoutEngine(ORBConfig(**(dict(config_version='test', opening_bars=2,
        breakout_buffer_bps=0., minimum_relative_volume=None, stop_at_range_low=True) | changes)))


def bias_context(direction):
    return PriorBias(direction=direction, available_at=AT, provenance_id='fixture-context')


def first(**changes):
    return First15Engine(First15Config(**(dict(config_version='test', opening_window_minutes=15,
        bullish_close_location=.7, minimum_return=None, invalidate_below_open=True,
        invalidation_return_floor=None) | changes)))


def vwap(**changes):
    return VWAPPullbackEngine(VWAPConfig(**(dict(config_version='test', minimum_extension_bps=100.,
        pullback_tolerance_bps=10., reclaim_buffer_bps=10., pattern_lookback_bars=3,
        stop_lookback_bars=None) | changes)))


def momentum(**changes):
    return MomentumContinuationEngine(MomentumConfig(**(dict(config_version='test', momentum_lookback_bars=2,
        minimum_return=.05, minimum_relative_volume=None, maximum_vwap_extension_bps=None,
        stop_lookback_bars=None) | changes)))


def score(symbol='A', **changes):
    return ScorerRow(**(dict(symbol=symbol, signal_date=DAY, available_at=AT,
        source_date=DAY, source_id='seed', provenance_id='fixture',
        scorer_contract='LEGACY_V5_PARITY_SEED', model_version='V5-fixture',
        trained_through=date(2020, 1, 1), model_p_top10=.5, model_p_close8=.4,
        model_p_stop5=.3, model_expected_close=.1, avg_turnover=100., today_return=.01) | changes))


def presurge():
    return PreSurgeV7Engine(PreSurgeConfig(config_version='test',
        scorer_contract='LEGACY_V5_PARITY_SEED', risk_penalty=.2))


def evidence(result):
    return json.loads(result.evidence_json)


@pytest.mark.parametrize('cls', [First15Config, ORBConfig, VWAPConfig, MomentumConfig, SwingConfig, PreSurgeConfig])
def test_missing_config_fails_closed(cls):
    with pytest.raises(ValueError):
        cls(config_version='test')


@pytest.mark.parametrize('change', [dict(interval_start=AT.replace(tzinfo=None)),
    dict(interval_end=AT), dict(available_at=AT), dict(open=0.), dict(high=9.),
    dict(low=11.), dict(close=float('nan')), dict(volume=-1.), dict(traded_value=-1.),
    dict(source_id=''), dict(sequence=0), dict(is_final=1)])
def test_intraday_bad_bar(change):
    with pytest.raises(ValueError):
        bar(1, **change)


@pytest.mark.parametrize('change', [dict(sequence=1), dict(symbol='OTHER'), dict(session_id='OTHER'),
    dict(market_date=date(2020, 1, 3)), dict(source_id='OTHER'), dict(provenance_id='OTHER'),
    dict(interval_start=AT+timedelta(minutes=4)), dict(interval_start=AT+timedelta(minutes=6)),
    dict(session_phase='AUCTION'), dict(session_phase='PRE_OPEN'), dict(session_phase='CLOSING_AUCTION'),
    dict(session_phase='UNKNOWN')])
def test_intraday_invalid_series(change):
    with pytest.raises(ValueError):
        available_bars([bar(1), bar(2, **change)], AT+timedelta(hours=1))


def test_intraday_cutoff_finality_and_order():
    rows = [bar(1), bar(2), bar(3, is_final=False)]
    assert available_bars(rows, rows[0].available_at) == (rows[0],)
    assert available_bars(rows, AT+timedelta(hours=1)) == tuple(rows[:2])
    with pytest.raises(ValueError):
        available_bars(rows[::-1], AT+timedelta(hours=1))
    with pytest.raises(ValueError):
        available_bars([bar(1, is_final=False), bar(2)], AT+timedelta(hours=1))
    with pytest.raises(ValueError):
        available_bars(rows, AT-timedelta(seconds=1))
    with pytest.raises(ValueError):
        available_bars(rows, AT.replace(tzinfo=None))


@pytest.mark.parametrize('bias,state', [('UP','CONTINUATION_CONFIRMED'), ('DOWN','REVERSAL_CONFIRMED'),
                                       ('NONE','NO_CONFIRMATION')])
def test_first15_confirmation(bias, state):
    rows = [bar(1), bar(2), bar(3, close=13.)]
    assert first().evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context(bias)).state == state


def test_first15_coverage_and_invalidation():
    rows = [bar(1, minutes=7), bar(2, minutes=7), bar(3, minutes=7, close=13.)]
    assert first().evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context('UP')).state == 'NO_CONFIRMATION'
    rows = [bar(1, minutes=15, close=13.)]
    assert first().evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context('UP')).state == 'CONTINUATION_CONFIRMED'
    rows = [bar(1), bar(2), bar(3, close=9.)]
    assert first().evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context('UP')).state == 'INVALIDATED'
    with pytest.raises(ValueError):
        first().evaluate([bar(1), bar(3)], decision_time=rows[-1].available_at, prior_bias=bias_context('UP'))
    assert first(minimum_return=.5).evaluate([bar(1, minutes=15, close=13.)],
        decision_time=rows[-1].available_at, prior_bias=bias_context('UP')).state == 'NO_CONFIRMATION'


def test_orb_opening_only_buffer_and_volume():
    rows = [bar(1), bar(2), bar(3, close=12., high=20.)]
    at = rows[-1].available_at
    result = orb().evaluate(rows, decision_time=at)
    assert result.state == 'READY' and result.entry_reference == 11. and result.stop_reference == 9.
    assert evidence(result)['range_high'] == 11.
    assert orb(breakout_buffer_bps=1000.).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert orb(minimum_relative_volume=2.).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert orb().evaluate(rows[:2], decision_time=at).state == 'NO_CONFIRMATION'


def test_vwap_exact_and_ordering():
    rows = [bar(1, close=12., open=12., low=11.), bar(2), bar(3, close=11.)]
    at = rows[-1].available_at
    result = vwap().evaluate(rows, decision_time=at)
    assert result.state == 'READY'
    assert evidence(result)['vwap'] == 10.
    assert evidence(result)['extension_sequence'] == 1
    assert evidence(result)['pullback_sequence'] == 2
    assert cumulative_vwap([bar(1, traded_value=800.), bar(2, volume=200., traded_value=2200.)]) == (8.,10.)
    assert vwap().evaluate([bar(1), bar(2, close=12.), bar(3, close=12.)], decision_time=at).state == 'NO_CONFIRMATION'
    assert vwap().evaluate([bar(i, volume=0.) for i in range(1,4)], decision_time=at).state == 'NO_CONFIRMATION'
    with pytest.raises(ValueError):
        vwap().evaluate([bar(1, traded_value=None)], decision_time=at)


def test_momentum_gates_and_lookback():
    rows = [bar(1, close=20.), bar(2), bar(3), bar(4, close=11., volume=200.)]
    at = rows[-1].available_at
    assert momentum(minimum_relative_volume=2.).evaluate(rows, decision_time=at).state == 'READY'
    assert momentum(minimum_relative_volume=2.1).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert momentum(maximum_vwap_extension_bps=500.).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert momentum(maximum_vwap_extension_bps=2000.).evaluate(rows, decision_time=at).state == 'READY'
    with pytest.raises(ValueError):
        momentum(maximum_vwap_extension_bps=100.).evaluate([bar(1, traded_value=None)], decision_time=at)


@pytest.mark.parametrize('engine,kwargs', [(orb(),{}), (vwap(),{}), (momentum(),{}), (first(),{'prior_bias':bias_context('UP')})])
def test_intraday_replay_future_independence_and_safety(engine, kwargs):
    rows = [bar(1), bar(2), bar(3, close=13.)]
    at = rows[1].available_at
    expected = engine.evaluate(rows[:2], decision_time=at, **kwargs)
    assert engine.evaluate(rows, decision_time=at, **kwargs) == expected
    assert engine.evaluate(rows, decision_time=at, **kwargs) == expected
    assert expected.execution_allowed is False and expected.validation_status == 'UNVALIDATED'
    with pytest.raises(ValueError):
        Candidate(**(expected.model_dump() | {'execution_allowed': True}))


def test_presurge_exact_formula_and_ties():
    rows = [score('B'), score('A'), score('C', model_p_top10=.9, model_p_stop5=.9,
                                             model_p_close8=.8, avg_turnover=200.)]
    result = presurge().evaluate(rows, decision_time=AT)
    assert [r.symbol for r in result] == ['C','A','B']
    # C ranks: 1, 1, 2/3, 1, 1. A/B: .5, .5, 2/3, .5, .5.
    assert evidence(result[0])['score'] == pytest.approx(1+.25+.15*(2/3)+.05-.2)
    assert evidence(result[1])['score'] == pytest.approx(.5+.25*.5+.15*(2/3)+.05*.5-.2*.5)
    assert result == presurge().evaluate(rows[::-1], decision_time=AT)
    assert all(r.execution_allowed is False and r.validation_status == 'UNVALIDATED' for r in result)


@pytest.mark.parametrize('change', [dict(model_p_top10=-.1), dict(model_p_close8=1.1),
    dict(model_p_stop5=float('nan')), dict(model_expected_close=float('inf')),
    dict(trained_through=DAY), dict(trained_through=date(2021,1,1)),
    dict(scorer_contract='UNKNOWN'), dict(source_date=date(2020,1,1)), dict(model_version='')])
def test_bad_scorer_rejected(change):
    with pytest.raises(ValueError):
        score(**change)


def test_presurge_eligibility_missing_fields_identity_future():
    engine = presurge()
    rows = [score('A', today_return=.05), score('B', today_return=.049999), score('C', today_return=.06)]
    assert [r.symbol for r in engine.evaluate(rows, decision_time=AT)] == ['B']
    assert engine.evaluate([score('A', available_at=AT+timedelta(seconds=1))], decision_time=AT) == ()
    for field in ('model_p_top10','model_p_close8','model_p_stop5','model_expected_close','avg_turnover','today_return'):
        data = score().model_dump()
        del data[field]
        with pytest.raises(ValueError):
            ScorerRow(**data)
    for rows in ([score(),score()], [score(),score('B', source_id='other')]):
        with pytest.raises(ValueError):
            engine.evaluate(rows, decision_time=AT)


def swing_config(**changes):
    return SwingConfig(**(dict(config_version='test', minimum_history=2,
        fast_ema_window=1, slow_ema_window=2, breakout_lookback=None) | changes))


def test_swing_m3_repository_only_and_history(repo):
    with pytest.raises(ValueError):
        SwingEngine(object(), swing_config())
    with pytest.raises(ValueError):
        SwingEngine(PointInTimeDailyRepository(repo), None)
    m = ready(repo)
    engine = SwingEngine(PointInTimeDailyRepository(repo), swing_config(minimum_history=3))
    with pytest.raises(ValueError, match='history'):
        engine.evaluate(raw_path=m.raw_path, signal_date=DAY, decision_time=cutoff())


def test_swing_pit_timing_replay_and_future_receipt(repo, monkeypatch):
    records = daily_bars()
    records[-1] |= dict(close=12., high=13.)
    m = ready(repo, records=records)
    engine = SwingEngine(PointInTimeDailyRepository(repo), swing_config(fast_ema_window=1))
    at = cutoff()
    calls = []
    original_load = engine.repository.load

    def observed_load(**kwargs):
        calls.append(kwargs['as_of'])
        return original_load(**kwargs)

    monkeypatch.setattr(engine.repository, 'load', observed_load)
    # A one-period EMA equals close, so the strict trend gate correctly does not qualify.
    result = engine.evaluate(raw_path=m.raw_path, signal_date=DAY, decision_time=at)
    assert result == engine.evaluate(raw_path=m.raw_path, signal_date=DAY, decision_time=at)
    assert result.execution_allowed is False
    assert result.data_cutoff == result.decision_time == at == calls[-1]
    assert result.strategy_version == '1'
    assert evidence(result)['timing'] == 'NEXT_ELIGIBLE_SESSION'
    assert 'fill' not in Candidate.model_fields
    with pytest.raises(ValueError, match='not known'):
        engine.evaluate(raw_path=m.raw_path, signal_date=DAY, decision_time=AT)


def test_swing_positive_next_session_candidate_with_real_m3_validation(repo):
    import hashlib
    from app.data.models import DataAssetType, BarGranularity
    from test_m3_point_in_time import ingest, review, universe, actions
    start = date(2019, 12, 31)
    days = [start, date(2020, 1, 1), DAY]
    for doc in [*(universe(day=d) for d in days), actions() | {'coverage_start': str(start)}]:
        manifest = ingest(repo, doc)
        review(repo, manifest, doc['contract'])
    records = [dict(date=str(d), open=p, high=p+1, low=p-1, close=p,
                    volume=100, adjusted_close=p) for d,p in zip(days, [10,12,15])]
    payload = json.dumps(records).encode()
    manifest = repo.raw_store.store_bytes(provider='synthetic', asset_type=DataAssetType.DAILY_BARS,
        payload=payload, filename=hashlib.sha256(payload).hexdigest()+'.json', market_date=DAY,
        symbol='SYNTH', granularity=BarGranularity.D1, record_count=3)
    from test_m3_point_in_time import ID
    manifest = repo.ingestions.save_manifest(manifest, source_uri='fixture://daily', metadata=dict(
        canonical_symbol='SYNTH', provider_symbol='SYNTH.TEST', instrument_id=str(ID),
        snapshot_date=str(DAY), requested_start_date=str(start), requested_end_date=str(DAY), response={}))
    review(repo, manifest, 'egx-daily-semantic-v1')
    at = cutoff()
    engine = SwingEngine(PointInTimeDailyRepository(repo), swing_config(
        minimum_history=3, fast_ema_window=2, slow_ema_window=3, breakout_lookback=2))
    result = engine.evaluate(raw_path=manifest.raw_path, signal_date=DAY, decision_time=at)
    assert result.state == 'WATCH' and result.entry_reference == 15.
    assert result.validation_status == 'UNVALIDATED' and result.execution_allowed is False
    assert result.stop_reference is None and result.target_reference is None
    assert evidence(result)['after_market_date'] == str(DAY)
    assert evidence(result)['reference_kind'] == 'D_CLOSE_PLANNING_REFERENCE_ONLY'
    assert result == engine.evaluate(raw_path=manifest.raw_path, signal_date=DAY, decision_time=at)
    # Removing review authority blocks the engine, not just direct M3 consumers.
    with repo.database.connect() as con:
        con.execute("UPDATE data_ingestions SET status='REJECTED' WHERE ingestion_id=?", (str(manifest.ingestion_id),))
    with pytest.raises(ValueError, match='source rejected'):
        engine.evaluate(raw_path=manifest.raw_path, signal_date=DAY, decision_time=at)


@pytest.mark.parametrize('engine', [orb(minimum_relative_volume=1.), momentum(minimum_relative_volume=1.)])
def test_zero_relative_volume_fails_gate(engine):
    rows = [bar(1, volume=0.), bar(2, volume=0.), bar(3, close=13.)]
    assert engine.evaluate(rows, decision_time=rows[-1].available_at).state == 'NO_CONFIRMATION'


def test_first15_future_and_later_bars_cannot_rewrite_window():
    rows = [bar(1), bar(2), bar(3, close=13.), bar(4, close=8.)]
    engine = first()
    at = rows[2].available_at
    assert engine.evaluate(rows, decision_time=at, prior_bias=bias_context('UP')).state == 'CONTINUATION_CONFIRMED'
    assert engine.evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context('UP')).state == 'CONTINUATION_CONFIRMED'


def test_vwap_reclaim_must_be_strictly_after_pullback_and_config_gates():
    rows = [bar(1, close=12.), bar(2, close=11.)]
    assert vwap().evaluate(rows, decision_time=rows[-1].available_at).state == 'NO_CONFIRMATION'
    rows.append(bar(3, close=11.))
    at = rows[-1].available_at
    assert vwap(reclaim_buffer_bps=2000.).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert vwap(minimum_extension_bps=3000.).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert vwap(stop_lookback_bars=4).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    assert vwap(stop_lookback_bars=2).evaluate(rows, decision_time=at).stop_reference == 9.


def test_momentum_stop_and_vwap_zero_volume():
    rows = [bar(1), bar(2), bar(3, close=12.)]
    at = rows[-1].available_at
    assert momentum(stop_lookback_bars=2).evaluate(rows, decision_time=at).stop_reference == 9.
    assert momentum(stop_lookback_bars=4).evaluate(rows, decision_time=at).state == 'NO_CONFIRMATION'
    zero = [bar(i, close=10.+i, volume=0.) for i in range(1,4)]
    assert momentum(maximum_vwap_extension_bps=100.).evaluate(zero, decision_time=at).state == 'NO_CONFIRMATION'


@pytest.mark.parametrize('factory,change', [(orb, {'breakout_buffer_bps': -1.}),
    (first, {'bullish_close_location': 1.1}), (vwap, {'pattern_lookback_bars': 2}),
    (momentum, {'momentum_lookback_bars': 0}), (momentum, {'minimum_return': float('nan')}),
    (swing_config, {'fast_ema_window': 3})])
def test_invalid_config_rejected(factory, change):
    with pytest.raises(ValueError):
        factory(**change)


def test_presurge_explicit_risk_unknown_contract_and_future_rank_independence():
    for data in [dict(config_version='test', scorer_contract='UNKNOWN', risk_penalty=.2),
                 dict(config_version='test', scorer_contract='LEGACY_V5_PARITY_SEED'),
                 dict(config_version='test', scorer_contract='LEGACY_V5_PARITY_SEED', risk_penalty=float('inf'))]:
        with pytest.raises(ValueError):
            PreSurgeConfig(**data)
    engine = presurge()
    rows = [score('A'), score('B')]
    expected = engine.evaluate(rows, decision_time=AT)
    assert engine.evaluate(rows+[score('C', available_at=AT+timedelta(seconds=1), model_p_top10=1.)],
                           decision_time=AT) == expected
    assert evidence(expected[0])['ranks']['model_p_top10'] == .75


def test_first15_nonuniform_exact_intervals():
    rows = [bar(1, minutes=4), bar(2, interval_start=AT+timedelta(minutes=4),
            interval_end=AT+timedelta(minutes=9), available_at=AT+timedelta(minutes=9)),
            bar(3, close=13., interval_start=AT+timedelta(minutes=9))]
    assert first().evaluate(rows, decision_time=rows[-1].available_at, prior_bias=bias_context('DOWN')).state == 'REVERSAL_CONFIRMED'


def test_first15_context_is_point_in_time_evidence():
    rows = [bar(1, minutes=15, close=13.)]
    at = rows[-1].available_at
    with pytest.raises(ValueError, match='future prior bias'):
        first().evaluate(rows, decision_time=at, prior_bias=PriorBias(
            direction='UP', available_at=at+timedelta(seconds=1), provenance_id='fixture'))
    result = first().evaluate(rows, decision_time=at+timedelta(seconds=2), prior_bias=PriorBias(
        direction='UP', available_at=at+timedelta(seconds=1), provenance_id='fixture'))
    assert result.data_cutoff == at+timedelta(seconds=1)


@pytest.mark.parametrize('field', ['strategy_id', 'strategy_version', 'config_id', 'symbol'])
def test_candidate_empty_identifiers_reject(field):
    data = presurge().evaluate([score()], decision_time=AT)[0].model_dump()
    with pytest.raises(ValueError):
        Candidate(**(data | {field: ''}))


@pytest.mark.parametrize('field', ['entry_reference', 'stop_reference', 'target_reference'])
@pytest.mark.parametrize('value', [0., -1., float('nan'), float('inf')])
def test_candidate_references_positive_when_present(field, value):
    data = presurge().evaluate([score()], decision_time=AT)[0].model_dump()
    with pytest.raises(ValueError):
        Candidate(**(data | {field: value}))
    assert getattr(Candidate(**(data | {field: 1.})), field) == 1.
    assert getattr(Candidate(**(data | {field: None})), field) is None


def test_candidate_explicit_version_and_deterministic_evidence():
    args = ('PRE_SURGE', presurge().config, 'A', AT, AT, 'WATCH')
    with pytest.raises(TypeError):
        candidate(*args)
    a = candidate(*args, strategy_version='custom', details={'z': AT, 'a': DAY})
    b = candidate(*args, strategy_version='custom', details={'a': DAY, 'z': AT})
    assert a == b
    assert a.strategy_version == 'custom'
    assert evidence(a)['details'] == {'a': DAY.isoformat(), 'z': AT.isoformat()}
    with pytest.raises(TypeError, match='unsupported evidence type'):
        candidate(*args, strategy_version='7', details={'nested': [object()]})
    with pytest.raises(ValueError):
        Candidate(**(a.model_dump() | {'execution_allowed': True}))
    with pytest.raises(ValueError, match='future evidence'):
        candidate(*args[:4], AT + timedelta(seconds=1), 'WATCH', strategy_version='7')


@pytest.mark.parametrize('engine,kwargs,strategy_id', [
    (first(), {'prior_bias': bias_context('UP')}, 'FIRST15'),
    (orb(), {}, 'ORB'), (vwap(), {}, 'VWAP_PULLBACK'),
    (momentum(), {}, 'MOMENTUM_CONTINUATION')])
def test_explicit_intraday_versions(engine, kwargs, strategy_id):
    rows = [bar(1), bar(2), bar(3, close=13.)]
    result = engine.evaluate(rows, decision_time=rows[-1].available_at, **kwargs)
    assert (result.strategy_id, result.strategy_version) == (strategy_id, '1')


def test_explicit_presurge_version():
    assert presurge().evaluate([score()], decision_time=AT)[0].strategy_version == '7'


@pytest.mark.parametrize('close,minimum_return,state', [
    (10., None, 'CONTINUATION_CONFIRMED'), (9., None, 'CONTINUATION_CONFIRMED'),
    (10., 0., 'CONTINUATION_CONFIRMED'), (9., 0., 'NO_CONFIRMATION'),
    (10., .01, 'NO_CONFIRMATION')])
def test_first15_only_explicit_return_gates(close, minimum_return, state):
    rows = [bar(1, minutes=15, close=close)]
    result = first(bullish_close_location=0., minimum_return=minimum_return,
                   invalidate_below_open=False).evaluate(
        rows, decision_time=rows[-1].available_at, prior_bias=bias_context('UP'))
    assert result.state == state


@pytest.mark.parametrize('volume,traded_value', [(0., 1.), (1., 0.)])
def test_intraday_inconsistent_traded_value_rejects(volume, traded_value):
    with pytest.raises(ValueError, match='inconsistent volume/traded_value'):
        bar(1, volume=volume, traded_value=traded_value)


@pytest.mark.parametrize('engine,kwargs', [(first(), {'prior_bias': bias_context('UP')}),
                                         (orb(), {}), (momentum(), {})])
def test_non_vwap_engines_allow_missing_traded_value(engine, kwargs):
    rows = [bar(i, close=13. if i == 3 else 10., traded_value=None) for i in range(1, 4)]
    result = engine.evaluate(rows, decision_time=rows[-1].available_at, **kwargs)
    assert result.state in ('READY', 'CONTINUATION_CONFIRMED')
