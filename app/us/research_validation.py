"""US8 pure point-in-time replay research selection and partitioning."""

from __future__ import annotations

from decimal import Context, Decimal, localcontext
from random import Random

from app.performance.models import PerformanceConfig
from app.performance.replay import analyze_replay_performance
from app.performance.replay_models import (
    ReplayPerformanceAnalysisInput,
    ReplaySlippageScenario,
)
from app.research.evidence import (
    BootstrapConfig,
    BootstrapReport,
    ConfidenceInterval,
    CriterionCheck,
    ResearchEvidenceCriteria,
)
from app.research.models import WalkForwardPlan
from app.us.paper_replay import build_us_replay_performance_observation
from app.us.paper_replay_models import canonical, exact_equal, exact_utc

from .research_validation_models import (
    USReplayDevelopmentEvidenceReport,
    USReplayDevelopmentScenarioReport,
    USReplayDevelopmentValidationReport,
    USReplayScenarioCoverage,
    USReplayDevelopmentOOSReport,
    USReplayDevelopmentPartition,
    USReplayFoldOOSReport,
    USReplayFoldPartition,
    USReplayResearchDataset,
)


def _scenario(value):
    if value is None:
        return None

    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise ValueError(
            "canonical nonblank scenario_id required"
        )

    return value


def _boundary(
    dataset,
    *,
    evaluation_cutoff_at,
    research_built_at,
):
    # Reconstruct every candidate before filtering. Therefore malformed
    # future evidence cannot hide behind the historical cutoff.
    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )

    evaluation_cutoff_at = exact_utc(
        evaluation_cutoff_at
    )
    research_built_at = exact_utc(
        research_built_at
    )

    if research_built_at < evaluation_cutoff_at:
        raise ValueError(
            "research_built_at cannot precede "
            "evaluation_cutoff_at"
        )

    return (
        dataset,
        evaluation_cutoff_at,
        research_built_at,
    )


def _latest_candidates(
    dataset,
    *,
    evaluation_cutoff_at,
    research_built_at,
    scenario_id,
    strict_before_at=None,
):
    selected = {}

    for row in dataset.candidates:
        if row.scenario_id != scenario_id:
            continue

        if row.evidence_available_at > evaluation_cutoff_at:
            continue

        if (
            strict_before_at is not None
            and row.evidence_available_at >= strict_before_at
        ):
            continue

        if row.request.research_built_at > research_built_at:
            continue

        previous = selected.get(
            row.observation_id
        )

        if (
            previous is None
            or previous.evidence_available_at
            < row.evidence_available_at
        ):
            selected[
                row.observation_id
            ] = row

    return tuple(
        sorted(
            selected.values(),
            key=lambda row: (
                row.decision_at,
                str(row.observation_id),
                row.evidence_available_at,
                row.identity,
            ),
        )
    )


