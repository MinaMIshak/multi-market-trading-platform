"""Offline contract fixtures only; these are not market/research evidence."""
from datetime import timedelta, timezone
from decimal import Decimal
from uuid import UUID
import socket

import pytest

from app.research import (
    FrozenHoldout, ResearchDataset, ResearchInterval, ResearchObservation,
    WalkForwardFold, WalkForwardPlan, partition_development,
)
from test_m7_performance import AT, observation
from test_m7_extensions import revise


def at(minutes):
    return AT + timedelta(minutes=minutes)


def interval(start, end):
    return ResearchInterval(start=at(start), end=at(end))


def fold(name='f1', train=(0, 10), test=(10, 20)):
    return WalkForwardFold(fold_id=name, train=interval(*train), test=interval(*test))


def plan(folds=None, holdout=(40, 50)):
    return WalkForwardPlan(plan_id='fixed-development', folds=folds or (fold(),),
                           holdout=FrozenHoldout(holdout_id='future-fixed', interval=interval(*holdout)))


def row(kind='WIN', minute=0, trade_id=1):
    return ResearchObservation(performance=observation(kind, at=at(minute), trade_id=trade_id))


def dataset(*rows):
    return ResearchDataset(strategy_id='fixture-strategy', strategy_version='1', observations=rows)


def test_happy_path_deterministic_auditable_and_no_mutation():
    rows = (row(minute=0), row('LOSS', 9, 2), row('OPEN', 4, 3), row('INCOMPLETE', 10, 4))
    source = tuple(r.performance.model_dump() for r in rows)
    p = plan((fold(), fold('f2', (5, 20), (20, 30))))
    result = partition_development(dataset(*rows), p)
    assert result == partition_development(dataset(*reversed(rows)), p)
    f = result.folds[0]
    assert f.fold == p.folds[0]
    assert f.training_ids == (UUID(int=1),)
    assert f.purged_training_ids == (UUID(int=2),)
    assert f.unavailable_training_ids == (UUID(int=3),)
    assert f.test_ids == (UUID(int=4),)
    assert (f.training_count, f.purged_training_count, f.unavailable_training_count, f.test_count) == (1, 1, 1, 1)
    assert result.plan.holdout == p.holdout
    assert tuple(r.performance.model_dump() for r in rows) == source
    with pytest.raises(ValueError):
        result.strategy_id = 'changed'


@pytest.mark.parametrize('kind,label', [('WIN', 'WIN'), ('LOSS', 'LOSS'), ('FLAT', 'BREAKEVEN'),
                                        ('TIME_WIN', 'WIN'), ('TIME_LOSS', 'LOSS')])
def test_executable_economic_labels(kind, label):
    r = row(kind)
    assert r.economic_label == label
    assert r.decision_at == r.performance.paper_input.admission_time
    assert r.label_available_at == r.performance.paper_result.position.exit.known_at == at(2)


def test_costs_and_slippage_are_inherited_not_gross_labels():
    original = observation('WIN')
    cfg = original.paper_input.config.model_copy(update={
        'fixed_cost_per_side': Decimal(11), 'target_slippage_bps': Decimal(100)})
    canonical = revise(original, config=cfg)
    r = ResearchObservation(performance=canonical)
    assert canonical.paper_result.outcome == 'WIN'
    assert canonical.paper_result.metrics.gross_pnl > 0
    assert r.economic_label == 'LOSS'
    assert r.performance == canonical
    assert r.performance.paper_input.config == cfg


@pytest.mark.parametrize('kind', ['REJECTED', 'NO_FILL', 'OPEN', 'INCOMPLETE'])
def test_noncompleted_never_becomes_training_label_but_test_admission_is_preserved(kind):
    r = row(kind)
    assert r.economic_label is r.label_available_at is None
    result = partition_development(dataset(r, row(kind, 10, 2)), plan()).folds[0]
    assert result.training_ids == result.purged_training_ids == ()
    assert result.unavailable_training_ids == (UUID(int=1),)
    assert result.test_ids == (UUID(int=2),)


def test_half_open_boundaries_and_strict_information_cutoff():
    rows = tuple(row(minute=m, trade_id=i+1) for i, m in enumerate((-1, 0, 8, 9, 10, 19, 20)))
    f = partition_development(dataset(*rows), plan()).folds[0]
    assert f.training_ids == (UUID(int=2),)
    assert f.purged_training_ids == (UUID(int=3), UUID(int=4))  # known exactly at / after cutoff
    assert f.test_ids == (UUID(int=5), UUID(int=6))


def test_delayed_exit_availability_not_interval_end_or_last_bar():
    original = observation('WIN')
    bars = original.paper_input.bars
    delayed = revise(original, bars=(bars[0], bars[1].model_copy(update={'available_at': at(11)})))
    r = ResearchObservation(performance=delayed)
    assert r.label_available_at == at(11)
    assert r.performance.paper_result.position.exit.interval_end == at(2)
    assert partition_development(dataset(r), plan()).folds[0].purged_training_ids == (r.observation_id,)


