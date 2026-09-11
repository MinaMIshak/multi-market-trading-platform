"""Arithmetic and boundary fixtures only; no research validation."""
from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest

from app.paper import simulate_paper
from app.performance import (
    PerformanceAnalysisInput, PerformanceConfig, PerformanceObservation,
    PeriodicReturnSeries, SlippageScenario, analyze_performance,
)
from app.ui.performance import render_performance_dashboard
from test_m7_performance import AT, observation

D = Decimal


def series(**updates):
    values = dict(schema_version='periodic-return-v1', series_id='explicit',
                  period_seconds=60, period_ends=tuple(AT + timedelta(minutes=i) for i in range(4)),
                  returns=(D('-1'), D('-1'), D('3'), D('3')),
                  risk_free_return=D(0), sortino_target=D(0), minimum_samples=2)
    values.update(updates)
    return PeriodicReturnSeries(**values)


def report(rows=(), **updates):
    return analyze_performance(PerformanceAnalysisInput(
        schema_version='performance-analysis-v1',
        config=PerformanceConfig(config_version='performance-v1', starting_equity=D(100)),
        observations=tuple(rows), **updates))


def scenario(rows, name='explicit'):
    return SlippageScenario(schema_version='slippage-scenario-v1', scenario_id=name, observations=tuple(rows))


def revise(row, **changes):
    inp = row.paper_input.model_copy(update=changes)
    return PerformanceObservation(**{**row.model_dump(exclude={'paper_input', 'paper_result'}),
                                    'paper_input': inp, 'paper_result': simulate_paper(inp)})


def test_absent_and_insufficient():
    r = report().risk_adjusted
    assert r.sharpe is r.sortino is None
    assert r.sharpe_reason == r.sortino_reason == 'NO_PERIODIC_SERIES'
    r = report(periodic_returns=series(minimum_samples=5)).risk_adjusted
    assert r.sharpe is r.sortino is None
    assert r.sharpe_reason == r.sortino_reason == 'INSUFFICIENT_SAMPLES'


@pytest.mark.parametrize('changes', [
    {'schema_version': 'periodic-return-v2'}, {'schema_version': ' periodic-return-v1'},
    {'returns': (D('NaN'),)*4}, {'returns': (D('Infinity'),)*4},
    {'returns': (D('-Infinity'),)*4}, {'returns': (0.1,)*4},
    {'period_ends': (AT,)*4}, {'period_ends': (AT, AT-timedelta(minutes=1), AT, AT)},
    {'period_ends': (AT,)}, {'period_ends': (AT.replace(tzinfo=None),)*4},
    {'period_seconds': 0}, {'period_seconds': True}, {'period_seconds': 120},
    {'minimum_samples': 1}, {'minimum_samples': True}, {'minimum_samples': '2'},
    {'annualization_factor': D(0)}, {'annualization_factor': D(-1)},
    {'annualization_factor': D('Infinity')}, {'risk_free_return': D('NaN')},
    {'sortino_target': D('NaN')}, {'frequency': 'daily'},
])
def test_invalid_periodic(changes):
    with pytest.raises(ValueError):
        series(**changes)


def test_ratios_exact_conventions_and_determinism():
    s = series()
    a = report(periodic_returns=s)
    assert a == report(periodic_returns=s)
    with localcontext(Context(prec=34)):
        assert a.risk_adjusted.sharpe == D(1) / (D(16)/3).sqrt()
        assert a.risk_adjusted.sortino == D(1) / D('.5').sqrt()
        scaled = report(periodic_returns=series(annualization_factor=D(4))).risk_adjusted
        assert scaled.sharpe == a.risk_adjusted.sharpe * 2
        assert scaled.sortino == a.risk_adjusted.sortino * 2
    assert report(periodic_returns=series(risk_free_return=D(1))).risk_adjusted.sharpe == 0
    assert report(periodic_returns=series(sortino_target=D(1))).risk_adjusted.sortino == 0
    assert a.risk_adjusted.series.annualization_factor is None