def select_us_replay_candidates_as_of(
    dataset: USReplayResearchDataset,
    *,
    evaluation_cutoff_at,
    research_built_at,
    scenario_id=None,
):
    """Return latest candidate visible at the explicit historical boundary."""

    (
        dataset,
        evaluation_cutoff_at,
        research_built_at,
    ) = _boundary(
        dataset,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    scenario_id = _scenario(
        scenario_id
    )

    return _latest_candidates(
        dataset,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
        scenario_id=scenario_id,
    )


def partition_us_replay_development(
    dataset: USReplayResearchDataset,
    plan: WalkForwardPlan,
    *,
    evaluation_cutoff_at,
    research_built_at,
) -> USReplayDevelopmentPartition:
    """Partition baseline replay truth without leaking later labels."""

    (
        dataset,
        evaluation_cutoff_at,
        research_built_at,
    ) = _boundary(
        dataset,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    plan = canonical(
        plan,
        WalkForwardPlan,
    )

    visible = _latest_candidates(
        dataset,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
        scenario_id=None,
    )

    partitions = []

    for fold in plan.folds:
        known_before_test = {
            row.observation_id: row
            for row in _latest_candidates(
                dataset,
                evaluation_cutoff_at=evaluation_cutoff_at,
                research_built_at=research_built_at,
                scenario_id=None,
                strict_before_at=fold.test.start,
            )
        }

        training = []
        purged = []
        unavailable = []
        incompatible_training = []
        test = []
        incompatible_test = []

        for row in visible:
            at = row.decision_at

            if fold.train.start <= at < fold.train.end:
                prior = known_before_test.get(
                    row.observation_id
                )

                if (
                    prior is not None
                    and prior.result.status
                    == "INCOMPATIBLE"
                ):
                    incompatible_training.append(
                        row.observation_id
                    )

                elif (
                    prior is not None
                    and prior.economic_label
                    is not None
                ):
                    training.append(
                        row.observation_id
                    )

                elif (
                    row.result.status
                    == "INCOMPATIBLE"
                ):
                    incompatible_training.append(
                        row.observation_id
                    )

                elif (
                    row.economic_label
                    is not None
                ):
                    # The completed label exists by the outer evaluation
                    # cutoff, but not strictly before test.start.
                    purged.append(
                        row.observation_id
                    )

                else:
                    unavailable.append(
                        row.observation_id
                    )

            if fold.test.start <= at < fold.test.end:
                test.append(
                    row.observation_id
                )

                if (
                    row.result.status
                    == "INCOMPATIBLE"
                ):
                    incompatible_test.append(
                        row.observation_id
                    )

        partitions.append(
            USReplayFoldPartition(
                fold=fold,
                training_ids=tuple(training),
                purged_training_ids=tuple(
                    purged
                ),
                unavailable_training_ids=tuple(
                    unavailable
                ),
                incompatible_training_ids=tuple(
                    incompatible_training
                ),
                test_ids=tuple(test),
                incompatible_test_ids=tuple(
                    incompatible_test
                ),
            )
        )

    return USReplayDevelopmentPartition(
        strategy_id=dataset.strategy_id,
        strategy_version=dataset.strategy_version,
        plan=plan,
        evaluation_cutoff_at=evaluation_cutoff_at,
        folds=tuple(partitions),
    )

def evaluate_us_replay_development_oos(
    dataset: USReplayResearchDataset,
    partition: USReplayDevelopmentPartition,
    performance_config: PerformanceConfig,
    *,
    research_built_at,
) -> USReplayDevelopmentOOSReport:
    """Evaluate baseline development OOS only through the M7.1 replay path."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    partition = canonical(
        partition,
        USReplayDevelopmentPartition,
    )
    performance_config = canonical(
        performance_config,
        PerformanceConfig,
    )
    research_built_at = exact_utc(
        research_built_at
    )

    if (
        dataset.strategy_id
        != partition.strategy_id
        or dataset.strategy_version
        != partition.strategy_version
    ):
        raise ValueError(
            "dataset strategy identity differs from partition"
        )

    expected_partition = partition_us_replay_development(
        dataset,
        partition.plan,
        evaluation_cutoff_at=partition.evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    if not exact_equal(
        partition,
        expected_partition,
    ):
        raise ValueError(
            "development partition mismatch"
        )

    visible = {
        row.observation_id: row
        for row in _latest_candidates(
            dataset,
            evaluation_cutoff_at=partition.evaluation_cutoff_at,
            research_built_at=research_built_at,
            scenario_id=None,
        )
    }

    fold_reports = []
    aggregate_observations = []
    aggregate_test_ids = []
    aggregate_replay_ids = []
    aggregate_incompatible_ids = []

    for fold_partition in partition.folds:
        replay_ids = []
        incompatible_ids = []
        observations = []

        for observation_id in fold_partition.test_ids:
            row = visible.get(
                observation_id
            )

            if row is None:
                raise ValueError(
                    "partition test observation is not visible"
                )

            if row.result.status == "INCOMPATIBLE":
                incompatible_ids.append(
                    observation_id
                )
                continue

            observation = (
                build_us_replay_performance_observation(
                    row.request,
                    row.result,
                )
            )
            observations.append(
                observation
            )
            replay_ids.append(
                observation_id
            )

        if tuple(
            incompatible_ids
        ) != fold_partition.incompatible_test_ids:
            raise ValueError(
                "partition incompatible_test_ids mismatch"
            )

        fold_performance = analyze_replay_performance(
            ReplayPerformanceAnalysisInput(
                config=performance_config,
                observations=tuple(
                    observations
                ),
            )
        )

        fold_status = (
            "INSUFFICIENT_EVIDENCE"
            if incompatible_ids
            else "EVALUATED"
        )

        fold_reports.append(
            USReplayFoldOOSReport(
                fold_id=fold_partition.fold.fold_id,
                test_ids=fold_partition.test_ids,
                replay_observation_ids=tuple(
                    replay_ids
                ),
                incompatible_observation_ids=tuple(
                    incompatible_ids
                ),
                performance=fold_performance,
                status=fold_status,
            )
        )

        aggregate_observations.extend(
            observations
        )
        aggregate_test_ids.extend(
            fold_partition.test_ids
        )
        aggregate_replay_ids.extend(
            replay_ids
        )
        aggregate_incompatible_ids.extend(
            incompatible_ids
        )

    if len(
        set(aggregate_test_ids)
    ) != len(
        aggregate_test_ids
    ):
        raise ValueError(
            "duplicate OOS trade across folds"
        )

    performance = analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=performance_config,
            observations=tuple(
                aggregate_observations
            ),
        )
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if aggregate_incompatible_ids
        else "EVALUATED"
    )

    return USReplayDevelopmentOOSReport(
        strategy_id=dataset.strategy_id,
        strategy_version=dataset.strategy_version,
        partition=partition,
        performance_config=performance_config,
        folds=tuple(
            fold_reports
        ),
        fold_count=len(
            fold_reports
        ),
        test_ids=tuple(
            aggregate_test_ids
        ),
        replay_observation_ids=tuple(
            aggregate_replay_ids
        ),
        incompatible_observation_ids=tuple(
            aggregate_incompatible_ids
        ),
        performance=performance,
        status=status,
    )

D = Decimal


def _replay_exit_key(observation):
    result = observation.replay_result

    if (
        result.state != "COMPLETED"
        or result.position is None
        or result.position.exit is None
        or result.metrics is None
    ):
        raise ValueError(
            "completed replay observation requires exit and metrics"
        )

    exit_fill = result.position.exit

    return (
        exit_fill.known_at_utc,
        exit_fill.interval_start_utc,
        str(
            observation.replay_input.trade_plan.trade_plan_id
        ),
    )


def _sample_replay_blocks(
    values,
    block_size,
    rng,
):
    sampled = []

    while len(sampled) < len(values):
        start = rng.randrange(
            len(values) - block_size + 1
        )
        sampled.extend(
            values[
                start:start + block_size
            ]
        )

    return tuple(
        sampled[:len(values)]
    )


def _replay_bootstrap_statistics(
    values,
    starting_equity,
):
    equity = peak = starting_equity
    drawdown = D(0)

    for net in values:
        equity += net
        peak = max(
            peak,
            equity,
        )
        drawdown = max(
            drawdown,
            peak - equity,
        )

    total = sum(
        values,
        D(0),
    )

    return (
        total / D(len(values)),
        total,
        drawdown,
    )


def _replay_quantile(
    values,
    probability,
):
    ordered = sorted(
        values
    )
    index = (
        D(len(ordered) - 1)
        * probability
    )
    low = int(index)
    high = min(
        low + 1,
        len(ordered) - 1,
    )

    return (
        ordered[low]
        + (
            ordered[high]
            - ordered[low]
        )
        * (
            index
            - low
        )
    )


def _bootstrap_us_replay(
    observations,
    performance,
    performance_config,
    bootstrap_config,
):
    completed = sorted(
        (
            row
            for row in observations
            if row.replay_result.state
            == "COMPLETED"
        ),
        key=_replay_exit_key,
    )

    values = tuple(
        row.replay_result.metrics.net_pnl
        for row in completed
    )
    ids = tuple(
        row.replay_input.trade_plan.trade_plan_id
        for row in completed
    )

    n = len(values)

    reason = (
        "ZERO_COMPLETED"
        if n == 0
        else "ONE_COMPLETED"
        if n == 1
        else "BLOCK_EXCEEDS_SAMPLE"
        if bootstrap_config.block_size > n
        else None
    )

    report = performance.performance

    estimates = (
        report.summary.net_expectancy,
        (
            report.summary.total_net_pnl
            if n
            else None
        ),
        (
            report.drawdown.max_drawdown_amount
            if n
            else None
        ),
    )

    metrics = (
        [],
        [],
        [],
    )

    with localcontext(
        Context(prec=34)
    ):
        if reason is None:
            rng = Random(
                bootstrap_config.seed
            )

            for _ in range(
                bootstrap_config.replications
            ):
                result = (
                    _replay_bootstrap_statistics(
                        _sample_replay_blocks(
                            values,
                            bootstrap_config.block_size,
                            rng,
                        ),
                        performance_config.starting_equity,
                    )
                )

                for bucket, value in zip(
                    metrics,
                    result,
                ):
                    bucket.append(
                        value
                    )

        tail = (
            D(1)
            - bootstrap_config.confidence_level
        ) / D(2)

        intervals = tuple(
            ConfidenceInterval(
                estimate=estimate,
                lower=(
                    _replay_quantile(
                        bucket,
                        tail,
                    )
                    if bucket
                    else None
                ),
                upper=(
                    _replay_quantile(
                        bucket,
                        D(1) - tail,
                    )
                    if bucket
                    else None
                ),
                valid_replications=len(
                    bucket
                ),
                unavailable_reason=reason,
            )
            for estimate, bucket in zip(
                estimates,
                metrics,
            )
        )

    return BootstrapReport(
        config=bootstrap_config,
        completed_observation_ids=ids,
        sample_count=n,
        net_expectancy=intervals[0],
        total_net_pnl=intervals[1],
        max_drawdown_amount=intervals[2],
    )


def _us_replay_checks(
    performance,
    criteria,
    bootstrap,
):
    report = performance.performance
    summary = report.summary

    minimum = (
        criteria.minimum_completed_development_trades
    )

    values = [
        (
            "completed_trades",
            D(summary.completed_count),
            D(minimum),
            "GE",
        ),
        (
            "net_expectancy",
            summary.net_expectancy,
            criteria.minimum_net_expectancy,
            "GE",
        ),
        (
            "drawdown_fraction",
            (
                report.drawdown.max_drawdown_pct
                if summary.completed_count
                else None
            ),
            criteria.maximum_drawdown_fraction,
            "LE",
        ),
    ]

    if (
        criteria.minimum_profit_factor
        is not None
    ):
        values.append(
            (
                "profit_factor",
                summary.profit_factor,
                criteria.minimum_profit_factor,
                "GE",
            )
        )

    if (
        criteria.minimum_net_expectancy_lower_bound
        is not None
    ):
        values.append(
            (
                "net_expectancy_lower_bound",
                bootstrap.net_expectancy.lower,
                criteria.minimum_net_expectancy_lower_bound,
                "GE",
            )
        )

    checks = []

    for (
        name,
        actual,
        threshold,
        comparison,
    ) in values:
        reason = (
            "BELOW_DECLARED_SAMPLE_MINIMUM"
            if summary.completed_count
            < minimum
            else "UNDEFINED_METRIC"
            if actual is None
            else None
        )

        checks.append(
            CriterionCheck(
                criterion=name,
                actual=actual,
                threshold=threshold,
                comparison=comparison,
                passed=(
                    None
                    if reason
                    else (
                        actual >= threshold
                        if comparison == "GE"
                        else actual <= threshold
                    )
                ),
                unavailable_reason=reason,
            )
        )

    return tuple(
        checks
    )


def _us_replay_evidence_status(
    oos,
    bootstrap,
    checks,
):
    if (
        oos.status
        == "INSUFFICIENT_EVIDENCE"
        or bootstrap.net_expectancy.lower
        is None
        or any(
            check.passed is None
            for check in checks
        )
    ):
        return "INSUFFICIENT_EVIDENCE"

    if any(
        not check.passed
        for check in checks
    ):
        return "FAILS_DECLARED_CRITERIA"

    return "MEETS_DECLARED_CRITERIA"


def evaluate_us_replay_development_evidence(
    dataset: USReplayResearchDataset,
    partition: USReplayDevelopmentPartition,
    performance_config: PerformanceConfig,
    bootstrap_config: BootstrapConfig,
    criteria: ResearchEvidenceCriteria,
    *,
    research_built_at,
) -> USReplayDevelopmentEvidenceReport:
    """Evaluate deterministic development criteria without resimulating bootstrap paths."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    partition = canonical(
        partition,
        USReplayDevelopmentPartition,
    )
    performance_config = canonical(
        performance_config,
        PerformanceConfig,
    )
    bootstrap_config = canonical(
        bootstrap_config,
        BootstrapConfig,
    )
    criteria = canonical(
        criteria,
        ResearchEvidenceCriteria,
    )
    research_built_at = exact_utc(
        research_built_at
    )

    oos = evaluate_us_replay_development_oos(
        dataset,
        partition,
        performance_config,
        research_built_at=research_built_at,
    )

    visible = {
        row.observation_id: row
        for row in _latest_candidates(
            dataset,
            evaluation_cutoff_at=(
                partition.evaluation_cutoff_at
            ),
            research_built_at=research_built_at,
            scenario_id=None,
        )
    }

    observations = []

    for observation_id in (
        oos.replay_observation_ids
    ):
        row = visible.get(
            observation_id
        )

        if row is None:
            raise ValueError(
                "OOS replay observation is not visible"
            )

        if (
            row.result.status
            != "REPLAYED"
        ):
            raise ValueError(
                "OOS replay observation became incompatible"
            )

        observations.append(
            build_us_replay_performance_observation(
                row.request,
                row.result,
            )
        )

    bootstrap = _bootstrap_us_replay(
        tuple(observations),
        oos.performance,
        performance_config,
        bootstrap_config,
    )

    checks = _us_replay_checks(
        oos.performance,
        criteria,
        bootstrap,
    )

    status = _us_replay_evidence_status(
        oos,
        bootstrap,
        checks,
    )

    return USReplayDevelopmentEvidenceReport(
        oos=oos,
        bootstrap=bootstrap,
        criteria=criteria,
        checks=checks,
        status=status,
    )

def _canonical_scenario_ids(value):
    if type(value) is not tuple:
        raise ValueError(
            "tuple of declared scenario IDs required"
        )

    rows = []

    for row in value:
        if (
            type(row) is not str
            or not row
            or row != row.strip()
        ):
            raise ValueError(
                "canonical nonblank scenario_id required"
            )

        rows.append(
            row
        )

    if len(set(rows)) != len(rows):
        raise ValueError(
            "duplicate scenario_id"
        )

    return tuple(
        sorted(rows)
    )


def evaluate_us_replay_development_scenarios(
    dataset: USReplayResearchDataset,
    partition: USReplayDevelopmentPartition,
    performance_config: PerformanceConfig,
    scenario_ids: tuple[str, ...],
    *,
    research_built_at,
) -> USReplayDevelopmentScenarioReport:
    """Require exact baseline OOS coverage before delegating scenario economics to M7.1."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    partition = canonical(
        partition,
        USReplayDevelopmentPartition,
    )
    performance_config = canonical(
        performance_config,
        PerformanceConfig,
    )
    scenario_ids = _canonical_scenario_ids(
        scenario_ids
    )
    research_built_at = exact_utc(
        research_built_at
    )

    oos = evaluate_us_replay_development_oos(
        dataset,
        partition,
        performance_config,
        research_built_at=research_built_at,
    )

    baseline_visible = {
        row.observation_id: row
        for row in _latest_candidates(
            dataset,
            evaluation_cutoff_at=(
                partition.evaluation_cutoff_at
            ),
            research_built_at=research_built_at,
            scenario_id=None,
        )
    }

    baseline_observations = []

    for observation_id in (
        oos.replay_observation_ids
    ):
        row = baseline_visible.get(
            observation_id
        )

        if row is None:
            raise ValueError(
                "baseline OOS replay observation is not visible"
            )

        if row.result.status != "REPLAYED":
            raise ValueError(
                "baseline OOS replay observation became incompatible"
            )

        baseline_observations.append(
            build_us_replay_performance_observation(
                row.request,
                row.result,
            )
        )

    baseline_incompatible = set(
        oos.incompatible_observation_ids
    )

    coverage_rows = []
    complete_scenarios = []

    for scenario_id in scenario_ids:
        visible = {
            row.observation_id: row
            for row in _latest_candidates(
                dataset,
                evaluation_cutoff_at=(
                    partition.evaluation_cutoff_at
                ),
                research_built_at=research_built_at,
                scenario_id=scenario_id,
            )
        }

        replay_ids = []
        unavailable_ids = []
        incompatible_ids = []
        baseline_incompatible_ids = []
        observations = []

        for observation_id in oos.test_ids:
            if observation_id in baseline_incompatible:
                baseline_incompatible_ids.append(
                    observation_id
                )
                continue

            row = visible.get(
                observation_id
            )

            if row is None:
                unavailable_ids.append(
                    observation_id
                )
                continue

            if row.result.status == "INCOMPATIBLE":
                incompatible_ids.append(
                    observation_id
                )
                continue

            observations.append(
                build_us_replay_performance_observation(
                    row.request,
                    row.result,
                )
            )
            replay_ids.append(
                observation_id
            )

        complete = (
            tuple(replay_ids)
            == tuple(oos.test_ids)
            and not unavailable_ids
            and not incompatible_ids
            and not baseline_incompatible_ids
        )

        coverage_rows.append(
            USReplayScenarioCoverage(
                scenario_id=scenario_id,
                baseline_observation_ids=oos.test_ids,
                replay_observation_ids=tuple(
                    replay_ids
                ),
                unavailable_observation_ids=tuple(
                    unavailable_ids
                ),
                incompatible_observation_ids=tuple(
                    incompatible_ids
                ),
                baseline_incompatible_observation_ids=tuple(
                    baseline_incompatible_ids
                ),
                status=(
                    "EVALUATED"
                    if complete
                    else "INCOMPLETE"
                ),
            )
        )

        if complete:
            complete_scenarios.append(
                ReplaySlippageScenario(
                    scenario_id=scenario_id,
                    observations=tuple(
                        observations
                    ),
                )
            )

    performance = analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=performance_config,
            observations=tuple(
                baseline_observations
            ),
            slippage_scenarios=tuple(
                complete_scenarios
            ),
        )
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if (
            oos.status == "INSUFFICIENT_EVIDENCE"
            or any(
                row.status == "INCOMPLETE"
                for row in coverage_rows
            )
        )
        else "EVALUATED"
    )

    return USReplayDevelopmentScenarioReport(
        oos=oos,
        scenario_ids=scenario_ids,
        scenarios=tuple(
            coverage_rows
        ),
        performance=performance,
        status=status,
    )


def evaluate_us_replay_development_validation(
    dataset: USReplayResearchDataset,
    partition: USReplayDevelopmentPartition,
    performance_config: PerformanceConfig,
    bootstrap_config: BootstrapConfig,
    criteria: ResearchEvidenceCriteria,
    scenario_ids: tuple[str, ...],
    *,
    research_built_at,
) -> USReplayDevelopmentValidationReport:
    """Combine baseline criteria with exact declared-scenario coverage."""

    scenario_ids = _canonical_scenario_ids(
        scenario_ids
    )

    evidence = evaluate_us_replay_development_evidence(
        dataset,
        partition,
        performance_config,
        bootstrap_config,
        criteria,
        research_built_at=research_built_at,
    )

    scenarios = evaluate_us_replay_development_scenarios(
        dataset,
        partition,
        performance_config,
        scenario_ids,
        research_built_at=research_built_at,
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if (
            evidence.status == "INSUFFICIENT_EVIDENCE"
            or scenarios.status == "INSUFFICIENT_EVIDENCE"
        )
        else evidence.status
    )

    return USReplayDevelopmentValidationReport(
        evidence=evidence,
        scenarios=scenarios,
        status=status,
    )

from decimal import Decimal

from app.performance.models import PerformanceConfig
from app.performance.replay_models import (
    ReplayPerformanceAnalysisInput,
    ReplaySlippageScenario,
)
from app.research.evidence import CriterionCheck, ResearchEvidenceCriteria
from app.research.models import WalkForwardPlan
from app.us.research_validation_models import (
    USReplayFrozenHoldoutReport,
    USReplayFrozenHoldoutScenarioReport,
    USReplayFrozenHoldoutValidationReport,
)

def _us_replay_holdout_checks(
    performance,
    criteria,
):
    """Legacy-M8B-equivalent holdout checks over M7.1 replay economics."""

    perf = performance.performance
    summary = perf.summary
    minimum = (
        criteria.minimum_completed_holdout_trades
    )

    values = [
        (
            "completed_trades",
            Decimal(summary.completed_count),
            Decimal(minimum),
            "GE",
        ),
        (
            "net_expectancy",
            summary.net_expectancy,
            criteria.minimum_net_expectancy,
            "GE",
        ),
        (
            "drawdown_fraction",
            (
                perf.drawdown.max_drawdown_pct
                if summary.completed_count
                else None
            ),
            criteria.maximum_drawdown_fraction,
            "LE",
        ),
    ]

    if criteria.minimum_profit_factor is not None:
        values.append(
            (
                "profit_factor",
                summary.profit_factor,
                criteria.minimum_profit_factor,
                "GE",
            )
        )

    checks = []

    for (
        name,
        actual,
        threshold,
        comparison,
    ) in values:
        reason = (
            "BELOW_DECLARED_SAMPLE_MINIMUM"
            if summary.completed_count < minimum
            else (
                "UNDEFINED_METRIC"
                if actual is None
                else None
            )
        )

        checks.append(
            CriterionCheck(
                criterion=name,
                actual=actual,
                threshold=threshold,
                comparison=comparison,
                passed=(
                    None
                    if reason
                    else (
                        actual >= threshold
                        if comparison == "GE"
                        else actual <= threshold
                    )
                ),
                unavailable_reason=reason,
            )
        )

    return tuple(checks)


def _us_replay_holdout_status(
    incompatible_ids,
    checks,
):
    if (
        incompatible_ids
        or any(
            check.passed is None
            for check in checks
        )
    ):
        return "INSUFFICIENT_EVIDENCE"

    if any(
        not check.passed
        for check in checks
    ):
        return "FAILS_DECLARED_CRITERIA"

    return "MEETS_DECLARED_CRITERIA"


def _us_replay_holdout_rows(
    dataset,
    plan,
    *,
    evaluation_cutoff_at,
    research_built_at,
    scenario_id,
):
    visible = _latest_candidates(
        dataset,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
        scenario_id=scenario_id,
    )
    interval = plan.holdout.interval

    return tuple(
        sorted(
            (
                row
                for row in visible
                if (
                    interval.start
                    <= row.decision_at
                    < interval.end
                )
            ),
            key=lambda row: (
                row.decision_at,
                str(row.observation_id),
            ),
        )
    )


def evaluate_us_replay_frozen_holdout(
    dataset: USReplayResearchDataset,
    plan: WalkForwardPlan,
    performance_config: PerformanceConfig,
    criteria: ResearchEvidenceCriteria,
    *,
    evaluation_cutoff_at,
    research_built_at,
) -> USReplayFrozenHoldoutReport:
    """Evaluate one frozen holdout explicitly, with no bootstrap."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    plan = canonical(
        plan,
        WalkForwardPlan,
    )
    performance_config = canonical(
        performance_config,
        PerformanceConfig,
    )
    criteria = canonical(
        criteria,
        ResearchEvidenceCriteria,
    )
    evaluation_cutoff_at = exact_utc(
        evaluation_cutoff_at
    )
    research_built_at = exact_utc(
        research_built_at
    )

    if evaluation_cutoff_at < plan.holdout.interval.end:
        raise ValueError(
            "holdout evaluation cutoff must be at or after holdout end"
        )

    rows = _us_replay_holdout_rows(
        dataset,
        plan,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
        scenario_id=None,
    )

    observation_ids = tuple(
        row.observation_id
        for row in rows
    )
    replay_ids = []
    incompatible_ids = []
    observations = []

    for row in rows:
        if row.result.status == "INCOMPATIBLE":
            incompatible_ids.append(
                row.observation_id
            )
            continue

        observations.append(
            build_us_replay_performance_observation(
                row.request,
                row.result,
            )
        )
        replay_ids.append(
            row.observation_id
        )

    performance = analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=performance_config,
            observations=tuple(
                observations
            ),
        )
    )

    checks = _us_replay_holdout_checks(
        performance,
        criteria,
    )
    status = _us_replay_holdout_status(
        tuple(incompatible_ids),
        checks,
    )

    return USReplayFrozenHoldoutReport(
        strategy_id=dataset.strategy_id,
        strategy_version=dataset.strategy_version,
        holdout=plan.holdout,
        evaluation_cutoff_at=evaluation_cutoff_at,
        performance_config=performance_config,
        observation_ids=observation_ids,
        replay_observation_ids=tuple(
            replay_ids
        ),
        incompatible_observation_ids=tuple(
            incompatible_ids
        ),
        performance=performance,
        criteria=criteria,
        checks=checks,
        status=status,
    )


