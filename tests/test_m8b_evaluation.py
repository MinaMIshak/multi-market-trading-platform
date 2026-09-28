"""Synthetic deterministic ENGINEERING fixtures only, never market evidence."""
from decimal import Context, Decimal, localcontext
from random import Random
import random
import socket
from uuid import UUID

import pytest

from app.domain.enums import MarketRegimeType
from app.performance.models import PerformanceConfig
from app.research import (
    BootstrapConfig, FrozenResearchProtocol, ResearchEvidenceCriteria,
    ResearchObservation, build_research_evidence, evaluate_development_oos,
    evaluate_frozen_holdout, partition_development,
)
from app.research.evaluation import _quantile, _sample_blocks, _statistics
from test_m7_performance import analyze, observation
from test_m7_extensions import revise
from test_m8_research import at, dataset, fold, plan, row

D = Decimal


def bootstrap(**changes):
    args = dict(config_version='research-bootstrap-v1', seed=123, replications=80,
                confidence_level=D('.9'), block_size=1)
    return BootstrapConfig(**(args | changes))


def criteria(**changes):
    args = dict(config_version='research-evidence-criteria-v1',
                minimum_completed_development_trades=2, minimum_completed_holdout_trades=2,
                minimum_net_expectancy=D(0), maximum_drawdown_fraction=D('.5'),
                minimum_profit_factor=None, minimum_net_expectancy_lower_bound=None)
    return ResearchEvidenceCriteria(**(args | changes))


def protocol(**changes):
    args = dict(schema_version='frozen-research-protocol-v1', protocol_id='predeclared-fixture',
                strategy_id='fixture-strategy', strategy_version='1', plan=plan(),
                performance_config=PerformanceConfig(config_version='performance-v1', starting_equity=D(100)),
                bootstrap=bootstrap(), criteria=criteria())
    return FrozenResearchProtocol(**(args | changes))


def evaluate(d, p=None):
    p = p or protocol()
    return evaluate_development_oos(d, partition_development(d, p.plan), p)


def mixed():
    return dataset(*(row(kind, 10+i, i+1) for i, kind in enumerate(
        ('WIN', 'LOSS', 'FLAT', 'REJECTED', 'NO_FILL', 'OPEN', 'INCOMPLETE'))))


def test_membership_metrics_states_folds_regimes_and_no_mutation():
    d = mixed()
    unknown = ResearchObservation(performance=observation('LOSS', at=at(21), trade_id=20,
                                                         regime=MarketRegimeType.UNKNOWN))
    d = dataset(*d.observations, unknown, row('WIN', 0, 100), row('WIN', 9, 101),
                row('OPEN', 2, 102), row('WIN', 40, 103))
    p = protocol(plan=plan((fold(), fold('f2', (0, 20), (20, 30)),
                           fold('empty', (0, 30), (30, 35)))))
    before = d.model_dump_json(), p.model_dump_json()
    result = evaluate(d, p)
    assert result == evaluate(dataset(*reversed(d.observations)), p)
    assert result.observation_ids == tuple(UUID(int=i) for i in (*range(1, 8), 20))
    assert result.performance == analyze([r.performance for r in d.observations[:8]])
    for f in result.folds:
        assert f.performance == analyze([r.performance for r in d.observations if r.observation_id in f.observation_ids])
    assert (result.fold_count, result.positive_folds, result.negative_folds, result.flat_folds) == (3, 1, 1, 0)
    assert result.undefined_fold_ids == ('empty',)
    s = result.performance.summary
    assert (s.total_observations, s.completed_count, s.rejected_count, s.no_fill_count,
            s.open_count, s.incomplete_count) == (8, 4, 1, 1, 1, 1)
    regimes = {r.market_regime: r.summary for r in result.performance.regimes}
    assert regimes[MarketRegimeType.UNKNOWN].total_net_pnl == -10
    assert regimes[MarketRegimeType.RISK_ON].total_observations == 7
    assert result.bootstrap.sample_count == 4
    assert before == (d.model_dump_json(), p.model_dump_json())


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'training', 'purged', 'unavailable',
                                    'plan', 'strategy', 'fold_schema', 'partition_schema', 'dict_fold'])
