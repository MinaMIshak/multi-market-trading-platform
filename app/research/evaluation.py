"""Pure offline M8B evaluation. M8A owns membership; M7 owns ordinary economics."""
from decimal import Context, Decimal, localcontext
from random import Random

from pydantic import BaseModel

from app.performance.analyzer import analyze_performance, _exit_key
from app.performance.models import PerformanceAnalysisInput
from .models import (
    DevelopmentPartition, FoldPartition, ResearchDataset, WalkForwardFold, _canonical,
)
from .splits import partition_development
from .evidence import (
    BootstrapReport, ConfidenceInterval, CriterionCheck, DevelopmentOOSReport,
    FoldOOSReport, FrozenResearchProtocol, HoldoutEvidenceReport, ResearchEvidenceReport,
)

D = Decimal


def _boundary(dataset, protocol):
    protocol = _canonical(FrozenResearchProtocol, protocol)
    dataset = _canonical(ResearchDataset, dataset)
    if (dataset.strategy_id, dataset.strategy_version) != (
            protocol.strategy_id, protocol.strategy_version):
        raise ValueError('protocol strategy identity/version mismatch')
    return dataset, protocol


def _partition(dataset, protocol, supplied):
    supplied = _canonical(DevelopmentPartition, supplied)
    # M8A output models deliberately have no recursive input validators.
    _canonical(type(protocol.plan), supplied.plan)
    for fold in supplied.folds:
        _canonical(FoldPartition, fold)
        _canonical(WalkForwardFold, fold.fold)
    expected = partition_development(dataset, protocol.plan)
    if supplied != expected:
        raise ValueError('development partition differs from exact M8A recomputation')
    return expected


def _performance(rows, protocol):
    return analyze_performance(PerformanceAnalysisInput(
        schema_version='performance-analysis-v1', config=protocol.performance_config,
        observations=tuple(row.performance for row in rows),
    ))


def _sample_blocks(values, block_size, rng):
    """Nonwrapping uniformly chosen valid starts, final block truncated to n."""
    sampled = []
    while len(sampled) < len(values):
        start = rng.randrange(len(values) - block_size + 1)
        sampled.extend(values[start:start + block_size])
    return tuple(sampled[:len(values)])


def _statistics(values, starting_equity):
    # Primitive equivalent of M7 realized drawdown; repeated IDs are resamples,
    # not admissible canonical M7 observations. Never replay M6 here.
    equity = peak = starting_equity
    drawdown = D(0)
    for net in values:
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    total = sum(values, D(0))
    return total / D(len(values)), total, drawdown


def _quantile(values, probability):
    """Linear interpolation at (B-1)*p in sorted empirical replicates."""
    ordered = sorted(values)
    index = D(len(ordered) - 1) * probability
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _bootstrap(rows, protocol, performance):
    completed = sorted((row.performance for row in rows
                        if row.performance.paper_result.state == 'COMPLETED'), key=_exit_key)
    values = tuple(row.paper_result.metrics.net_pnl for row in completed)
    ids = tuple(row.paper_input.trade_plan.trade_plan_id for row in completed)
    n, config = len(values), protocol.bootstrap
    reason = ('ZERO_COMPLETED' if n == 0 else 'ONE_COMPLETED' if n == 1 else
              'BLOCK_EXCEEDS_SAMPLE' if config.block_size > n else None)
    estimates = (performance.summary.net_expectancy,
                 performance.summary.total_net_pnl if n else None,
                 performance.drawdown.max_drawdown_amount if n else None)
    metrics = ([], [], [])
    with localcontext(Context(prec=34)):
        if reason is None:
            rng = Random(config.seed)
            for _ in range(config.replications):
                result = _statistics(_sample_blocks(values, config.block_size, rng),
                                     protocol.performance_config.starting_equity)
                for bucket, value in zip(metrics, result):
                    bucket.append(value)
        tail = (D(1) - config.confidence_level) / 2
        intervals = tuple(ConfidenceInterval(
            estimate=estimate, lower=_quantile(bucket, tail) if bucket else None,
            upper=_quantile(bucket, 1 - tail) if bucket else None,
            valid_replications=len(bucket), unavailable_reason=reason,
        ) for estimate, bucket in zip(estimates, metrics))
    return BootstrapReport(config=config, completed_observation_ids=ids, sample_count=n,
                           net_expectancy=intervals[0], total_net_pnl=intervals[1],
                           max_drawdown_amount=intervals[2])


def _status(checks):
    # Missing evidence dominates a failure: every required check must be evaluable.
    if any(check.passed is None for check in checks):
        return 'INSUFFICIENT_EVIDENCE'
    if any(not check.passed for check in checks):
        return 'FAILS_DECLARED_CRITERIA'
    return 'MEETS_DECLARED_CRITERIA'