def evaluate_us_replay_frozen_holdout_scenarios(
    dataset: USReplayResearchDataset,
    plan: WalkForwardPlan,
    performance_config: PerformanceConfig,
    criteria: ResearchEvidenceCriteria,
    scenario_ids: tuple[str, ...],
    *,
    evaluation_cutoff_at,
    research_built_at,
) -> USReplayFrozenHoldoutScenarioReport:
    """Evaluate only exact-set complete holdout scenarios through M7.1."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    plan = canonical(
        plan,
        WalkForwardPlan,
    )
    performance_config = canonical(
        performance_config,
        PerformanceConfig,
    )
    criteria = canonical(
        criteria,
        ResearchEvidenceCriteria,
    )
    scenario_ids = _canonical_scenario_ids(
        scenario_ids
    )
    evaluation_cutoff_at = exact_utc(
        evaluation_cutoff_at
    )
    research_built_at = exact_utc(
        research_built_at
    )

    holdout = evaluate_us_replay_frozen_holdout(
        dataset,
        plan,
        performance_config,
        criteria,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    baseline_visible = {
        row.observation_id: row
        for row in _us_replay_holdout_rows(
            dataset,
            plan,
            evaluation_cutoff_at=evaluation_cutoff_at,
            research_built_at=research_built_at,
            scenario_id=None,
        )
    }

    baseline_observations = []

    for observation_id in (
        holdout.replay_observation_ids
    ):
        row = baseline_visible.get(
            observation_id
        )

        if row is None:
            raise ValueError(
                "holdout replay observation is not visible"
            )

        if row.result.status != "REPLAYED":
            raise ValueError(
                "holdout replay observation became incompatible"
            )

        baseline_observations.append(
            build_us_replay_performance_observation(
                row.request,
                row.result,
            )
        )

    baseline_incompatible = set(
        holdout.incompatible_observation_ids
    )

    coverage_rows = []
    complete_scenarios = []

    for scenario_id in scenario_ids:
        visible = {
            row.observation_id: row
            for row in _us_replay_holdout_rows(
                dataset,
                plan,
                evaluation_cutoff_at=evaluation_cutoff_at,
                research_built_at=research_built_at,
                scenario_id=scenario_id,
            )
        }

        replay_ids = []
        unavailable_ids = []
        incompatible_ids = []
        baseline_incompatible_ids = []
        observations = []

        for observation_id in (
            holdout.observation_ids
        ):
            if observation_id in baseline_incompatible:
                baseline_incompatible_ids.append(
                    observation_id
                )
                continue

            row = visible.get(
                observation_id
            )

            if row is None:
                unavailable_ids.append(
                    observation_id
                )
                continue

            if row.result.status == "INCOMPATIBLE":
                incompatible_ids.append(
                    observation_id
                )
                continue

            observations.append(
                build_us_replay_performance_observation(
                    row.request,
                    row.result,
                )
            )
            replay_ids.append(
                observation_id
            )

        complete = (
            tuple(replay_ids)
            == tuple(holdout.observation_ids)
            and not unavailable_ids
            and not incompatible_ids
            and not baseline_incompatible_ids
        )

        coverage_rows.append(
            USReplayScenarioCoverage(
                scenario_id=scenario_id,
                baseline_observation_ids=(
                    holdout.observation_ids
                ),
                replay_observation_ids=tuple(
                    replay_ids
                ),
                unavailable_observation_ids=tuple(
                    unavailable_ids
                ),
                incompatible_observation_ids=tuple(
                    incompatible_ids
                ),
                baseline_incompatible_observation_ids=tuple(
                    baseline_incompatible_ids
                ),
                status=(
                    "EVALUATED"
                    if complete
                    else "INCOMPLETE"
                ),
            )
        )

        if complete:
            complete_scenarios.append(
                ReplaySlippageScenario(
                    scenario_id=scenario_id,
                    observations=tuple(
                        observations
                    ),
                )
            )

    performance = analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=performance_config,
            observations=tuple(
                baseline_observations
            ),
            slippage_scenarios=tuple(
                complete_scenarios
            ),
        )
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if (
            holdout.status
            == "INSUFFICIENT_EVIDENCE"
            or any(
                row.status == "INCOMPLETE"
                for row in coverage_rows
            )
        )
        else "EVALUATED"
    )

    return USReplayFrozenHoldoutScenarioReport(
        holdout=holdout,
        scenario_ids=scenario_ids,
        scenarios=tuple(
            coverage_rows
        ),
        performance=performance,
        status=status,
    )


def evaluate_us_replay_frozen_holdout_validation(
    dataset: USReplayResearchDataset,
    plan: WalkForwardPlan,
    performance_config: PerformanceConfig,
    criteria: ResearchEvidenceCriteria,
    scenario_ids: tuple[str, ...],
    *,
    evaluation_cutoff_at,
    research_built_at,
) -> USReplayFrozenHoldoutValidationReport:
    """Combine frozen holdout criteria with exact required scenario coverage."""

    scenario_ids = _canonical_scenario_ids(
        scenario_ids
    )

    evidence = evaluate_us_replay_frozen_holdout(
        dataset,
        plan,
        performance_config,
        criteria,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    scenarios = evaluate_us_replay_frozen_holdout_scenarios(
        dataset,
        plan,
        performance_config,
        criteria,
        scenario_ids,
        evaluation_cutoff_at=evaluation_cutoff_at,
        research_built_at=research_built_at,
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if (
            evidence.status
            == "INSUFFICIENT_EVIDENCE"
            or scenarios.status
            == "INSUFFICIENT_EVIDENCE"
        )
        else evidence.status
    )

    return USReplayFrozenHoldoutValidationReport(
        evidence=evidence,
        scenarios=scenarios,
        status=status,
    )

from .research_validation_models import (
    USReplayFrozenResearchProtocol,
    USReplayResearchValidationReport,
    USReplaySelectedEvidence,
    _selected_evidence_sort_key,
    us_replay_validation_report_id,
)


def _selected_evidence_record(
    row,
    *,
    scope,
    fold_id=None,
):
    return USReplaySelectedEvidence(
        scope=scope,
        fold_id=fold_id,
        scenario_id=row.scenario_id,
        observation_id=row.observation_id,
        decision_at=row.decision_at,
        evidence_available_at=row.evidence_available_at,
        candidate_id=row.identity,
        request_id=row.request.identity,
        result_id=row.result.identity,
    )


def _decision_in_development(plan, decision_at):
    return any(
        (
            fold.train.start
            <= decision_at
            < fold.train.end
        )
        or (
            fold.test.start
            <= decision_at
            < fold.test.end
        )
        for fold in plan.folds
    )


def _final_selected_evidence(
    dataset,
    protocol,
    development,
    holdout,
    *,
    research_built_at,
):
    rows = []

    development_baseline = _latest_candidates(
        dataset,
        evaluation_cutoff_at=(
            protocol.development_evaluation_cutoff_at
        ),
        research_built_at=research_built_at,
        scenario_id=None,
    )

    rows.extend(
        _selected_evidence_record(
            row,
            scope="DEVELOPMENT_BASELINE",
        )
        for row in development_baseline
        if _decision_in_development(
            protocol.plan,
            row.decision_at,
        )
    )

    for fold in protocol.plan.folds:
        training_visible = _latest_candidates(
            dataset,
            evaluation_cutoff_at=(
                protocol.development_evaluation_cutoff_at
            ),
            research_built_at=research_built_at,
            scenario_id=None,
            strict_before_at=fold.test.start,
        )

        rows.extend(
            _selected_evidence_record(
                row,
                scope="DEVELOPMENT_TRAIN",
                fold_id=fold.fold_id,
            )
            for row in training_visible
            if (
                fold.train.start
                <= row.decision_at
                < fold.train.end
            )
        )

    development_relevant = (
        set(
            development.evidence.oos.test_ids
        )
        - set(
            development.evidence.oos.incompatible_observation_ids
        )
    )

    for scenario_id in protocol.scenario_ids:
        scenario_visible = _latest_candidates(
            dataset,
            evaluation_cutoff_at=(
                protocol.development_evaluation_cutoff_at
            ),
            research_built_at=research_built_at,
            scenario_id=scenario_id,
        )

        rows.extend(
            _selected_evidence_record(
                row,
                scope="DEVELOPMENT_SCENARIO",
            )
            for row in scenario_visible
            if row.observation_id in development_relevant
        )

    holdout_baseline = _us_replay_holdout_rows(
        dataset,
        protocol.plan,
        evaluation_cutoff_at=(
            protocol.holdout_evaluation_cutoff_at
        ),
        research_built_at=research_built_at,
        scenario_id=None,
    )

    rows.extend(
        _selected_evidence_record(
            row,
            scope="HOLDOUT_BASELINE",
        )
        for row in holdout_baseline
    )

    holdout_relevant = (
        set(
            holdout.evidence.observation_ids
        )
        - set(
            holdout.evidence.incompatible_observation_ids
        )
    )

    for scenario_id in protocol.scenario_ids:
        scenario_visible = _us_replay_holdout_rows(
            dataset,
            protocol.plan,
            evaluation_cutoff_at=(
                protocol.holdout_evaluation_cutoff_at
            ),
            research_built_at=research_built_at,
            scenario_id=scenario_id,
        )

        rows.extend(
            _selected_evidence_record(
                row,
                scope="HOLDOUT_SCENARIO",
            )
            for row in scenario_visible
            if row.observation_id in holdout_relevant
        )

    return tuple(
        sorted(
            rows,
            key=_selected_evidence_sort_key,
        )
    )


def evaluate_us_replay_research_validation(
    dataset: USReplayResearchDataset,
    protocol: USReplayFrozenResearchProtocol,
    *,
    research_built_at,
) -> USReplayResearchValidationReport:
    """Build the final frozen US8 artifact without admitting future or local-clock semantics."""

    dataset = canonical(
        dataset,
        USReplayResearchDataset,
    )
    protocol = canonical(
        protocol,
        USReplayFrozenResearchProtocol,
    )
    research_built_at = exact_utc(
        research_built_at
    )

    if (
        dataset.strategy_id,
        dataset.strategy_version,
    ) != (
        protocol.strategy_id,
        protocol.strategy_version,
    ):
        raise ValueError(
            "dataset strategy identity differs from frozen protocol"
        )

    if (
        research_built_at
        < protocol.holdout_evaluation_cutoff_at
    ):
        raise ValueError(
            "research_built_at cannot precede frozen holdout evaluation cutoff"
        )

    partition = partition_us_replay_development(
        dataset,
        protocol.plan,
        evaluation_cutoff_at=(
            protocol.development_evaluation_cutoff_at
        ),
        research_built_at=research_built_at,
    )

    development = evaluate_us_replay_development_validation(
        dataset,
        partition,
        protocol.performance_config,
        protocol.bootstrap,
        protocol.criteria,
        protocol.scenario_ids,
        research_built_at=research_built_at,
    )

    holdout = evaluate_us_replay_frozen_holdout_validation(
        dataset,
        protocol.plan,
        protocol.performance_config,
        protocol.criteria,
        protocol.scenario_ids,
        evaluation_cutoff_at=(
            protocol.holdout_evaluation_cutoff_at
        ),
        research_built_at=research_built_at,
    )

    selected_evidence = _final_selected_evidence(
        dataset,
        protocol,
        development,
        holdout,
        research_built_at=research_built_at,
    )

    status = (
        "INSUFFICIENT_EVIDENCE"
        if (
            development.status
            == "INSUFFICIENT_EVIDENCE"
            or holdout.status
            == "INSUFFICIENT_EVIDENCE"
        )
        else (
            "FAILS_DECLARED_CRITERIA"
            if (
                development.status
                == "FAILS_DECLARED_CRITERIA"
                or holdout.status
                == "FAILS_DECLARED_CRITERIA"
            )
            else "MEETS_DECLARED_CRITERIA"
        )
    )

    report_id = us_replay_validation_report_id(
        protocol=protocol,
        development=development,
        holdout=holdout,
        selected_evidence=selected_evidence,
        status=status,
    )

    return USReplayResearchValidationReport(
        protocol=protocol,
        development=development,
        holdout=holdout,
        selected_evidence=selected_evidence,
        status=status,
        report_id=report_id,
    )

__all__ = [
    "select_us_replay_candidates_as_of",
    "partition_us_replay_development",
    "evaluate_us_replay_development_oos",
    "evaluate_us_replay_development_evidence",
    "evaluate_us_replay_development_scenarios",
    "evaluate_us_replay_development_validation",
    "evaluate_us_replay_frozen_holdout",
    "evaluate_us_replay_frozen_holdout_scenarios",
    "evaluate_us_replay_frozen_holdout_validation",
    "evaluate_us_replay_research_validation",
]