def test_fabricated_partition_rejected(change):
    d, p = mixed(), protocol()
    part = partition_development(d, p.plan)
    f = part.folds[0]
    if change in ('missing', 'duplicate', 'training'):
        ids = {'missing': (UUID(int=999),), 'duplicate': f.test_ids + f.test_ids,
               'training': ()}[change]
        part = part.model_copy(update={'folds': (f.model_copy(update={'test_ids': ids}),)})
    elif change in ('purged', 'unavailable'):
        part = part.model_copy(update={'folds': (f.model_copy(update={change + '_training_ids': (UUID(int=999),)}),)})
    elif change == 'plan':
        part = part.model_copy(update={'plan': plan(holdout=(41, 50))})
    elif change == 'strategy':
        part = part.model_copy(update={'strategy_version': 'other'})
    elif change == 'fold_schema':
        part = part.model_copy(update={'folds': (f.model_copy(update={'schema_version': 'bad'}),)})
    elif change == 'dict_fold':
        part = part.model_copy(update={'folds': (f.model_dump(),)})
    else:
        part = part.model_copy(update={'schema_version': 'bad'})
    with pytest.raises(ValueError):
        evaluate_development_oos(d, part, p)


@pytest.mark.parametrize('field', ['seed', 'replications', 'block_size'])
@pytest.mark.parametrize('value', [True, False, '2', 2.0, D(2), None])
def test_bootstrap_strict_integer(field, value):
    with pytest.raises(ValueError):
        bootstrap(**{field: value})


@pytest.mark.parametrize('field,value', [('replications', 0), ('replications', -1), ('block_size', 0),
    ('block_size', -1), ('confidence_level', D(0)), ('confidence_level', D(1)),
    ('confidence_level', D('NaN')), ('confidence_level', D('Infinity')),
    ('confidence_level', D('-Infinity')), ('confidence_level', .9), ('config_version', 'bad')])
def test_bootstrap_invalid(field, value):
    with pytest.raises(ValueError):
        bootstrap(**{field: value})


@pytest.mark.parametrize('field', list(BootstrapConfig.model_fields))
def test_bootstrap_all_settings_required(field):
    args = {n: getattr(bootstrap(), n) for n in BootstrapConfig.model_fields if n != field}
    with pytest.raises(ValueError):
        BootstrapConfig(**args)


@pytest.mark.parametrize('field', list(ResearchEvidenceCriteria.model_fields))
def test_criteria_explicit_even_optional_disabled_fields(field):
    args = {n: getattr(criteria(), n) for n in ResearchEvidenceCriteria.model_fields if n != field}
    with pytest.raises(ValueError):
        ResearchEvidenceCriteria(**args)


@pytest.mark.parametrize('field,value', [('minimum_completed_holdout_trades', True),
    ('minimum_completed_development_trades', 1), ('minimum_completed_holdout_trades', 0),
    ('minimum_net_expectancy', D('NaN')), ('maximum_drawdown_fraction', D('Infinity')),
    ('maximum_drawdown_fraction', D(-1)), ('minimum_profit_factor', D(-1)),
    ('minimum_net_expectancy_lower_bound', D('Infinity'))])
def test_criteria_invalid(field, value):
    with pytest.raises(ValueError):
        criteria(**{field: value})


def test_criterion_check_unavailable_reason_is_closed_set():
    from app.research import CriterionCheck
    args = dict(criterion='completed_trades', actual=None, threshold=D(0),
               comparison='GE', passed=None)
    CriterionCheck(**args, unavailable_reason='BELOW_DECLARED_SAMPLE_MINIMUM')
    CriterionCheck(**args, unavailable_reason='UNDEFINED_METRIC')
    CriterionCheck(**args, unavailable_reason=None)
    with pytest.raises(ValueError):
        CriterionCheck(**args, unavailable_reason='BELOW_DECLARED_SAMPLE_MINUMUM')


