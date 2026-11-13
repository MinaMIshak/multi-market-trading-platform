from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import (
    CONTRACT_VERSION, NEXT_SESSION, REASONS, RULES_VERSION, TOUCH, OutcomeAssumptions,
    evaluate_decision_outcome)
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
VALID_UNTIL = AT + timedelta(days=3)          # 2026-11-08T12:00Z: sessions 0-2 end before it
COMPLETE = datetime(2026, 11, 9, tzinfo=timezone.utc)
INSTRUMENT = UUID(int=7)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))


def observation(trigger=TOUCH, target='12', flat_by=None, holding=3, market='EGX'):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol='TEST', decision_time=AT,
                          data_cutoff=AT - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=INSTRUMENT,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=AT, fresh_until=AT + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger=trigger, entry_low=D('9.5'), entry_high=D('10.5'),
                          entry_reference=D('10'), stop=D('9'), target=D(target),
                          valid_until=VALID_UNTIL, max_holding_sessions=holding, flat_by=flat_by)
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  None, recorded_at=AT + timedelta(minutes=5))
    return sha256(canonical_bytes(record)).hexdigest(), record


def bar(day, o, h, l, c, instrument=str(INSTRUMENT), symbol='TEST'):
    start = datetime(2026, 11, 5, 13, tzinfo=timezone.utc) + timedelta(days=day)
    return PaperReplayBar(
        instrument_id=instrument, venue_id='XCAI', symbol=symbol, session_id=f'S{day}',
        market_date=date(2026, 11, 5) + timedelta(days=day), session_sequence=1,
        interval_start_utc=start, interval_end_utc=start + timedelta(hours=7),
        available_at_utc=start + timedelta(hours=7, minutes=30),
        open=D(o), high=D(h), low=D(l), close=D(c), volume=D('1000'),
        source_id='fixture-bars', provenance_id=f'prov-{day}')


def available(day):
    return bar(day, '1', '1', '1', '1').available_at_utc


def run(bars, obs=None, assumptions=ZERO, complete=COMPLETE, evaluated=None):
    identity, record = obs or observation()
    return evaluate_decision_outcome(
        identity, record, tuple(bars), assumptions=assumptions,
        path_complete_through_at=complete,
        evaluated_at=evaluated or complete + timedelta(hours=1))


FILL = bar(0, '10', '10.6', '9.8', '10.4')          # opens inside the zone: fill at open 10
ABOVE = [bar(d, '10.8', '11', '10.6', '10.9') for d in range(3)]


def test_target_close_in_planned_r_with_provenance():
    out = run([FILL, bar(1, '10.5', '12.3', '9.9', '12.1')])
    assert out['contract_version'] == CONTRACT_VERSION == 'decision-outcome-v1'
    assert out['rules_version'] == RULES_VERSION
    assert (out['status'], out['reason']) == ('CLOSED', None)
    assert out['entry'] == {'market_date': '2026-11-05', 'session_id': 'S0', 'raw_price': '10',
                            'price': '10', 'at_open': True}
    assert out['exit']['reason'] == 'TARGET' and out['exit']['raw_price'] == '12'
    assert out['exit']['at_open'] is False
    m = out['metrics']
    assert D(m['gross_r']) == D(m['net_r']) == 2
    assert D(m['mae_r']) == D('-0.2') and D(m['mfe_r']) == 2
    assert m['holding_sessions'] == 2 and D(m['r_unit_per_share']) == 1
    assert [b['provenance_id'] for b in out['bars_used']] == ['prov-0', 'prov-1']
    assert out['bars_used'][0]['available_at'] == '2026-11-05T20:30:00Z'