def test_zero_and_copy_revalidation():
    r = report(periodic_returns=series(returns=(D(1),)*4)).risk_adjusted
    assert r.sharpe is r.sortino is None
    assert r.sharpe_reason == 'ZERO_DISPERSION'
    assert r.sortino_reason == 'ZERO_DOWNSIDE_DEVIATION'
    with pytest.raises(ValueError):
        report(periodic_returns=series().model_copy(update={'minimum_samples': 0}))


def test_exact_sensitivity_and_no_hidden_scenarios():
    rows = (observation('WIN'), observation('LOSS', trade_id=2), observation('OPEN', trade_id=3))
    before = tuple(r.model_dump() for r in rows)
    assert report(rows).slippage_sensitivity == ()
    changed = tuple(revise(r, config=r.paper_input.config.model_copy(update={
        'target_slippage_bps': D(100), 'stop_slippage_bps': D(100)})) for r in rows)
    args = dict(slippage_scenarios=(scenario(rows, 'base'), scenario(changed)), baseline_scenario_id='base')
    result = report(rows, **args)
    assert result == report(rows, **args)
    base, altered = result.slippage_sensitivity
    assert base.total_net_pnl == 10
    assert altered.completed_count == 2
    assert altered.total_net_pnl == D('7.9')
    assert altered.net_expectancy == D('3.95')
    assert altered.max_drawdown_amount == D('10.9')
    with localcontext(Context(prec=34)):
        assert altered.profit_factor == D('18.8') / D('10.9')
        assert altered.max_drawdown_pct == D('10.9') / D('118.8')
    assert altered.delta_total_net_pnl == D('-2.1')
    assert altered.delta_net_expectancy == D('-1.05')
    assert tuple(r.model_dump() for r in rows) == before