@pytest.mark.parametrize('block', [1, 2, 3])
def test_bootstrap_deterministic_off_ambient_random_and_decimal_context(block):
    d, p = mixed(), protocol(bootstrap=bootstrap(block_size=block))
    expected = evaluate(d, p)
    random.seed(987)
    state = random.getstate()
    with localcontext(Context(prec=6)):
        assert evaluate(d, p).model_dump_json() == expected.model_dump_json()
    assert random.getstate() == state
    random.seed(456)
    assert evaluate(d, p) == expected
    b = expected.bootstrap
    for ci in (b.net_expectancy, b.total_net_pnl, b.max_drawdown_amount):
        assert ci.lower <= ci.upper
        assert ci.valid_replications == p.bootstrap.replications
        assert ci.unavailable_reason is None
        assert all(v.is_finite() for v in (ci.estimate, ci.lower, ci.upper))
    assert b.net_expectancy.estimate == expected.performance.summary.net_expectancy
    assert b.max_drawdown_amount.estimate == expected.performance.drawdown.max_drawdown_amount


def test_exact_block_convention_and_quantile():
    values = tuple(map(D, range(5)))
    class Starts:
        def __init__(self):
            self.starts = iter((3, 1, 2))
        def randrange(self, stop):
            assert stop == 4
            return next(self.starts)
    assert _sample_blocks(values, 2, Starts()) == tuple(map(D, (3, 4, 1, 2, 2)))
    rng = Random(123)
    expected = tuple(values[rng.randrange(5)] for _ in values)
    assert _sample_blocks(values, 1, Random(123)) == expected
    assert _sample_blocks(values, 5, Random(123)) == values
    assert _quantile((D(0), D(10), D(20)), D('.25')) == 5
    assert _quantile((D(7),), D('.95')) == 7


def test_exact_bootstrap_primitive_drawdown_matches_m7():
    rows = [observation('WIN', trade_id=1), observation('LOSS', trade_id=2),
            observation('LOSS', trade_id=3)]
    expected = analyze(rows)
    with localcontext(Context(prec=34)):
        result = _statistics(tuple(r.paper_result.metrics.net_pnl for r in rows), D(100))
    assert result == (expected.summary.net_expectancy, expected.summary.total_net_pnl,
                      expected.drawdown.max_drawdown_amount)


@pytest.mark.parametrize('kinds,block,reason', [((), 1, 'ZERO_COMPLETED'),
    (('OPEN',), 1, 'ZERO_COMPLETED'), (('WIN',), 1, 'ONE_COMPLETED'),
    (('LOSS',), 3, 'ONE_COMPLETED'), (('WIN', 'LOSS'), 3, 'BLOCK_EXCEEDS_SAMPLE')])
def test_insufficient_intervals_and_evidence(kinds, block, reason):
    d = dataset(*(row(k, 10+i, i+1) for i, k in enumerate(kinds)))
    r = evaluate(d, protocol(bootstrap=bootstrap(block_size=block)))
    assert r.status == 'INSUFFICIENT_EVIDENCE'
    for ci in (r.bootstrap.net_expectancy, r.bootstrap.total_net_pnl, r.bootstrap.max_drawdown_amount):
        assert ci.lower is ci.upper is None
        assert ci.valid_replications == 0
        assert ci.unavailable_reason == reason
        if reason == 'ZERO_COMPLETED':
            assert ci.estimate is None


def test_exit_order_known_at_interval_start_and_identity():
    original = observation('WIN', at=at(10), trade_id=3)
    delayed = ResearchObservation(performance=revise(original, bars=(original.paper_input.bars[0],
        original.paper_input.bars[1].model_copy(update={'available_at': at(18)}))))
    rows = (row('LOSS', 12, 2), delayed, row('WIN', 12, 1))
    result = evaluate(dataset(*rows))
    assert result.bootstrap.completed_observation_ids == tuple(UUID(int=i) for i in (1, 2, 3))
    # Equal known_at: earlier interval_start must beat UUID ordering.
    other = observation('WIN', at=at(11), trade_id=4)
    other = ResearchObservation(performance=revise(other, bars=(other.paper_input.bars[0],
        other.paper_input.bars[1].model_copy(update={'available_at': at(18)}))))
    assert evaluate(dataset(other, delayed)).bootstrap.completed_observation_ids == (UUID(int=3), UUID(int=4))