def test_plan_creation_is_not_signal_creation_time():
    original = observation('WIN')
    p = original.paper_input.trade_plan.model_copy(update={'created_at': at(-5)})
    r = ResearchObservation(performance=revise(original, trade_plan=p))
    assert r.decision_at == AT
    assert partition_development(dataset(r), plan()).folds[0].training_count == 1


def test_empty_dataset_and_empty_fold_sets():
    result = partition_development(dataset(), plan())
    f = result.folds[0]
    assert (f.training_count, f.purged_training_count, f.unavailable_training_count, f.test_count) == (0, 0, 0, 0)
    assert f.training_ids == f.test_ids == ()
    assert result.strategy_version == '1'


def test_all_candidate_labels_purged():
    f = partition_development(dataset(row(minute=9)), plan()).folds[0]
    assert f.training_count == f.test_count == 0
    assert f.purged_training_count == 1


@pytest.mark.parametrize('kind', ['WIN', 'LOSS', 'FLAT', 'REJECTED', 'NO_FILL', 'OPEN', 'INCOMPLETE'])
def test_holdout_replacement_addition_removal_does_not_change_development(kind):
    dev = row()
    expected = partition_development(dataset(dev), plan())
    future = row(kind, 40, 200)
    assert partition_development(dataset(future, dev), plan()) == expected
    assert partition_development(dataset(dev, row(kind, 49, 201)), plan()) == expected
    assert '00000000-0000-0000-0000-0000000000c8' not in expected.model_dump_json()


def test_test_labels_do_not_change_any_membership():
    before = partition_development(dataset(row(), row('WIN', 10, 2)), plan())
    after = partition_development(dataset(row(), row('INCOMPLETE', 10, 2)), plan())
    assert before == after


@pytest.mark.parametrize('start,end', [(0, 0), (1, 0)])
def test_bad_intervals(start, end):
    with pytest.raises(ValueError, match='positive interval'):
        interval(start, end)


@pytest.mark.parametrize('field', ['start', 'end'])
@pytest.mark.parametrize('zone', [None, timezone(timedelta(hours=3))])
def test_naive_and_non_utc_intervals(field, zone):
    values = dict(start=AT, end=at(10))
    values[field] = values[field].replace(tzinfo=zone)
    with pytest.raises(ValueError, match='UTC'):
        ResearchInterval(**values)


def test_non_utc_canonical_observation_rejected_without_inventing_time():
    original = observation('WIN', at=AT.astimezone(timezone(timedelta(hours=3))))
    with pytest.raises(ValueError, match='UTC'):
        ResearchObservation(performance=original)


@pytest.mark.parametrize('train,test', [((0, 11), (10, 20)), ((20, 30), (10, 20))])
def test_train_test_chronology(train, test):
    with pytest.raises(ValueError, match='precede'):
        fold(train=train, test=test)


@pytest.mark.parametrize('folds,match', [
    ((fold(), fold(test=(20, 30))), 'duplicate'),
    ((fold(), fold('f2', test=(19, 30))), 'nonoverlapping'),
    ((fold('f2', test=(20, 30)), fold()), 'nonoverlapping'),
    ((fold(), fold('f2', (-1, 15), (20, 30))), 'roll forward'),
    ((fold(), fold('f2', (0, 9), (20, 30))), 'roll forward'),
])
def test_fold_order_and_overlap_not_repaired(folds, match):
    with pytest.raises(ValueError, match=match):
        plan(folds)


@pytest.mark.parametrize('holdout', [(15, 25), (20, 30), (-10, -1), (0, 50)])
def test_holdout_must_be_strictly_after_development(holdout):
    with pytest.raises(ValueError, match='strictly after'):
        plan(holdout=holdout)


def test_overlapping_rolling_training_and_adjacent_tests_allowed():
    p = plan((fold(), fold('f2', (5, 20), (20, 30))))
    assert len(partition_development(dataset(), p).folds) == 2


def test_duplicate_observation_identity_rejected_even_in_holdout():
    r = row(minute=40)
    with pytest.raises(ValueError, match='duplicate observation'):
        dataset(r, r)


@pytest.mark.parametrize('field', ['strategy_id', 'strategy_version'])
def test_mixed_strategy_rejected(field):
    other = ResearchObservation(performance=observation('WIN', trade_id=2).model_copy(update={field: 'other'}))
    with pytest.raises(ValueError, match='mixed strategy'):
        dataset(row(), other)