def _checks(performance, protocol, *, holdout, bootstrap=None):
    criteria = protocol.criteria
    s = performance.summary
    minimum = (criteria.minimum_completed_holdout_trades if holdout else
               criteria.minimum_completed_development_trades)
    values = [('completed_trades', D(s.completed_count), D(minimum), 'GE'),
              ('net_expectancy', s.net_expectancy, criteria.minimum_net_expectancy, 'GE'),
              ('drawdown_fraction', performance.drawdown.max_drawdown_pct if s.completed_count else None,
               criteria.maximum_drawdown_fraction, 'LE')]
    if criteria.minimum_profit_factor is not None:
        values.append(('profit_factor', s.profit_factor, criteria.minimum_profit_factor, 'GE'))
    # This optional bound is explicitly DEVELOPMENT-only: bootstrap never samples holdout.
    if not holdout and criteria.minimum_net_expectancy_lower_bound is not None:
        values.append(('net_expectancy_lower_bound', bootstrap.net_expectancy.lower,
                       criteria.minimum_net_expectancy_lower_bound, 'GE'))
    checks = []
    for name, actual, threshold, comparison in values:
        reason = ('BELOW_DECLARED_SAMPLE_MINIMUM' if s.completed_count < minimum else
                  'UNDEFINED_METRIC' if actual is None else None)
        checks.append(CriterionCheck(
            criterion=name, actual=actual, threshold=threshold, comparison=comparison,
            passed=None if reason else (actual >= threshold if comparison == 'GE' else actual <= threshold),
            unavailable_reason=reason,
        ))
    return tuple(checks)


def evaluate_development_oos(dataset: ResearchDataset, partition: DevelopmentPartition,
                             protocol: FrozenResearchProtocol) -> DevelopmentOOSReport:
    dataset, protocol = _boundary(dataset, protocol)
    partition = _partition(dataset, protocol, partition)
    by_id = {row.observation_id: row for row in dataset.observations}
    ids = tuple(identity for fold in partition.folds for identity in fold.test_ids)
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate OOS observation identity')
    if any(identity not in by_id for identity in ids):
        raise ValueError('missing OOS observation identity')
    rows = tuple(by_id[identity] for identity in ids)
    folds = tuple(FoldOOSReport(
        fold_id=fold.fold.fold_id, observation_ids=fold.test_ids,
        performance=_performance(tuple(by_id[i] for i in fold.test_ids), protocol),
    ) for fold in partition.folds)
    performance = _performance(rows, protocol)
    bootstrap = _bootstrap(rows, protocol, performance)
    checks = _checks(performance, protocol, holdout=False, bootstrap=bootstrap)
    defined = tuple(f for f in folds if f.performance.summary.completed_count)
    return DevelopmentOOSReport(
        protocol=protocol, partition=partition, folds=folds, fold_count=len(folds),
        observation_ids=ids, performance=performance, bootstrap=bootstrap, checks=checks,
        positive_folds=sum(f.performance.summary.total_net_pnl > 0 for f in defined),
        negative_folds=sum(f.performance.summary.total_net_pnl < 0 for f in defined),
        flat_folds=sum(f.performance.summary.total_net_pnl == 0 for f in defined),
        undefined_fold_ids=tuple(f.fold_id for f in folds if not f.performance.summary.completed_count),
        status=('INSUFFICIENT_EVIDENCE' if bootstrap.net_expectancy.lower is None else _status(checks)),
    )


def evaluate_frozen_holdout(dataset: ResearchDataset,
                            protocol: FrozenResearchProtocol) -> HoldoutEvidenceReport:
    dataset, protocol = _boundary(dataset, protocol)
    interval = protocol.plan.holdout.interval
    rows = tuple(sorted((row for row in dataset.observations
                         if interval.start <= row.decision_at < interval.end),
                        key=lambda row: (row.decision_at, str(row.observation_id))))
    performance = _performance(rows, protocol)
    checks = _checks(performance, protocol, holdout=True)
    return HoldoutEvidenceReport(protocol=protocol,
                                 observation_ids=tuple(row.observation_id for row in rows),
                                 performance=performance, checks=checks, status=_status(checks))


def _revalidate_report(value):
    """Reconstruct report descendants too; copied bool/int substitutions must fail."""
    if isinstance(value, BaseModel):
        return type(value)(**{name: _revalidate_report(getattr(value, name))
                              for name in type(value).model_fields})
    if isinstance(value, tuple):
        return tuple(_revalidate_report(item) for item in value)
    return value


def build_research_evidence(dataset: ResearchDataset, partition: DevelopmentPartition,
                            protocol: FrozenResearchProtocol,
                            development: DevelopmentOOSReport | None,
                            holdout: HoldoutEvidenceReport | None) -> ResearchEvidenceReport:
    """Audit supplied evidence by exact recomputation; missing reports stay missing.

    Holdout evaluation must have been explicitly requested separately. This final
    audit repeats it only when a holdout report is supplied, never to fill a gap.
    """
    dataset, protocol = _boundary(dataset, protocol)
    partition = _partition(dataset, protocol, partition)
    if development is not None:
        if type(development) is not DevelopmentOOSReport:
            raise ValueError('canonical DevelopmentOOSReport required')
        development = _revalidate_report(development)
        expected = evaluate_development_oos(dataset, partition, protocol)
        if development != expected:
            raise ValueError('development evidence mismatch')
        development = expected
    if holdout is not None:
        if type(holdout) is not HoldoutEvidenceReport:
            raise ValueError('canonical HoldoutEvidenceReport required')
        holdout = _revalidate_report(holdout)
        expected = evaluate_frozen_holdout(dataset, protocol)
        if holdout != expected:
            raise ValueError('holdout evidence mismatch')
        holdout = expected
    status = ('INSUFFICIENT_EVIDENCE' if development is None or holdout is None
              or 'INSUFFICIENT_EVIDENCE' in (development.status, holdout.status) else
              _status(development.checks + holdout.checks))
    return ResearchEvidenceReport(protocol=protocol, development=development,
                                  holdout=holdout, status=status)