@pytest.mark.parametrize('kinds,changes,status', [
    (('WIN', 'LOSS'), {}, 'MEETS_DECLARED_CRITERIA'),
    (('LOSS', 'LOSS'), {}, 'FAILS_DECLARED_CRITERIA'),
    (('FLAT', 'FLAT'), {}, 'MEETS_DECLARED_CRITERIA'),
    (('WIN', 'WIN'), {'minimum_profit_factor': D(1)}, 'INSUFFICIENT_EVIDENCE'),
    (('WIN', 'LOSS'), {'minimum_profit_factor': D(3)}, 'FAILS_DECLARED_CRITERIA'),
    (('WIN', 'LOSS'), {'maximum_drawdown_fraction': D('.01')}, 'FAILS_DECLARED_CRITERIA'),
    (('WIN', 'LOSS'), {'minimum_net_expectancy_lower_bound': D(0)}, 'FAILS_DECLARED_CRITERIA'),
    (('WIN', 'LOSS'), {'minimum_net_expectancy_lower_bound': D(-100)}, 'MEETS_DECLARED_CRITERIA'),
    (('LOSS', 'LOSS'), {'minimum_completed_development_trades': 3}, 'INSUFFICIENT_EVIDENCE'),
])
def test_declared_status(kinds, changes, status):
    d = dataset(*(row(k, 10+i, i+1) for i, k in enumerate(kinds)))
    assert evaluate(d, protocol(criteria=criteria(**changes))).status == status


def test_separate_holdout_half_open_states_and_isolation(monkeypatch):
    p = protocol()
    dev = (row('WIN', 10, 1), row('LOSS', 11, 2))
    future = tuple(row(k, m, i+10) for i, (k, m) in enumerate(
        (('WIN', 39), ('WIN', 40), ('LOSS', 41), ('REJECTED', 42),
         ('NO_FILL', 43), ('OPEN', 44), ('INCOMPLETE', 49), ('WIN', 50))))
    d = dataset(*dev, *future)
    part = partition_development(d, p.plan)
    development = evaluate_development_oos(d, part, p)
    before = (p.model_dump_json(), part.model_dump_json(), development.model_dump_json())
    h = evaluate_frozen_holdout(d, p)
    assert h.observation_ids == tuple(UUID(int=i) for i in range(11, 17))
    assert h.performance == analyze([r.performance for r in future[1:-1]])
    assert h == evaluate_frozen_holdout(dataset(row('LOSS', 10, 1), *future), p)
    changed = dataset(*dev, *(row('LOSS', m, i+10) for i, m in enumerate((39, 40, 41, 42, 43, 44, 49, 50))))
    assert evaluate(changed, p) == development
    assert evaluate_frozen_holdout(changed, p) != h
    assert before == (p.model_dump_json(), part.model_dump_json(), development.model_dump_json())
    def forbidden(*a, **k):
        raise AssertionError('holdout API must not run implicitly')
    monkeypatch.setattr('app.research.evaluation.evaluate_frozen_holdout', forbidden)
    assert evaluate(d, p) == development
    assert build_research_evidence(d, part, p, development, None).status == 'INSUFFICIENT_EVIDENCE'


@pytest.mark.parametrize('kind,status', [('WIN', 'MEETS_DECLARED_CRITERIA'),
    ('LOSS', 'FAILS_DECLARED_CRITERIA'), ('OPEN', 'INSUFFICIENT_EVIDENCE')])
def test_final_report_and_missing_evidence(kind, status):
    d = dataset(row('WIN', 10, 1), row('LOSS', 11, 2), row(kind, 40, 3), row(kind, 41, 4))
    p = protocol()
    part = partition_development(d, p.plan)
    dev, hold = evaluate(d, p), evaluate_frozen_holdout(d, p)
    r = build_research_evidence(d, part, p, dev, hold)
    assert r.status == status
    assert r.protocol == p and r.development == dev and r.holdout == hold
    assert r.limitations
    for a, b in ((dev, None), (None, hold), (None, None)):
        assert build_research_evidence(d, part, p, a, b).status == 'INSUFFICIENT_EVIDENCE'
    with pytest.raises(ValueError):
        r.status = 'MEETS_DECLARED_CRITERIA'


