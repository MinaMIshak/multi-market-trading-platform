"""Offline arithmetic fixtures only; no strategy-validation evidence."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.data.intraday import IntradayBar
from app.domain.models import TradePlan, RiskDecision
from app.domain.enums import RiskDecisionType, SignalDirection
from app.paper import PaperExecutionConfig, PaperSimulationInput, simulate_paper

D = Decimal
AT = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)


def config(**changes):
    values = dict(config_version='paper-execution-v1', entry_slippage_bps=D(0),
                  stop_slippage_bps=D(0), target_slippage_bps=D(0),
                  scheduled_exit_slippage_bps=D(0), cost_bps_per_side=D(0),
                  fixed_cost_per_side=D(0), max_volume_participation_pct=None,
                  profit_target='TARGET_1', time_exit_at=None, session_end_at=None)
    return PaperExecutionConfig(**(values | changes))


def plan(**changes):
    values = dict(trade_plan_id=UUID(int=1), signal_id=UUID(int=2), symbol='SWDY',
                  entry_low=D(10), entry_high=D(11), entry_reference=D(10),
                  stop_price=D(9), target_1=D(12), created_at=AT,
                  valid_until=AT + timedelta(minutes=5))
    return TradePlan(**(values | changes))


def risk(**changes):
    values = dict(risk_decision_id=UUID(int=3), trade_plan_id=UUID(int=1),
                  policy_version='m5-test', policy_identity='fixture',
                  decision=RiskDecisionType.APPROVE, account_equity=D(10000),
                  risk_budget=D(100), approved_risk=D(20), quantity=10,
                  max_position_value=D(1000), portfolio_exposure_pct=D('.1'))
    return RiskDecision(**(values | changes))


def bar(n=0, o=10, h=11, l=10, c=10, **changes):
    values = dict(symbol='SWDY', interval_start=AT + timedelta(minutes=n),
                  interval_end=AT + timedelta(minutes=n+1), available_at=AT + timedelta(minutes=n+1),
                  open=float(o), high=float(h), low=float(l), close=float(c), volume=100.0,
                  traded_value=None, session_id='s1', market_date=AT.date(), sequence=n+1,
                  session_phase='CONTINUOUS', is_final=True, source_id='fixture', provenance_id='p1')
    return IntradayBar(**(values | changes))


def request(bars=(), **changes):
    values = dict(schema_version='paper-simulation-v1', trade_plan=plan(), risk_decision=risk(),
                  bars=tuple(bars), admission_time=AT, config=config(), session_id='s1',
                  market_date=AT.date(), source_id='fixture', provenance_id='p1')
    return PaperSimulationInput(**(values | changes))


def run(bars=(), **changes):
    return simulate_paper(request(bars, **changes))


@pytest.mark.parametrize('decision,quantity', [(RiskDecisionType.APPROVE, 10), (RiskDecisionType.REDUCE, 3)])
def test_admission(decision, quantity):
    out = run([bar()], risk_decision=risk(decision=decision, quantity=quantity))
    assert out.position.quantity == out.position.entry.quantity == quantity
    assert out.position.entry.price == D(10)
    assert out.state == 'OPEN' and out.metrics is None and out.outcome is None


def test_block_has_no_execution_artifacts():
    out = run([bar()], risk_decision=risk(decision=RiskDecisionType.BLOCK, quantity=0, approved_risk=D(0)))
    assert out.state == 'REJECTED' and out.rejection_reason == 'M5_BLOCK'
    assert out.position is out.metrics is out.outcome is None


@pytest.mark.parametrize('changes', [dict(trade_plan_id=UUID(int=99)), dict(quantity=0),
    dict(quantity=-1), dict(quantity=True), dict(approved_risk=D(0)), dict(approved_risk=D('-1')),
    dict(approved_risk=D('Infinity')), dict(policy_identity=' ')])
def test_bad_risk_revalidated(changes):
    with pytest.raises(ValueError):
        run(risk_decision=risk().model_copy(update=changes))


@pytest.mark.parametrize('changes', [dict(direction=SignalDirection.SHORT, stop_price=D(13), target_1=D(8)),
    dict(target_1=None), dict(target_1=D('Infinity')), dict(entry_low=D(8)),
    dict(created_at=AT.replace(tzinfo=None)), dict(valid_until=AT.replace(tzinfo=None))])
def test_invalid_plan(changes):
    with pytest.raises(ValueError):
        run(trade_plan=plan().model_copy(update=changes))


@pytest.mark.parametrize('field', list(PaperExecutionConfig.model_fields))
def test_all_config_fields_explicit(field):
    values = config().model_dump()
    del values[field]
    with pytest.raises(ValidationError):
        PaperExecutionConfig(**values)


@pytest.mark.parametrize('field', ['entry_slippage_bps', 'stop_slippage_bps', 'target_slippage_bps',
    'scheduled_exit_slippage_bps', 'cost_bps_per_side', 'fixed_cost_per_side'])
@pytest.mark.parametrize('value', [D('-1'), D('NaN'), D('Infinity')])
def test_invalid_numbers(field, value):
    with pytest.raises(ValueError):
        config(**{field: value})


@pytest.mark.parametrize('changes', [dict(config_version='v2'), dict(profit_target='TARGET_2'),
    dict(max_volume_participation_pct=D(0)), dict(max_volume_participation_pct=D('1.01')),
    dict(time_exit_at=AT.replace(tzinfo=None)), dict(session_end_at=AT.replace(tzinfo=None))])
def test_invalid_config(changes):
    with pytest.raises(ValueError):
        config(**changes)


@pytest.mark.parametrize('changes', [dict(schema_version='v2'), dict(admission_time=AT.replace(tzinfo=None)),
    dict(admission_time=AT-timedelta(seconds=1)), dict(admission_time=AT+timedelta(minutes=5)),
    dict(session_id='other'), dict(source_id='other'), dict(provenance_id='other'),
    dict(market_date=date(2026, 9, 11)), dict(session_id=' ')])
def test_boundary_mismatch(changes):
    with pytest.raises(ValueError):
        run([bar()], **changes)


@pytest.mark.parametrize('changes', [dict(symbol='OTHER'), dict(is_final=False),
    dict(session_phase='AUCTION'), dict(high=float('inf')), dict(volume=float('nan')),
    dict(interval_start=AT.replace(tzinfo=None)), dict(low=12.0)])
def test_bad_bar_revalidated(changes):
    with pytest.raises(ValueError):
        run([bar().model_copy(update=changes)])


def test_canonical_objects_only():
    for field, value in [('trade_plan', plan().model_dump()), ('risk_decision', risk().model_dump()),
                         ('bars', (bar().model_dump(),))]:
        with pytest.raises(ValueError):
            request(**{field: value})
    with pytest.raises(TypeError):
        simulate_paper({})


@pytest.mark.parametrize('bars', [(bar(1), bar()), (bar(), bar()), (bar(), bar(2)),
    (bar(0, available_at=AT+timedelta(minutes=3)), bar(1)),
    (bar(), bar(1, interval_start=AT+timedelta(seconds=30)))])
def test_chronology_not_sorted_or_repaired(bars):
    with pytest.raises(ValueError, match='chronology'):
        run(bars)



def test_nonempty_replay_requires_opening_sequence_one():
    with pytest.raises(ValueError, match='opening sequence'):
        run([bar(1)])


def test_consecutive_suffix_cannot_manufacture_no_fill():
    # This is internally consecutive and reaches valid_until, but sequences
    # 1-2 are missing. Reject rather than manufacturing NO_FILL from a
    # truncated history.
    suffix = (
        bar(2, o=13, h=14, l=13, c=13),
        bar(3, o=13, h=14, l=13, c=13),
        bar(4, o=13, h=14, l=13, c=13),
    )
    assert suffix[0].sequence == 3
    assert suffix[-1].sequence == 5
    assert suffix[-1].interval_end == plan().valid_until

    with pytest.raises(ValueError, match='opening sequence'):
        run(suffix)

@pytest.mark.parametrize('o,h,l,c,expected', [(10.5,11,10,10,D('10.5')),
    (9.5,11,9.5,10,D(10)), (11.5,11.5,10.5,11,D(11))])
def test_entry_pricing(o,h,l,c,expected):
    out = run([bar(o=o,h=h,l=l,c=c)])
    assert out.position.entry.raw_price == out.position.entry.price == expected


def test_no_intersection_and_early_end():
    out = run([bar(o=13,h=14,l=13,c=13)])
    assert out.state == 'INCOMPLETE' and out.position is out.outcome is out.metrics is None
    assert run().state == 'INCOMPLETE'


def test_admission_and_expiry_straddles():
    out = run([bar(),bar(1),bar(2)], admission_time=AT+timedelta(seconds=90))
    assert out.position.entry.bar_sequence == 3
    expiry = AT+timedelta(seconds=30)
    out = run([bar()], trade_plan=plan(valid_until=expiry))
    assert out.state == 'NO_FILL' and out.position is out.metrics is None
    assert run([bar()], trade_plan=plan(valid_until=AT+timedelta(minutes=1))).position.entry.price == 10


def test_entry_slippage_and_volume_full_quantity():
    out = run([bar(volume=99.0),bar(1,volume=100.0)],
              config=config(entry_slippage_bps=D(100), max_volume_participation_pct=D('.1')))
    assert out.position.entry.bar_sequence == 2
    assert out.position.entry.quantity == 10
    assert out.position.entry.price == D('10.1')
    out = run([bar(volume=99.0)], config=config(max_volume_participation_pct=D('.1')))
    assert out.position is None and out.state == 'INCOMPLETE'


@pytest.mark.parametrize('o,h,l,c,reason,raw', [(10,11,8,10,'STOP',D(9)),
    (8,13,7,10,'STOP',D(8)), (10,13,10,11,'TARGET_1',D(12)),
    (13,20,7,10,'TARGET_1',D(12)), (10,13,8,11,'STOP',D(9))])
def test_exits(o,h,l,c,reason,raw):
    out = run([bar(),bar(1,o,h,l,c)], config=config(stop_slippage_bps=D(100),target_slippage_bps=D(200)))
    assert out.exit_reason == reason
    assert out.position.exit.raw_price == raw
    assert out.position.exit.price == raw * (D('.99') if reason=='STOP' else D('.98'))
    assert out.outcome == ('LOSS' if reason=='STOP' else 'WIN')


def test_entry_bar_ambiguity():
    out = run([bar(o=13,h=14,l=10,c=11)])
    assert out.state == 'OPEN' and out.position.entry.price == 11
    out = run([bar(o=13,h=14,l=8,c=11)])
    assert out.outcome == 'LOSS' and out.position.exit.price == 9
    out = run([bar(o=8,h=14,l=8,c=11)])
    assert out.outcome == 'LOSS' and out.position.exit.raw_price == 9  # no pre-entry gap exit at 8
    out = run([bar(o=13,h=14,l=10,c=12)])
    assert out.outcome == 'WIN' and out.position.exit.price == 12
    out = run([bar(o=10,h=13,l=8,c=11)])
    assert out.outcome == 'LOSS' and out.position.exit.price == 9


@pytest.mark.parametrize('field,reason', [('time_exit_at','TIME_EXIT'),('session_end_at','SESSION_END')])
def test_scheduled_boundary(field,reason):
    out = run([bar(),bar(1),bar(2,o=10.5,h=14,l=8,c=11)],
              config=config(**{field: AT+timedelta(seconds=90)}, scheduled_exit_slippage_bps=D(100)))
    assert out.exit_reason == reason and out.outcome == 'TIME_EXIT'
    assert out.position.exit.bar_sequence == 3 and out.position.exit.at_open
    assert out.position.exit.raw_price == D('10.5') and out.position.exit.price == D('10.395')
    assert out.metrics.mae == 0 and out.metrics.mfe == 1


@pytest.mark.parametrize('o,h,l,reason,price', [(8,14,7,'STOP',8), (13,14,8,'TARGET_1',12)])
def test_open_gap_precedes_schedule(o,h,l,reason,price):
    out = run([bar(),bar(1,o,h,l,10)], config=config(time_exit_at=AT+timedelta(minutes=1)))
    assert out.exit_reason == reason and out.position.exit.price == price


def test_earlier_schedule_and_no_invented_close():
    bars = [bar(),bar(1),bar(2)]
    out = run(bars, config=config(time_exit_at=AT+timedelta(minutes=2),session_end_at=AT+timedelta(minutes=1)))
    assert out.exit_reason == 'SESSION_END' and out.position.exit.bar_sequence == 2
    out = run(bars)
    assert out.state == 'OPEN' and out.metrics is out.outcome is out.position.exit is None
    assert run([bar()], config=config(time_exit_at=AT)).state == 'INCOMPLETE'


def test_exact_costs_pnl_r_and_excursions():
    out = run([bar(),bar(1,o=10,h=20,l=9.5,c=11)],
              config=config(entry_slippage_bps=D(100), target_slippage_bps=D(100),
                            cost_bps_per_side=D(10),fixed_cost_per_side=D(2)))
    m = out.metrics
    assert (m.entry_notional,m.exit_notional) == (D(101),D('118.8'))
    assert (m.entry_cost,m.exit_cost) == (D('2.101'),D('2.1188'))
    assert m.gross_pnl == D('17.8') and m.net_pnl == D('13.5802')
    assert m.r_multiple == D('.67901')  # approved 20, not entry-stop risk 11
    assert (m.mae,m.mfe,m.mae_r,m.mfe_r) == (D('.1'),D('1.9'),D('.05'),D('.95'))


def test_terminal_extrema_censored_and_future_does_not_resolve_stop():
    bars = [bar(o=10,h=10,l=10,c=10),bar(1,o=10,h=100,l=1,c=50)]
    out = run(bars)
    assert out.outcome == 'LOSS' and out.metrics.mae == 1 and out.metrics.mfe == 0
    assert run(bars+[bar(2,o=50,h=100,l=40,c=90)]) == out
    out = run([bar(o=13,h=100,l=10,c=11),bar(1,o=11,h=12,l=1,c=11)])
    assert out.metrics.mfe == 0 and out.metrics.mae == 2


def test_conclusive_no_fill_and_determinism():
    bars = [bar(i,o=13,h=14,l=13,c=13) for i in range(5)]
    assert run(bars[:-1]).state == 'INCOMPLETE'
    out = run(bars)
    assert out.state == out.outcome == 'NO_FILL' and out.position is out.metrics is None
    req = request([bar(),bar(1,o=10,h=13,l=10,c=11)])
    before = req.model_dump()
    first = simulate_paper(req)
    with localcontext() as ctx:
        ctx.prec = 6
        assert simulate_paper(req) == first
    assert simulate_paper(req) == first and req.model_dump() == before


@pytest.mark.parametrize('field', ['stop_slippage_bps','target_slippage_bps','scheduled_exit_slippage_bps'])
def test_nonpositive_exit_fails_closed(field):
    terminal = bar(1,o=10,h=11,l=8,c=10) if field=='stop_slippage_bps' else bar(1,o=10,h=13,l=10,c=11)
    cfg = {field:D(10000)}
    if field=='scheduled_exit_slippage_bps':
        cfg['time_exit_at'] = AT+timedelta(minutes=1)
    with pytest.raises(ValueError, match='execution price'):
        run([bar(),terminal], config=config(**cfg))


def test_slipped_entry_geometry_fails_closed():
    with pytest.raises(ValueError, match='geometry'):
        run([bar()], config=config(entry_slippage_bps=D(2000)))


def test_candidate_is_not_executable():
    from app.strategies.contracts import Candidate
    candidate = Candidate(strategy_id='fixture', strategy_version='1', config_id='fixture',
                          symbol='SWDY', decision_time=AT, data_cutoff=AT, state='READY',
                          entry_reference=10.0, stop_reference=9.0, evidence_json='{}')
    with pytest.raises(ValueError, match='TradePlan'):
        request(trade_plan=candidate)


def test_revalidate_copied_request_and_config():
    req = request([bar()])
    with pytest.raises(ValueError):
        simulate_paper(req.model_copy(update={'schema_version':'unknown'}))
    with pytest.raises(ValueError):
        simulate_paper(req.model_copy(update={'config':config().model_copy(update={'cost_bps_per_side':D('-1')})}))
    with pytest.raises(ValueError):
        run([bar().model_copy(update={'available_at':AT})])


def test_delayed_availability_and_timezone_replay():
    bars = [bar(available_at=AT+timedelta(minutes=4))]
    out = run(bars)
    assert out.position.entry.known_at == AT+timedelta(minutes=4)
    assert out.position.entry.interval_start == AT
    other_zone = timezone(timedelta(hours=3))
    assert run(bars, admission_time=AT.astimezone(other_zone)) == out


def test_nonterminal_entry_high_not_credited_and_surviving_path_extrema():
    out = run([bar(o=13,h=100,l=10,c=11),bar(1,o=11,h=11.5,l=9.5,c=11),
               bar(2,o=11,h=13,l=10,c=11)])
    assert out.metrics.mae == D('1.5') and out.metrics.mfe == D(1)
    assert out.metrics.mae_r == D('.75') and out.metrics.mfe_r == D('.5')