@pytest.mark.parametrize('field', ['cost_bps_per_side', 'fixed_cost_per_side', 'max_volume_participation_pct', 'time_exit_at'])
def test_unauthorized_config(field):
    row = observation('WIN')
    value = AT + timedelta(minutes=1) if field == 'time_exit_at' else D('.1')
    changed = revise(row, config=row.paper_input.config.model_copy(update={field: value}))
    with pytest.raises(ValueError, match='unauthorized'):
        report((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize('field', ['bars', 'trade_plan', 'risk_decision', 'admission_time'])
def test_changed_underlying(field):
    row = observation('WIN')
    changes = {'bars': row.paper_input.bars[:1],
               'trade_plan': row.paper_input.trade_plan.model_copy(update={'target_1': D(13)}),
               'risk_decision': row.paper_input.risk_decision.model_copy(update={'quantity': 9}),
               'admission_time': AT + timedelta(seconds=1)}
    changed = revise(row, **{field: changes[field]})
    with pytest.raises(ValueError, match='underlying'):
        report((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize('field', ['strategy_id', 'strategy_version', 'market_regime'])
def test_changed_observation_identity(field):
    from app.domain.enums import MarketRegimeType
    row = observation('WIN')
    value = MarketRegimeType.RISK_OFF if field == 'market_regime' else 'changed'
    changed = row.model_copy(update={field: value})
    with pytest.raises(ValueError, match='identity'):
        report((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize('field', ['source_id', 'provenance_id', 'session_id', 'market_date'])
def test_changed_stream_identity(field):
    row = observation('INCOMPLETE')
    value = AT.date() + timedelta(days=1) if field == 'market_date' else 'changed'
    changed = revise(row, **{field: value})
    with pytest.raises(ValueError, match='underlying'):
        report((row,), slippage_scenarios=(scenario((changed,)),))


def test_scenario_fail_closed():
    row = observation('WIN')
    s = scenario((row,))
    for scenarios, baseline in [((s, s), None), ((scenario(()),), None),
                                ((scenario((row, row)),), None), ((), 'missing')]:
        with pytest.raises(ValueError):
            report((row,), slippage_scenarios=scenarios, baseline_scenario_id=baseline)
    with pytest.raises(ValueError):
        report((row,), slippage_scenarios=(s.model_copy(update={'schema_version': 'bad'}),))
    bad = row.model_copy(update={'paper_result': row.paper_result.model_copy(update={'state': 'OPEN'})})
    with pytest.raises(ValueError):
        scenario((bad,))


@pytest.mark.parametrize('value', [D(-1), D('NaN'), D('Infinity')])
def test_invalid_slippage(value):
    row = observation('WIN')
    with pytest.raises(ValueError):
        revise(row, config=row.paper_input.config.model_copy(update={'entry_slippage_bps': value}))


def test_populated_ui_is_pure_and_escapes(monkeypatch):
    row = observation('WIN')
    r = report((row,), slippage_scenarios=(scenario((row,), '<script>'),))
    def forbidden(*args, **kwargs):
        raise AssertionError('presenter must not analyze or simulate')
    monkeypatch.setattr('app.paper.simulator.simulate_paper', forbidden)
    monkeypatch.setattr('app.performance.analyzer.analyze_performance', forbidden)
    page = render_performance_dashboard(r)
    for text in ['Monthly consistency', 'Regime consistency', 'NO_PERIODIC_SERIES',
                 'Slippage sensitivity', '&lt;script&gt;', 'Observation states', '20']:
        assert text in page
    assert '<script>' not in page
    assert 'BUY' not in page and 'SELL' not in page


@pytest.mark.parametrize('field', ['risk_free_return', 'sortino_target', 'minimum_samples', 'period_seconds'])
def test_periodic_assumptions_are_required(field):
    values = {n: getattr(series(), n) for n in PeriodicReturnSeries.model_fields}
    del values[field]
    with pytest.raises(ValueError):
        PeriodicReturnSeries(**values)


def test_ambient_decimal_context_does_not_change_results():
    expected = report(periodic_returns=series())
    with localcontext(Context(prec=6)):
        assert report(periodic_returns=series()) == expected


def test_sensitivity_alignment_and_all_noncompleted_states():
    rows = tuple(observation(kind, trade_id=i+1) for i, kind in enumerate(
        ('REJECTED', 'NO_FILL', 'OPEN', 'INCOMPLETE')))
    result = report(rows, slippage_scenarios=(scenario(tuple(reversed(rows))),),
                    baseline_scenario_id='explicit').slippage_sensitivity[0]
    assert result.completed_count == 0
    assert result.total_net_pnl == result.max_drawdown_amount == result.max_drawdown_pct == 0
    assert result.net_expectancy is result.profit_factor is result.delta_net_expectancy is None
    assert result.delta_total_net_pnl == 0


def test_sensitivity_without_baseline_and_empty_set():
    result = report(slippage_scenarios=(scenario(()),)).slippage_sensitivity[0]
    assert result.delta_total_net_pnl is result.delta_net_expectancy is None
    assert result.net_expectancy is result.profit_factor is None


def test_ui_periodic_values_and_identity_escape():
    r = report(periodic_returns=series(series_id='<img src=x>', risk_free_return=D(1),
                                      sortino_target=D(1), annualization_factor=D(4)))
    page = render_performance_dashboard(r)
    assert '&lt;img src=x&gt;' in page and '<img src=x>' not in page
    assert 'Annualization factor' in page
    assert '<td>Sharpe</td><td>0' in page
    assert '<td>Sortino</td><td>0' in page


def test_empty_ui_has_no_economic_cards():
    page = render_performance_dashboard()
    for text in ['class="card"', 'Net Expectancy', '<td>', 'Monthly economics']:
        assert text not in page


@pytest.mark.parametrize('field', ['entry_slippage_bps', 'stop_slippage_bps',
                                   'target_slippage_bps', 'scheduled_exit_slippage_bps'])
def test_nonfinite_slippage_rejected_even_without_execution(field):
    row = observation('INCOMPLETE')
    with pytest.raises(ValueError):
        revise(row, config=row.paper_input.config.model_copy(update={field: D('Infinity')}))