@pytest.mark.parametrize('corruption', ['protocol_schema', 'strategy', 'plan', 'bootstrap', 'criteria',
                                      'performance_config', 'dataset_schema', 'duplicate', 'result', 'dict'])
@pytest.mark.parametrize('holdout', [False, True])
def test_public_boundaries_revalidate_corruption(corruption, holdout):
    d, p = mixed(), protocol()
    part = partition_development(d, p.plan)
    if corruption == 'protocol_schema':
        p = p.model_copy(update={'schema_version': 'bad'})
    elif corruption == 'strategy':
        p = p.model_copy(update={'strategy_version': '2'})
    elif corruption == 'plan':
        p = p.model_copy(update={'plan': p.plan.model_copy(update={'holdout': p.plan.holdout.model_copy(
            update={'schema_version': 'bad'})})})
    elif corruption == 'bootstrap':
        p = p.model_copy(update={'bootstrap': bootstrap().model_copy(update={'seed': True})})
    elif corruption == 'criteria':
        p = p.model_copy(update={'criteria': criteria().model_copy(update={'minimum_net_expectancy': D('NaN')})})
    elif corruption == 'performance_config':
        p = p.model_copy(update={'performance_config': p.performance_config.model_copy(update={'starting_equity': D(0)})})
    elif corruption == 'dataset_schema':
        d = d.model_copy(update={'schema_version': 'bad'})
    elif corruption == 'duplicate':
        d = d.model_copy(update={'observations': d.observations + d.observations[:1]})
    elif corruption == 'dict':
        p = p.model_dump()
    else:
        r = d.observations[0]
        perf = r.performance
        result = perf.paper_result.model_copy(update={'metrics': perf.paper_result.metrics.model_copy(update={'net_pnl': D(999)})})
        bad = r.model_copy(update={'performance': perf.model_copy(update={'paper_result': result})})
        d = d.model_copy(update={'observations': (bad,)})
    with pytest.raises(ValueError):
        if holdout:
            evaluate_frozen_holdout(d, p)
        else:
            evaluate_development_oos(d, part, p)


def test_final_rejects_copied_reports_changed_protocol_and_outcomes():
    d = dataset(row('WIN', 10, 1), row('LOSS', 11, 2), row('WIN', 40, 3), row('LOSS', 41, 4))
    p = protocol()
    part = partition_development(d, p.plan)
    dev, hold = evaluate(d, p), evaluate_frozen_holdout(d, p)
    for a, b in ((dev.model_copy(update={'positive_folds': 99}), hold),
                 (dev, hold.model_copy(update={'schema_version': 'bad'})),
                 (dev, hold.model_copy(update={'observation_ids': ()})),
                 (dev.model_dump(), hold)):
        with pytest.raises(ValueError):
            build_research_evidence(d, part, p, a, b)
    with pytest.raises(ValueError):
        build_research_evidence(d, part, protocol(bootstrap=bootstrap(seed=4)), dev, hold)
    changed = dataset(*d.observations[:2], row('LOSS', 40, 3), row('LOSS', 41, 4))
    with pytest.raises(ValueError):
        build_research_evidence(changed, part, p, dev, hold)