@pytest.mark.parametrize('path', ['performance', 'paper_input', 'paper_result', 'position', 'entry', 'exit', 'metrics'])
def test_nested_dictionary_substitution_rejected(path):
    r = row()
    p = r.performance
    result = p.paper_result
    if path == 'performance':
        bad = r.model_copy(update={'performance': p.model_dump()})
    elif path in ('paper_input', 'paper_result'):
        bad = r.model_copy(update={'performance': p.model_copy(update={path: getattr(p, path).model_dump()})})
    else:
        if path in ('entry', 'exit'):
            position = result.position.model_copy(update={path: getattr(result.position, path).model_dump()})
            result = result.model_copy(update={'position': position})
        else:
            result = result.model_copy(update={path: getattr(result, path).model_dump()})
        bad = r.model_copy(update={'performance': p.model_copy(update={'paper_result': result})})
    with pytest.raises(ValueError, match='canonical'):
        partition_development(dataset().model_copy(update={'observations': (bad,)}), plan())


@pytest.mark.parametrize('corruption', ['label', 'bar', 'metrics', 'fill', 'result', 'schema', 'interval', 'fold', 'holdout', 'plan', 'dataset'])
def test_model_copy_corruption_revalidated(corruption):
    r, p = row(), plan()
    d = dataset(r)
    if corruption == 'label':
        bad = r.model_copy(update={'economic_label': 'LOSS'})
        # Derived properties cannot be overridden by model_copy extras.
        assert partition_development(d.model_copy(update={'observations': (bad,)}), p) == partition_development(d, p)
        return
    if corruption in ('bar', 'metrics', 'fill', 'result'):
        perf = r.performance
        if corruption == 'bar':
            inp = perf.paper_input
            inp = inp.model_copy(update={'bars': (inp.bars[0].model_copy(update={'high': float('nan')}), *inp.bars[1:])})
            perf = perf.model_copy(update={'paper_input': inp})
        else:
            result = perf.paper_result
            if corruption == 'metrics':
                result = result.model_copy(update={'metrics': result.metrics.model_copy(update={'net_pnl': Decimal(999)})})
            elif corruption == 'fill':
                pos = result.position
                result = result.model_copy(update={'position': pos.model_copy(update={'exit': pos.exit.model_copy(update={'known_at': AT})})})
            else:
                result = result.model_copy(update={'state': 'OPEN'})
            perf = perf.model_copy(update={'paper_result': result})
        d = d.model_copy(update={'observations': (r.model_copy(update={'performance': perf}),)})
    elif corruption == 'schema':
        d = d.model_copy(update={'observations': (r.model_copy(update={'schema_version': 'bad'}),)})
    elif corruption == 'interval':
        f = p.folds[0].model_copy(update={'train': interval(0, 10).model_copy(update={'end': at(-1)})})
        p = p.model_copy(update={'folds': (f,)})
    elif corruption == 'fold':
        p = p.model_copy(update={'folds': (p.folds[0].model_copy(update={'schema_version': 'bad'}),)})
    elif corruption == 'holdout':
        p = p.model_copy(update={'holdout': p.holdout.model_copy(update={'interval': interval(5, 6)})})
    elif corruption == 'plan':
        p = p.model_copy(update={'schema_version': 'bad'})
    else:
        d = d.model_copy(update={'strategy_version': 'other'})
    with pytest.raises(ValueError):
        partition_development(d, p)


def test_no_network_usage(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    assert partition_development(dataset(row()), plan()).folds[0].training_count == 1


def test_source_domain_mutation_does_not_change_constructed_observation():
    original = observation('WIN')
    wrapped = ResearchObservation(performance=original)
    original.paper_input.trade_plan.symbol = 'OTHER'
    assert wrapped.performance.paper_input.trade_plan.symbol == 'SWDY'


def test_empty_plan_and_noncanonical_public_arguments_rejected():
    p = plan()
    with pytest.raises(ValueError):
        partition_development(dataset(), p.model_copy(update={'folds': ()}))
    with pytest.raises(ValueError):
        partition_development({}, p)
    with pytest.raises(ValueError):
        partition_development(dataset(), p.model_dump())


def test_explicit_gap_uses_test_information_boundary():
    r = row(minute=9)  # decision before train end; label known at minute 11
    f = partition_development(dataset(r), plan((fold(test=(12, 20)),))).folds[0]
    assert f.training_ids == (r.observation_id,)
    assert f.purged_training_ids == ()


def test_regime_metadata_never_filters_membership():
    from app.domain.enums import MarketRegimeType
    original = row()
    changed = ResearchObservation(performance=original.performance.model_copy(
        update={'market_regime': MarketRegimeType.RISK_OFF}))
    assert changed.performance.market_regime == MarketRegimeType.RISK_OFF
    assert partition_development(dataset(original), plan()) == partition_development(dataset(changed), plan())


def test_later_fold_can_use_previously_tested_label_only_when_known():
    r = row(minute=19)  # test in f1, unavailable until minute 21
    p = plan((fold(), fold('f2', (5, 20), (20, 30))))
    result = partition_development(dataset(r), p)
    assert result.folds[0].test_ids == (r.observation_id,)
    assert result.folds[1].training_ids == ()
    assert result.folds[1].purged_training_ids == (r.observation_id,)