def test_explicit_slippage_and_costs_reduce_net_r():
    costs = OutcomeAssumptions(assumptions_version='fixture-costs', market='EGX',
                               entry_slippage_bps=D('10'), exit_slippage_bps=D('10'),
                               cost_bps_per_side=D('5'))
    out = run([FILL, bar(1, '10.5', '12.3', '9.9', '12.1')], assumptions=costs)
    assert D(out['entry']['price']) == D('10.01') and D(out['exit']['price']) == D('11.988')
    assert D(out['metrics']['cost_per_share']) == D('0.010999')
    assert D(out['metrics']['gross_r']) == 2 and D(out['metrics']['net_r']) == D('1.967001')
    assert out['assumptions'] == {
        'schema_version': 'decision-outcome-assumptions-v1', 'assumptions_version': 'fixture-costs',
        'market': 'EGX', 'entry_slippage_bps': '10', 'exit_slippage_bps': '10',
        'cost_bps_per_side': '5', 'basis': 'DECLARED_UNVALIDATED'}


def test_stop_and_target_in_one_session_assumes_stop_first():
    out = run([FILL, bar(1, '10', '12.5', '8.9', '11')])
    assert out['exit']['reason'] == 'STOP' and out['exit']['raw_price'] == '9'
    assert D(out['metrics']['gross_r']) == -1
    assert D(out['metrics']['mae_r']) == -1 and D(out['metrics']['mfe_r']) == D('0.6')


def test_gap_through_stop_exits_at_open_beyond_one_r():
    out = run([FILL, bar(1, '8.5', '8.8', '8.2', '8.6')])
    assert out['exit'] == {'market_date': '2026-11-06', 'session_id': 'S1', 'reason': 'STOP',
                           'raw_price': '8.5', 'price': '8.5', 'at_open': True}
    assert D(out['metrics']['gross_r']) == D('-1.5') and D(out['metrics']['mae_r']) == D('-1.5')


def test_gap_through_target_is_capped_at_target():
    out = run([FILL, bar(1, '12.5', '13', '12.2', '12.8')])
    assert out['exit']['reason'] == 'TARGET' and out['exit']['raw_price'] == '12'
    assert out['exit']['at_open'] is True
    assert D(out['metrics']['gross_r']) == 2 and D(out['metrics']['mae_r']) == D('-0.2')


def test_time_exit_at_close_of_last_holding_session_counting_fill_session():
    bars = [FILL, bar(1, '10.4', '10.9', '9.6', '10.6'), bar(2, '10.6', '11', '10.1', '10.8'),
            bar(3, '10.8', '13', '10.7', '12.9')]
    out = run(bars)
    assert out['exit']['reason'] == 'TIME_EXIT' and out['exit']['raw_price'] == '10.8'
    assert out['metrics']['holding_sessions'] == 3 and D(out['metrics']['gross_r']) == D('0.8')
    assert [b['session_id'] for b in out['bars_used']] == ['S0', 'S1', 'S2']
    assert out['bars_supplied'] == 4


def test_open_position_stays_pending_without_metrics():
    out = run([FILL], complete=available(0))
    assert (out['status'], out['reason']) == ('PENDING_PATH', 'POSITION_OPEN')
    assert out['entry']['raw_price'] == '10' and out['exit'] is None and out['metrics'] is None


def test_fill_session_target_needs_open_fill_or_close_at_target():
    intrabar = bar(0, '9.4', '12.5', '9.3', '11')   # opens below zone, fills at 9.5 intrabar
    out = run([intrabar])
    assert out['entry'] == {'market_date': '2026-11-05', 'session_id': 'S0', 'raw_price': '9.5',
                            'price': '9.5', 'at_open': False}
    assert (out['status'], out['reason']) == ('PENDING_PATH', 'POSITION_OPEN')
    out = run([bar(0, '9.4', '12.5', '9.3', '12.1')])
    assert out['exit']['reason'] == 'TARGET' and out['metrics']['holding_sessions'] == 1