def test_no_network_and_replay_cost_independent_of_replications(monkeypatch):
    import app.paper.simulator as simulator
    calls = []
    original = simulator.simulate_paper
    def replay(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    def forbidden(*a, **k):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    d, p = mixed(), protocol()
    monkeypatch.setattr(simulator, 'simulate_paper', replay)
    evaluate(d, p)
    count = len(calls)
    calls.clear()
    p2 = protocol(bootstrap=bootstrap(replications=800))
    evaluate(d, p2)
    assert len(calls) == count
    hold = evaluate_frozen_holdout(d, p)
    assert hold.status == 'INSUFFICIENT_EVIDENCE'


def test_late_labels_costs_and_regime_do_not_remove_admitted_evidence():
    original = observation('WIN', at=at(19), trade_id=1, regime=MarketRegimeType.RISK_OFF)
    late = ResearchObservation(performance=revise(original,
        config=original.paper_input.config.model_copy(update={'fixed_cost_per_side': D(11)}),
        bars=(original.paper_input.bars[0], original.paper_input.bars[1].model_copy(
            update={'available_at': at(60)}))))
    p = protocol()
    dev = evaluate(dataset(late, row('WIN', 10, 2)), p)
    assert late.label_available_at == at(60)
    assert late.economic_label == 'LOSS'
    assert late.observation_id in dev.observation_ids
    assert dev.performance.summary.total_costs == 22
    assert dev.performance == analyze([row('WIN', 10, 2).performance, late.performance])
    original = observation('LOSS', at=at(49), trade_id=3, regime=MarketRegimeType.UNKNOWN)
    future = ResearchObservation(performance=revise(original, bars=(original.paper_input.bars[0],
        original.paper_input.bars[1].model_copy(update={'available_at': at(70)}))))
    hold = evaluate_frozen_holdout(dataset(future), p)
    assert hold.observation_ids == (future.observation_id,)
    assert hold.performance.summary.completed_count == 1
    assert hold.performance.regimes[0].market_regime == MarketRegimeType.UNKNOWN


def test_flat_fold_is_distinct_from_undefined_and_exact_thresholds_pass():
    d = dataset(row('FLAT', 10, 1), row('FLAT', 11, 2))
    r = evaluate(d, protocol(criteria=criteria(maximum_drawdown_fraction=D(0))))
    assert r.flat_folds == 1 and r.undefined_fold_ids == ()
    assert r.status == 'MEETS_DECLARED_CRITERIA'
    for check in r.checks:
        assert check.actual == check.threshold and check.passed is True


def test_final_nested_corruption_and_bootstrap_insufficiency():
    d = dataset(row('WIN', 10, 1), row('LOSS', 11, 2), row('WIN', 40, 3), row('LOSS', 41, 4))
    p = protocol(bootstrap=bootstrap(block_size=3))
    part = partition_development(d, p.plan)
    dev, hold = evaluate(d, p), evaluate_frozen_holdout(d, p)
    assert hold.status == 'MEETS_DECLARED_CRITERIA'
    assert build_research_evidence(d, part, p, dev, hold).status == 'INSUFFICIENT_EVIDENCE'
    bad = hold.model_copy(update={'performance': hold.performance.model_copy(update={
        'summary': hold.performance.summary.model_copy(update={'win_count': True})})})
    with pytest.raises(ValueError):
        build_research_evidence(d, part, p, dev, bad)


def test_model_construct_corruption_and_valid_plan_change_rejected():
    d, p = mixed(), protocol()
    part = partition_development(d, p.plan)
    bad_config = BootstrapConfig.model_construct(config_version='research-bootstrap-v1', seed=1,
        replications=0, confidence_level=D('.9'), block_size=1)
    with pytest.raises(ValueError):
        evaluate_development_oos(d, part, p.model_copy(update={'bootstrap': bad_config}))
    with pytest.raises(ValueError):
        evaluate_development_oos(d, part, protocol(plan=plan(holdout=(41, 51))))


def test_bootstrap_bounds_exact_against_independent_iid_replicates():
    d = dataset(row('WIN', 10, 1), row('LOSS', 11, 2))
    p = protocol(bootstrap=bootstrap(replications=4, confidence_level=D('.5')))
    result = evaluate(d, p).bootstrap
    rng = Random(p.bootstrap.seed)
    totals = sorted(sum((D(20), D(-10))[rng.randrange(2)] for _ in range(2)) for _ in range(4))
    # B=4: p=.25/.75 correspond to indices .75 and 2.25.
    lower = totals[0] + D('.75') * (totals[1] - totals[0])
    upper = totals[2] + D('.25') * (totals[3] - totals[2])
    assert (result.total_net_pnl.lower, result.total_net_pnl.upper) == (lower, upper)
    assert (result.net_expectancy.lower, result.net_expectancy.upper) == (lower / 2, upper / 2)