def test_touch_entry_window_pending_expired_and_unevaluable():
    out = run(ABOVE[:1], complete=available(0))
    assert (out['status'], out['reason']) == ('PENDING_PATH', 'AWAITING_ENTRY_WINDOW')
    late_touch = bar(3, '10.4', '10.7', '10.2', '10.5')   # ends after valid_until
    out = run(ABOVE + [late_touch])
    assert (out['status'], out['reason']) == ('NO_ENTRY', 'NOT_TOUCHED_BEFORE_EXPIRY')
    assert out['entry'] is None and out['metrics'] is None
    assert [b['session_id'] for b in out['bars_used']] == ['S0', 'S1', 'S2']
    out = run([])
    assert (out['status'], out['reason']) == ('UNEVALUABLE', 'NO_SESSION_BARS_IN_ENTRY_WINDOW')


def test_next_session_rule_uses_first_session_only():
    obs = observation(trigger=NEXT_SESSION)
    out = run([bar(0, '9.4', '10', '9.2', '9.8')], obs=obs)
    assert (out['status'], out['reason']) == ('NO_ENTRY', 'NO_FILL_GAP_DOWN')
    out = run([ABOVE[0], bar(1, '10.4', '10.7', '10.2', '10.5')], obs=obs)
    assert (out['status'], out['reason']) == ('NO_ENTRY', 'NO_FILL_GAP_UP')
    assert [b['session_id'] for b in out['bars_used']] == ['S0']
    out = run([bar(0, '10.8', '11', '10.3', '10.6')], obs=obs)
    assert out['entry']['raw_price'] == '10.5' and out['entry']['at_open'] is False
    assert out['reason'] == 'POSITION_OPEN'
    out = run([], obs=obs, complete=AT + timedelta(minutes=30))
    assert (out['status'], out['reason']) == ('PENDING_PATH', 'AWAITING_ENTRY_WINDOW')
    assert out['entry_trigger'] == NEXT_SESSION


def test_flat_by_is_unevaluable_with_session_bars_once_filled():
    obs = observation(flat_by=AT + timedelta(days=2))
    out = run([FILL, bar(1, '10.5', '12.3', '9.9', '12.1')], obs=obs)
    assert (out['status'], out['reason']) == ('UNEVALUABLE', 'FLAT_BY_NOT_SUPPORTED_BY_SESSION_BARS')
    assert out['entry'] is not None and out['exit'] is None and out['metrics'] is None
    out = run(ABOVE, obs=obs)
    assert (out['status'], out['reason']) == ('NO_ENTRY', 'NOT_TOUCHED_BEFORE_EXPIRY')


def test_slipped_entry_beyond_target_is_unevaluable_not_a_trade():
    slip = OutcomeAssumptions(assumptions_version='fixture-slip', market='EGX',
                              entry_slippage_bps=D('100'), exit_slippage_bps=D('0'),
                              cost_bps_per_side=D('0'))
    out = run([bar(0, '10.5', '10.55', '10.4', '10.5')], obs=observation(target='10.6'),
              assumptions=slip)
    assert (out['status'], out['reason']) == ('UNEVALUABLE', 'ENTRY_OUTSIDE_STOP_TARGET')
    assert out['entry'] is None and out['metrics'] is None


def test_later_bars_cannot_change_a_closed_outcome():
    short = run([FILL, bar(1, '10.5', '12.3', '9.9', '12.1')])
    longer = run([FILL, bar(1, '10.5', '12.3', '9.9', '12.1'), bar(2, '5', '6', '4', '5')])
    for key in ('status', 'reason', 'entry', 'exit', 'metrics', 'bars_used'):
        assert short[key] == longer[key]


@pytest.mark.parametrize('bars, complete, evaluated, match', [
    ([bar(-1, '10', '10.6', '9.8', '10.4')], COMPLETE, None, 'before detection'),
    ([FILL, bar(1, '10.5', '12.3', '9.9', '12.1')], available(0), None, 'not available'),
    ([FILL], COMPLETE, COMPLETE - timedelta(seconds=1), 'between detection'),
    ([], AT - timedelta(seconds=1), None, 'between detection'),
    ([FILL], COMPLETE, COMPLETE.replace(tzinfo=None) + timedelta(hours=1), 'timezone-aware'),
])
def test_point_in_time_violations_fail_closed(bars, complete, evaluated, match):
    with pytest.raises(ValueError, match=match):
        run(bars, complete=complete, evaluated=evaluated)


def test_naive_path_complete_rejected():
    with pytest.raises(ValueError, match='timezone-aware'):
        run([FILL], complete=COMPLETE.replace(tzinfo=None), evaluated=COMPLETE + timedelta(hours=1))


@pytest.mark.parametrize('bars', [
    [bar(1, '10', '10.6', '9.8', '10.4'), FILL],
    [FILL, bar(0, '10', '10.6', '9.8', '10.4')],
    [bar(0, '10', '10.6', '9.8', '10.4', instrument=str(UUID(int=8)))],
    [bar(0, '10', '10.6', '9.8', '10.4', symbol='OTHER')],
])
def test_bar_order_and_identity_fail_closed(bars):
    with pytest.raises(ValueError):
        run(bars)


def test_bars_must_be_exact_tuple():
    identity, record = observation()
    with pytest.raises(ValueError, match='PaperReplayBar'):
        evaluate_decision_outcome(identity, record, [FILL], assumptions=ZERO,
                                  path_complete_through_at=COMPLETE, evaluated_at=COMPLETE)


def test_record_is_bound_to_its_content_address():
    identity, record = observation()
    edited = deepcopy(record)
    edited['terms']['target'] = '13'
    with pytest.raises(ValueError):
        run([FILL], obs=(identity, edited))
    with pytest.raises(ValueError):
        run([FILL], obs=('b' * 64, record))
    with pytest.raises(ValueError):
        run([FILL], obs=(identity, None))


def test_market_separation_and_explicit_assumptions():
    us = observation(market='US')
    with pytest.raises(ValueError, match='market'):
        run([FILL], obs=us)
    us_zero = OutcomeAssumptions(assumptions_version='fixture-us', market='US',
                                 entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                                 cost_bps_per_side=D('0'))
    assert run([FILL], obs=us, assumptions=us_zero)['market'] == 'US'
    for bad in ({'entry_slippage_bps': 1.0}, {'cost_bps_per_side': D('NaN')},
                {'exit_slippage_bps': D('-1')}, {'assumptions_version': 'has space'}):
        fields = {'assumptions_version': 'x', 'market': 'EGX', 'entry_slippage_bps': D('0'),
                  'exit_slippage_bps': D('0'), 'cost_bps_per_side': D('0')} | bad
        with pytest.raises(ValueError):
            OutcomeAssumptions(**fields)
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        run([FILL], assumptions={'market': 'EGX'})


def test_deterministic_labelled_hypothetical_and_record_unchanged():
    identity, record = observation()
    before = deepcopy(record)
    bars = [FILL, bar(1, '10.5', '12.3', '9.9', '12.1')]
    first = run(bars, obs=(identity, record))
    assert canonical_bytes(first) == canonical_bytes(run(bars, obs=(identity, record)))
    assert record == before
    assert first['observation_identity'] == identity and first['decision_id'] == record['decision_id']
    assert (first['strategy_id'], first['strategy_version'], first['config_id']) == \
        ('fixture-challenger', '1', 'fixture')
    assert first['fill_kind'] == 'HYPOTHETICAL_FROM_SESSION_BARS'
    assert first['execution_evidence'] == 'NONE' and first['performance_claim'] == 'NONE'
    assert first['path_completeness'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert first['evaluated_at_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert first['corporate_actions'] == 'NOT_CHECKED'
    assert (first['mode'], first['live_money'], first['execution_allowed'], first['promotion']) == \
        ('SHADOW', False, False, 'NOT_AUTHORIZED')
    assert not {'signal', 'order', 'side', 'quantity'} & set(first)
    assert first['status'] in REASONS
