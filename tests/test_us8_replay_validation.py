from datetime import timedelta, timezone
from decimal import Decimal
import inspect

import pytest

from app.performance.models import PerformanceConfig
from app.performance.replay import analyze_replay_performance
from app.performance.replay_models import ReplayPerformanceAnalysisInput
from app.research.evidence import (
    BootstrapConfig,
    ResearchEvidenceCriteria,
)
from app.research.models import (
    FrozenHoldout,
    ResearchInterval,
    WalkForwardFold,
    WalkForwardPlan,
)
from app.us.contracts import USCorporateActionType
from app.us.paper_replay import (
    build_us_replay_performance_observation,
    replay_us_research_trade,
)
from app.us.research_validation import (
    evaluate_us_replay_development_evidence,
    evaluate_us_replay_development_oos,
    partition_us_replay_development,
    select_us_replay_candidates_as_of,
)
from app.us.research_validation_models import (
    USReplayResearchCandidate,
    USReplayResearchDataset,
)

from test_us7c_b_paper_replay import DAY, action, make_request

DEFAULT_ROWS = inspect.signature(make_request).parameters["rows"].default


def candidate(*, scenario_id=None, request=None):
    request = request or make_request()
    result = replay_us_research_trade(request)

    return USReplayResearchCandidate(
        scenario_id=scenario_id,
        request=request,
        result=result,
    )


def dataset(*rows):
    return USReplayResearchDataset(
        strategy_id=rows[0].strategy_id if rows else "fixture-strategy",
        strategy_version=(
            rows[0].strategy_version
            if rows
            else "1"
        ),
        candidates=tuple(rows),
    )


def test_candidate_binds_exact_us7c_b_replay_truth():
    row = candidate()

    assert row.observation_id == row.request.plan.trade_plan_id
    assert (
        row.decision_at
        == row.request.risk_admission.risk_decision_at
    )
    assert (
        row.evidence_available_at
        == row.request.evidence_cutoff_at
    )

    assert row.result.status == "REPLAYED"

    if row.result.replay_result.state == "COMPLETED":
        assert row.economic_label in {
            "WIN",
            "LOSS",
            "BREAKEVEN",
        }
        assert (
            row.label_available_at
            == row.request.evidence_cutoff_at
        )


def test_candidate_rejects_forged_replay_result():
    request = make_request()
    result = replay_us_research_trade(request)

    forged = result.model_copy(
        update={
            "provenance": result.provenance.model_copy(
                update={
                    "request_id": "0" * 64,
                }
            ),
        }
    )

    with pytest.raises(
        ValueError,
        match="candidate result/provenance mismatch|noncanonical",
    ):
        USReplayResearchCandidate(
            request=request,
            result=forged,
        )


def test_candidate_rejects_dictionary_substitution():
    request = make_request()
    result = replay_us_research_trade(request)

    with pytest.raises(
        ValueError,
        match="USPaperReplayRequest",
    ):
        USReplayResearchCandidate(
            request=request.model_dump(
                mode="python",
            ),
            result=result,
        )

    with pytest.raises(
        ValueError,
        match="USPaperReplayResult",
    ):
        USReplayResearchCandidate(
            request=request,
            result=result.model_dump(
                mode="python",
            ),
        )


def test_dataset_requires_canonical_tuple():
    row = candidate()

    with pytest.raises(
        ValueError,
        match="tuple of canonical",
    ):
        USReplayResearchDataset(
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            candidates=[row],
        )


def test_duplicate_exact_version_key_fails():
    row = candidate()

    with pytest.raises(
        ValueError,
        match="duplicate replay research candidate version",
    ):
        dataset(row, row)


def test_scenario_and_baseline_are_distinct_versions():
    baseline = candidate()
    stress = candidate(
        scenario_id="stress",
        request=baseline.request,
    )

    built = dataset(
        stress,
        baseline,
    )

    assert built.candidates == (
        baseline,
        stress,
    )

    assert (
        baseline.observation_id
        == stress.observation_id
    )
    assert (
        baseline.evidence_available_at
        == stress.evidence_available_at
    )
    assert baseline.identity != stress.identity


def test_caller_order_does_not_change_canonical_dataset():
    baseline = candidate()
    stress = candidate(
        scenario_id="stress",
        request=baseline.request,
    )

    assert dataset(
        baseline,
        stress,
    ) == dataset(
        stress,
        baseline,
    )


def test_local_research_build_clock_does_not_change_semantic_identity():
    first_request = make_request()
    first = candidate(
        request=first_request,
    )

    later_request = first_request.model_copy(
        update={
            "research_built_at":
                first_request.research_built_at
                + timedelta(days=1),
        }
    )

    later = candidate(
        request=later_request,
    )

    assert (
        first_request.identity
        == later_request.identity
    )
    assert first.result == later.result
    assert first.identity == later.identity


def test_candidate_model_copy_result_corruption_is_revalidated():
    row = candidate()

    corrupted = row.model_copy(
        update={
            "result": row.result.model_copy(
                update={
                    "schema_version":
                        "not-us-paper-replay-result-v1",
                }
            ),
        }
    )

    with pytest.raises(ValueError):
        dataset(corrupted)


def test_empty_dataset_is_allowed_and_canonical():
    built = USReplayResearchDataset(
        strategy_id="fixture-strategy",
        strategy_version="1",
        candidates=(),
    )

    assert built.candidates == ()


def versions():
    early = candidate(
        request=make_request(
            rows=DEFAULT_ROWS[:1],
        )
    )

    late = candidate(
        request=make_request(
            rows=DEFAULT_ROWS[:2],
        )
    )

    assert early.observation_id == late.observation_id
    assert early.decision_at == late.decision_at
    assert early.evidence_available_at < late.evidence_available_at

    assert early.result.replay_result.state == "OPEN"
    assert late.result.replay_result.state == "COMPLETED"

    return early, late


def test_as_of_selects_latest_visible_version():
    early, late = versions()

    d = dataset(
        late,
        early,
    )

    build = late.request.research_built_at

    at_early = select_us_replay_candidates_as_of(
        d,
        evaluation_cutoff_at=early.evidence_available_at,
        research_built_at=build,
    )

    at_late = select_us_replay_candidates_as_of(
        d,
        evaluation_cutoff_at=late.evidence_available_at,
        research_built_at=build,
    )

    assert at_early == (early,)
    assert at_late == (late,)


def test_future_version_cannot_repair_earlier_truth():
    early, late = versions()

    build = late.request.research_built_at

    expected = select_us_replay_candidates_as_of(
        dataset(early),
        evaluation_cutoff_at=early.evidence_available_at,
        research_built_at=build,
    )

    actual = select_us_replay_candidates_as_of(
        dataset(
            late,
            early,
        ),
        evaluation_cutoff_at=early.evidence_available_at,
        research_built_at=build,
    )

    assert expected == actual
    assert actual == (early,)


def test_candidate_built_after_outer_build_is_not_visible():
    request = make_request(
        rows=DEFAULT_ROWS[:1],
    )

    later_request = request.model_copy(
        update={
            "research_built_at":
                request.research_built_at
                + timedelta(days=1),
        }
    )

    later = candidate(
        request=later_request
    )

    selected = select_us_replay_candidates_as_of(
        dataset(later),
        evaluation_cutoff_at=later.evidence_available_at,
        research_built_at=request.research_built_at,
    )

    assert selected == ()


def test_as_of_boundaries_require_exact_utc_and_chronology():
    row = candidate()
    d = dataset(row)

    noncanonical = row.evidence_available_at.replace(
        tzinfo=timezone(
            timedelta(hours=2)
        )
    )

    with pytest.raises(
        ValueError,
        match="timezone.utc",
    ):
        select_us_replay_candidates_as_of(
            d,
            evaluation_cutoff_at=noncanonical,
            research_built_at=row.request.research_built_at,
        )

    with pytest.raises(
        ValueError,
        match="cannot precede evaluation_cutoff",
    ):
        select_us_replay_candidates_as_of(
            d,
            evaluation_cutoff_at=row.evidence_available_at,
            research_built_at=(
                row.evidence_available_at
                - timedelta(seconds=1)
            ),
        )


def test_scenario_versions_are_selected_independently():
    baseline = candidate()

    stress = candidate(
        scenario_id="stress",
        request=baseline.request,
    )

    d = dataset(
        stress,
        baseline,
    )

    cutoff = baseline.evidence_available_at
    build = baseline.request.research_built_at

    assert select_us_replay_candidates_as_of(
        d,
        evaluation_cutoff_at=cutoff,
        research_built_at=build,
    ) == (baseline,)

    assert select_us_replay_candidates_as_of(
        d,
        evaluation_cutoff_at=cutoff,
        research_built_at=build,
        scenario_id="stress",
    ) == (stress,)


def development_plan(row, *, test_start):
    test_end = test_start + timedelta(hours=1)

    return WalkForwardPlan(
        plan_id="us8-fixture-plan",
        folds=(
            WalkForwardFold(
                fold_id="fold-1",
                train=ResearchInterval(
                    start=row.decision_at - timedelta(days=1),
                    end=test_start,
                ),
                test=ResearchInterval(
                    start=test_start,
                    end=test_end,
                ),
            ),
        ),
        holdout=FrozenHoldout(
            holdout_id="future-holdout",
            interval=ResearchInterval(
                start=test_end + timedelta(seconds=1),
                end=test_end + timedelta(days=1),
            ),
        ),
    )


def partition_for(
    d,
    row,
    *,
    test_start,
    evaluation_cutoff_at=None,
):
    return partition_us_replay_development(
        d,
        development_plan(
            row,
            test_start=test_start,
        ),
        evaluation_cutoff_at=(
            evaluation_cutoff_at
            or row.evidence_available_at
        ),
        research_built_at=row.request.research_built_at,
    )


def test_completed_strictly_before_test_is_training():
    early, late = versions()

    result = partition_for(
        dataset(early, late),
        late,
        test_start=late.evidence_available_at + timedelta(seconds=1),
        evaluation_cutoff_at=late.evidence_available_at,
    )

    fold = result.folds[0]

    assert fold.training_ids == (late.observation_id,)
    assert fold.purged_training_ids == ()
    assert fold.unavailable_training_ids == ()
    assert fold.incompatible_training_ids == ()


def test_completion_exactly_at_test_boundary_is_purged():
    early, late = versions()

    result = partition_for(
        dataset(late, early),
        late,
        test_start=late.evidence_available_at,
        evaluation_cutoff_at=late.evidence_available_at,
    )

    fold = result.folds[0]

    assert fold.training_ids == ()
    assert fold.purged_training_ids == (late.observation_id,)
    assert fold.unavailable_training_ids == ()
    assert fold.incompatible_training_ids == ()


def test_visible_open_training_truth_is_unavailable():
    early, _ = versions()

    result = partition_for(
        dataset(early),
        early,
        test_start=early.evidence_available_at + timedelta(seconds=1),
        evaluation_cutoff_at=early.evidence_available_at,
    )

    fold = result.folds[0]

    assert fold.training_ids == ()
    assert fold.purged_training_ids == ()
    assert fold.unavailable_training_ids == (early.observation_id,)
    assert fold.incompatible_training_ids == ()


def incompatible_candidate():
    request = make_request(
        actions=(
            action(
                USCorporateActionType.SPLIT,
                DAY + timedelta(days=1),
            ),
        ),
    )

    row = candidate(request=request)

    assert row.result.status == "INCOMPATIBLE"
    assert row.result.replay_input is None
    assert row.result.replay_result is None

    return row


def test_incompatible_training_truth_is_explicit():
    row = incompatible_candidate()

    result = partition_for(
        dataset(row),
        row,
        test_start=row.evidence_available_at + timedelta(seconds=1),
    )

    fold = result.folds[0]

    assert fold.training_ids == ()
    assert fold.purged_training_ids == ()
    assert fold.unavailable_training_ids == ()
    assert fold.incompatible_training_ids == (row.observation_id,)


def test_test_membership_preserved_for_incompatible_trade():
    row = incompatible_candidate()

    result = partition_for(
        dataset(row),
        row,
        test_start=row.decision_at,
    )

    fold = result.folds[0]

    assert fold.training_ids == ()
    assert fold.test_ids == (row.observation_id,)
    assert fold.incompatible_test_ids == (row.observation_id,)


def test_replayed_noncompleted_test_membership_is_preserved():
    early, _ = versions()

    result = partition_for(
        dataset(early),
        early,
        test_start=early.decision_at,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    fold = result.folds[0]

    assert fold.test_ids == (early.observation_id,)
    assert fold.incompatible_test_ids == ()


def test_future_completion_does_not_change_earlier_partition():
    early, late = versions()

    test_start = late.evidence_available_at

    expected = partition_for(
        dataset(early),
        early,
        test_start=test_start,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    actual = partition_for(
        dataset(late, early),
        early,
        test_start=test_start,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    assert actual == expected
    assert actual.folds[0].unavailable_training_ids == (
        early.observation_id,
    )


def test_scenario_candidates_do_not_change_baseline_partition():
    early, late = versions()

    stress = candidate(
        scenario_id="stress",
        request=late.request,
    )

    expected = partition_for(
        dataset(early, late),
        late,
        test_start=late.evidence_available_at + timedelta(seconds=1),
        evaluation_cutoff_at=late.evidence_available_at,
    )

    actual = partition_for(
        dataset(stress, late, early),
        late,
        test_start=late.evidence_available_at + timedelta(seconds=1),
        evaluation_cutoff_at=late.evidence_available_at,
    )

    assert actual == expected


def test_partition_identity_is_deterministic_off_caller_order():
    early, late = versions()

    kwargs = dict(
        test_start=late.evidence_available_at + timedelta(seconds=1),
        evaluation_cutoff_at=late.evidence_available_at,
    )

    first = partition_for(
        dataset(early, late),
        late,
        **kwargs,
    )

    second = partition_for(
        dataset(late, early),
        late,
        **kwargs,
    )

    assert first == second
    assert first.identity == second.identity


def us8_performance_config():
    return PerformanceConfig(
        config_version="performance-v1",
        starting_equity=Decimal(1000),
    )


def direct_m7_1_report(*rows):
    observations = tuple(
        build_us_replay_performance_observation(
            row.request,
            row.result,
        )
        for row in rows
    )

    return analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=us8_performance_config(),
            observations=observations,
        )
    )


def evaluate_oos(d, partition, row):
    return evaluate_us_replay_development_oos(
        d,
        partition,
        us8_performance_config(),
        research_built_at=row.request.research_built_at,
    )


def test_completed_oos_economics_exactly_match_m7_1():
    early, late = versions()

    d = dataset(
        early,
        late,
    )

    partition = partition_for(
        d,
        late,
        test_start=late.decision_at,
        evaluation_cutoff_at=late.evidence_available_at,
    )

    report = evaluate_oos(
        d,
        partition,
        late,
    )

    expected = direct_m7_1_report(
        late
    )

    assert report.status == "EVALUATED"
    assert report.test_ids == (
        late.observation_id,
    )
    assert report.replay_observation_ids == (
        late.observation_id,
    )
    assert report.incompatible_observation_ids == ()
    assert report.performance == expected

    fold = report.folds[0]

    assert fold.status == "EVALUATED"
    assert fold.test_ids == (
        late.observation_id,
    )
    assert fold.replay_observation_ids == (
        late.observation_id,
    )
    assert fold.incompatible_observation_ids == ()
    assert fold.performance == expected


def test_open_oos_is_preserved_and_exactly_matches_m7_1():
    early, _ = versions()

    d = dataset(early)

    partition = partition_for(
        d,
        early,
        test_start=early.decision_at,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    report = evaluate_oos(
        d,
        partition,
        early,
    )

    expected = direct_m7_1_report(
        early
    )

    assert (
        early.result.replay_result.state
        == "OPEN"
    )
    assert report.status == "EVALUATED"
    assert report.test_ids == (
        early.observation_id,
    )
    assert report.replay_observation_ids == (
        early.observation_id,
    )
    assert report.incompatible_observation_ids == ()
    assert report.performance == expected
    assert report.folds[0].performance == expected


def test_incompatible_oos_is_explicit_and_insufficient():
    row = incompatible_candidate()

    d = dataset(row)

    partition = partition_for(
        d,
        row,
        test_start=row.decision_at,
    )

    report = evaluate_oos(
        d,
        partition,
        row,
    )

    expected = analyze_replay_performance(
        ReplayPerformanceAnalysisInput(
            config=us8_performance_config(),
            observations=(),
        )
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.test_ids == (
        row.observation_id,
    )
    assert report.replay_observation_ids == ()
    assert report.incompatible_observation_ids == (
        row.observation_id,
    )
    assert report.performance == expected

    fold = report.folds[0]

    assert fold.status == "INSUFFICIENT_EVIDENCE"
    assert fold.replay_observation_ids == ()
    assert fold.incompatible_observation_ids == (
        row.observation_id,
    )


def test_profitable_compatible_subset_cannot_hide_incompatible_oos():
    incompatible = incompatible_candidate()

    plain = make_request()

    profitable = candidate(
        request=make_request(
            expiry=(
                plain.terms.valid_until
                + timedelta(days=1)
            ),
        )
    )

    assert (
        profitable.observation_id
        != incompatible.observation_id
    )
    assert (
        profitable.result.status
        == "REPLAYED"
    )
    assert (
        profitable.result.replay_result.state
        == "COMPLETED"
    )
    assert (
        profitable.result.replay_result.outcome
        == "WIN"
    )

    d = dataset(
        profitable,
        incompatible,
    )

    partition = partition_for(
        d,
        profitable,
        test_start=profitable.decision_at,
        evaluation_cutoff_at=(
            profitable.evidence_available_at
        ),
    )

    report = evaluate_oos(
        d,
        partition,
        profitable,
    )

    expected = direct_m7_1_report(
        profitable
    )

    assert set(report.test_ids) == {
        profitable.observation_id,
        incompatible.observation_id,
    }
    assert report.replay_observation_ids == (
        profitable.observation_id,
    )
    assert report.incompatible_observation_ids == (
        incompatible.observation_id,
    )
    assert report.performance == expected
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.folds[0].status == "INSUFFICIENT_EVIDENCE"


def test_future_completion_does_not_change_earlier_oos():
    early, late = versions()

    expected_dataset = dataset(
        early
    )

    actual_dataset = dataset(
        late,
        early,
    )

    expected_partition = partition_for(
        expected_dataset,
        early,
        test_start=early.decision_at,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    actual_partition = partition_for(
        actual_dataset,
        early,
        test_start=early.decision_at,
        evaluation_cutoff_at=early.evidence_available_at,
    )

    expected = evaluate_oos(
        expected_dataset,
        expected_partition,
        early,
    )

    actual = evaluate_oos(
        actual_dataset,
        actual_partition,
        early,
    )

    assert actual == expected


def test_scenario_rows_do_not_change_baseline_oos():
    early, late = versions()

    stress = candidate(
        scenario_id="stress",
        request=late.request,
    )

    baseline_dataset = dataset(
        early,
        late,
    )

    scenario_dataset = dataset(
        stress,
        late,
        early,
    )

    baseline_partition = partition_for(
        baseline_dataset,
        late,
        test_start=late.decision_at,
        evaluation_cutoff_at=late.evidence_available_at,
    )

    scenario_partition = partition_for(
        scenario_dataset,
        late,
        test_start=late.decision_at,
        evaluation_cutoff_at=late.evidence_available_at,
    )

    expected = evaluate_oos(
        baseline_dataset,
        baseline_partition,
        late,
    )

    actual = evaluate_oos(
        scenario_dataset,
        scenario_partition,
        late,
    )

    assert actual == expected


def test_oos_rejects_partition_corruption():
    early, late = versions()

    d = dataset(
        early,
        late,
    )

    partition = partition_for(
        d,
        late,
        test_start=late.decision_at,
        evaluation_cutoff_at=late.evidence_available_at,
    )

    fold = partition.folds[0]

    forged_fold = fold.model_copy(
        update={
            "test_ids": (),
            "incompatible_test_ids": (),
        }
    )

    forged = partition.model_copy(
        update={
            "folds": (
                forged_fold,
            ),
        }
    )

    with pytest.raises(
        ValueError,
        match="development partition mismatch",
    ):
        evaluate_us_replay_development_oos(
            d,
            forged,
            us8_performance_config(),
            research_built_at=(
                late.request.research_built_at
            ),
        )


def us8_bootstrap_config(**changes):
    args = dict(
        config_version="research-bootstrap-v1",
        seed=123,
        replications=40,
        confidence_level=Decimal(".9"),
        block_size=1,
    )
    args.update(changes)
    return BootstrapConfig(**args)


def us8_evidence_criteria(**changes):
    args = dict(
        config_version="research-evidence-criteria-v1",
        minimum_completed_development_trades=2,
        minimum_completed_holdout_trades=2,
        minimum_net_expectancy=Decimal(0),
        maximum_drawdown_fraction=Decimal(1),
        minimum_profit_factor=None,
        minimum_net_expectancy_lower_bound=None,
    )
    args.update(changes)
    return ResearchEvidenceCriteria(**args)


def completed_research_rows(count=2):
    base = make_request()
    rows = tuple(
        candidate(
            request=make_request(
                expiry=(
                    base.terms.valid_until
                    + timedelta(days=i)
                ),
            )
        )
        for i in range(1, count + 1)
    )

    assert len(
        {
            row.observation_id
            for row in rows
        }
    ) == count

    for row in rows:
        assert row.result.status == "REPLAYED"
        assert row.result.replay_result.state == "COMPLETED"
        assert row.result.replay_result.outcome == "WIN"

    return rows


def evidence_fixture(rows):
    d = dataset(*rows)
    anchor = rows[0]
    cutoff = max(
        row.evidence_available_at
        for row in rows
    )

    partition = partition_for(
        d,
        anchor,
        test_start=anchor.decision_at,
        evaluation_cutoff_at=cutoff,
    )

    return d, partition, anchor


def evaluate_evidence(
    d,
    partition,
    anchor,
    *,
    bootstrap_config=None,
    criteria=None,
):
    return evaluate_us_replay_development_evidence(
        d,
        partition,
        us8_performance_config(),
        bootstrap_config or us8_bootstrap_config(),
        criteria or us8_evidence_criteria(),
        research_built_at=(
            anchor.request.research_built_at
        ),
    )


def test_development_bootstrap_and_declared_criteria_pass():
    rows = completed_research_rows(2)
    d, partition, anchor = evidence_fixture(rows)

    report = evaluate_evidence(
        d,
        partition,
        anchor,
        criteria=us8_evidence_criteria(
            minimum_net_expectancy_lower_bound=Decimal(0),
        ),
    )

    assert report.status == "MEETS_DECLARED_CRITERIA"
    assert report.oos.status == "EVALUATED"
    assert report.bootstrap.sample_count == 2
    assert (
        report.bootstrap.net_expectancy.valid_replications
        == report.bootstrap.config.replications
    )
    assert report.bootstrap.net_expectancy.unavailable_reason is None

    estimate = (
        report.oos.performance.performance.summary.net_expectancy
    )

    assert report.bootstrap.net_expectancy.estimate == estimate
    assert report.bootstrap.net_expectancy.lower == estimate
    assert report.bootstrap.net_expectancy.upper == estimate
    assert all(
        check.passed is True
        for check in report.checks
    )


def test_declared_criteria_failure_is_not_insufficient():
    rows = completed_research_rows(2)
    d, partition, anchor = evidence_fixture(rows)

    report = evaluate_evidence(
        d,
        partition,
        anchor,
        criteria=us8_evidence_criteria(
            minimum_net_expectancy=Decimal(1000),
        ),
    )

    assert report.bootstrap.net_expectancy.lower is not None
    assert report.status == "FAILS_DECLARED_CRITERIA"

    net = next(
        check
        for check in report.checks
        if check.criterion == "net_expectancy"
    )

    assert net.passed is False
    assert net.unavailable_reason is None


def test_one_completed_trade_is_insufficient_evidence():
    rows = completed_research_rows(1)
    d, partition, anchor = evidence_fixture(rows)

    report = evaluate_evidence(
        d,
        partition,
        anchor,
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.bootstrap.sample_count == 1
    assert (
        report.bootstrap.net_expectancy.unavailable_reason
        == "ONE_COMPLETED"
    )
    assert report.bootstrap.net_expectancy.lower is None

    completed = next(
        check
        for check in report.checks
        if check.criterion == "completed_trades"
    )

    assert completed.passed is None
    assert (
        completed.unavailable_reason
        == "BELOW_DECLARED_SAMPLE_MINIMUM"
    )


def test_bootstrap_block_exceeding_sample_is_insufficient():
    rows = completed_research_rows(2)
    d, partition, anchor = evidence_fixture(rows)

    report = evaluate_evidence(
        d,
        partition,
        anchor,
        bootstrap_config=us8_bootstrap_config(
            block_size=3,
        ),
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.bootstrap.sample_count == 2
    assert (
        report.bootstrap.net_expectancy.unavailable_reason
        == "BLOCK_EXCEEDS_SAMPLE"
    )
    assert report.bootstrap.net_expectancy.lower is None


def test_bootstrap_completed_ids_follow_m7_1_realized_exit_order():
    rows = completed_research_rows(3)
    d, partition, anchor = evidence_fixture(
        tuple(reversed(rows))
    )

    report = evaluate_evidence(
        d,
        partition,
        anchor,
    )

    expected = tuple(
        row.observation_id
        for row in sorted(
            rows,
            key=lambda row: (
                row.result.replay_result.position.exit.known_at_utc,
                row.result.replay_result.position.exit.interval_start_utc,
                str(row.observation_id),
            ),
        )
    )

    assert report.bootstrap.completed_observation_ids == expected
    assert report.bootstrap.sample_count == len(expected)


def test_development_evidence_is_deterministic_off_caller_order():
    rows = completed_research_rows(3)

    first_dataset, first_partition, first_anchor = evidence_fixture(
        rows
    )
    second_dataset, second_partition, second_anchor = evidence_fixture(
        tuple(reversed(rows))
    )

    first = evaluate_evidence(
        first_dataset,
        first_partition,
        first_anchor,
    )
    second = evaluate_evidence(
        second_dataset,
        second_partition,
        second_anchor,
    )

    assert first == second
    assert first.identity == second.identity


def test_incompatible_oos_dominates_profitable_bootstrap():
    profitable = completed_research_rows(2)
    incompatible = incompatible_candidate()

    rows = profitable + (
        incompatible,
    )

    d, partition, anchor = evidence_fixture(
        rows
    )

    report = evaluate_evidence(
        d,
        partition,
        anchor,
    )

    assert report.oos.status == "INSUFFICIENT_EVIDENCE"
    assert (
        report.oos.incompatible_observation_ids
        == (incompatible.observation_id,)
    )
    assert report.bootstrap.sample_count == 2
    assert report.bootstrap.net_expectancy.lower is not None
    assert report.status == "INSUFFICIENT_EVIDENCE"


def test_bootstrap_replications_do_not_replay_each_sample(monkeypatch):
    import app.us.research_validation as validation

    rows = completed_research_rows(2)
    d, partition, anchor = evidence_fixture(rows)

    original = (
        validation.build_us_replay_performance_observation
    )
    calls = {"count": 0}

    def counted(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        validation,
        "build_us_replay_performance_observation",
        counted,
    )

    low = evaluate_evidence(
        d,
        partition,
        anchor,
        bootstrap_config=us8_bootstrap_config(
            replications=5,
        ),
    )

    low_calls = calls["count"]
    calls["count"] = 0

    high = evaluate_evidence(
        d,
        partition,
        anchor,
        bootstrap_config=us8_bootstrap_config(
            replications=50,
        ),
    )

    high_calls = calls["count"]

    assert low_calls == high_calls == 4
    assert (
        low.bootstrap.net_expectancy.valid_replications
        == 5
    )
    assert (
        high.bootstrap.net_expectancy.valid_replications
        == 50
    )
from app.us.research_validation import (
    evaluate_us_replay_development_scenarios,
    evaluate_us_replay_development_validation,
)
from test_m6_1_paper_replay import config as us8_execution_config
from test_us7c_b_paper_replay import (
    DAY as US8_DAY,
    ENTRY as US8_ENTRY,
    OPEN as US8_OPEN,
    TARGET as US8_TARGET,
    action as us8_action,
    make_request as us8_make_request,
)


def us8_scenario_row(
    scenario_id="stress",
    *,
    cfg=None,
    rows=(US8_ENTRY, US8_TARGET),
    actions=(),
    start=US8_OPEN,
):
    return candidate(
        scenario_id=scenario_id,
        request=us8_make_request(
            rows=rows,
            actions=actions,
            start=start,
            cfg=cfg,
        ),
    )


def us8_scenario_report(d, partition, row, scenario_ids=("stress",)):
    return evaluate_us_replay_development_scenarios(
        d,
        partition,
        us8_performance_config(),
        scenario_ids,
        research_built_at=row.request.research_built_at,
    )


def test_complete_slippage_scenario_uses_exact_oos_set_and_m7_1_economics():
    baseline = candidate(request=us8_make_request())
    stress = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
    )
    d = dataset(stress, baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    report = us8_scenario_report(
        d,
        partition,
        baseline,
    )
    coverage = report.scenarios[0]

    assert report.status == "EVALUATED"
    assert coverage.status == "EVALUATED"
    assert coverage.baseline_observation_ids == report.oos.test_ids
    assert coverage.replay_observation_ids == report.oos.test_ids
    assert coverage.unavailable_observation_ids == ()
    assert coverage.incompatible_observation_ids == ()
    assert coverage.baseline_incompatible_observation_ids == ()

    assert len(report.performance.slippage_sensitivity) == 1
    sensitivity = report.performance.slippage_sensitivity[0]
    assert sensitivity.scenario.scenario_id == "stress"
    assert tuple(
        row.replay_input.trade_plan.trade_plan_id
        for row in sensitivity.scenario.observations
    ) == report.oos.test_ids
    assert (
        sensitivity.total_net_pnl
        < report.oos.performance.performance.summary.total_net_pnl
    )


def test_missing_declared_scenario_is_incomplete_without_subset_evaluation():
    baseline = candidate(request=us8_make_request())
    d = dataset(baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    report = us8_scenario_report(
        d,
        partition,
        baseline,
    )
    coverage = report.scenarios[0]

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert coverage.status == "INCOMPLETE"
    assert coverage.replay_observation_ids == ()
    assert coverage.unavailable_observation_ids == report.oos.test_ids
    assert report.performance.slippage_sensitivity == ()


def test_future_only_scenario_version_cannot_repair_current_coverage():
    baseline = candidate(request=us8_make_request())
    future = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        rows=(US8_ENTRY, US8_TARGET, US8_TARGET),
    )
    assert future.observation_id == baseline.observation_id
    assert future.evidence_available_at > baseline.evidence_available_at

    d = dataset(future, baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    report = us8_scenario_report(
        d,
        partition,
        baseline,
    )
    coverage = report.scenarios[0]

    assert coverage.status == "INCOMPLETE"
    assert coverage.unavailable_observation_ids == report.oos.test_ids
    assert coverage.replay_observation_ids == ()
    assert report.performance.slippage_sensitivity == ()


def test_incompatible_scenario_is_explicit_and_never_subset_evaluated():
    baseline = candidate(request=us8_make_request())
    incompatible = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        actions=(
            us8_action(
                USCorporateActionType.SPLIT,
                US8_DAY + timedelta(days=1),
            ),
        ),
    )
    assert incompatible.observation_id == baseline.observation_id
    assert incompatible.result.status == "INCOMPATIBLE"

    d = dataset(incompatible, baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    report = us8_scenario_report(
        d,
        partition,
        baseline,
    )
    coverage = report.scenarios[0]

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert coverage.incompatible_observation_ids == report.oos.test_ids
    assert coverage.replay_observation_ids == ()
    assert report.performance.slippage_sensitivity == ()


def test_complete_scenario_non_slippage_change_is_rejected_by_m7_1():
    baseline = candidate(request=us8_make_request())
    unauthorized = us8_scenario_row(
        cfg=us8_execution_config(
            cost_bps_per_side=Decimal("1"),
        ),
    )
    d = dataset(unauthorized, baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    with pytest.raises(
        ValueError,
        match="unauthorized scenario config change",
    ):
        us8_scenario_report(
            d,
            partition,
            baseline,
        )


def test_scenario_selection_is_order_and_future_version_invariant():
    baseline = candidate(request=us8_make_request())
    mild = us8_scenario_row(
        "a-stress",
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("50"),
        ),
    )
    severe = us8_scenario_row(
        "z-stress",
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
    )
    future_mild = us8_scenario_row(
        "a-stress",
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("500"),
        ),
        rows=(US8_ENTRY, US8_TARGET, US8_TARGET),
    )

    base_dataset = dataset(
        severe,
        baseline,
        mild,
    )
    future_dataset = dataset(
        future_mild,
        mild,
        baseline,
        severe,
    )
    partition = partition_for(
        base_dataset,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    expected = evaluate_us_replay_development_scenarios(
        base_dataset,
        partition,
        us8_performance_config(),
        ("z-stress", "a-stress"),
        research_built_at=baseline.request.research_built_at,
    )
    actual = evaluate_us_replay_development_scenarios(
        future_dataset,
        partition,
        us8_performance_config(),
        ("a-stress", "z-stress"),
        research_built_at=baseline.request.research_built_at,
    )

    assert actual == expected
    assert actual.scenario_ids == ("a-stress", "z-stress")
    assert tuple(
        row.scenario.scenario_id
        for row in actual.performance.slippage_sensitivity
    ) == ("a-stress", "z-stress")


def test_missing_required_scenario_dominates_passing_baseline_criteria():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    assert first.observation_id != second.observation_id

    d = dataset(
        second,
        first,
    )
    partition = partition_for(
        d,
        second,
        test_start=first.decision_at,
        evaluation_cutoff_at=second.evidence_available_at,
    )
    criteria = ResearchEvidenceCriteria(
        config_version="research-evidence-criteria-v1",
        minimum_completed_development_trades=2,
        minimum_completed_holdout_trades=2,
        minimum_net_expectancy=Decimal("-1000"),
        maximum_drawdown_fraction=Decimal("1"),
        minimum_profit_factor=None,
        minimum_net_expectancy_lower_bound=None,
    )

    report = evaluate_us_replay_development_validation(
        d,
        partition,
        us8_performance_config(),
        us8_bootstrap_config(),
        criteria,
        ("stress",),
        research_built_at=second.request.research_built_at,
    )

    assert report.evidence.status == "MEETS_DECLARED_CRITERIA"
    assert report.scenarios.status == "INSUFFICIENT_EVIDENCE"
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert set(
        report.scenarios.scenarios[0].unavailable_observation_ids
    ) == set(report.evidence.oos.test_ids)
    assert report.scenarios.performance.slippage_sensitivity == ()


def test_baseline_incompatibility_has_explicit_scenario_coverage_bucket():
    incompatible_baseline = candidate(
        request=us8_make_request(
            actions=(
                us8_action(
                    USCorporateActionType.SPLIT,
                    US8_DAY + timedelta(days=1),
                ),
            ),
        )
    )
    assert incompatible_baseline.result.status == "INCOMPATIBLE"

    d = dataset(incompatible_baseline)
    partition = partition_for(
        d,
        incompatible_baseline,
        test_start=incompatible_baseline.decision_at,
        evaluation_cutoff_at=incompatible_baseline.evidence_available_at,
    )

    report = us8_scenario_report(
        d,
        partition,
        incompatible_baseline,
    )
    coverage = report.scenarios[0]

    assert report.oos.status == "INSUFFICIENT_EVIDENCE"
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert (
        coverage.baseline_incompatible_observation_ids
        == report.oos.test_ids
    )
    assert coverage.replay_observation_ids == ()
    assert coverage.unavailable_observation_ids == ()
    assert coverage.incompatible_observation_ids == ()
    assert report.performance.slippage_sensitivity == ()

# Stage 4B: frozen holdout validation contracts.

def us8_holdout_plan(*, start, end):
    from app.research.models import (
        FrozenHoldout,
        ResearchInterval,
        WalkForwardFold,
        WalkForwardPlan,
    )

    test_end = start - timedelta(seconds=1)
    test_start = test_end - timedelta(hours=1)

    return WalkForwardPlan(
        plan_id="us8-holdout-fixture-plan",
        folds=(
            WalkForwardFold(
                fold_id="pre-holdout",
                train=ResearchInterval(
                    start=test_start - timedelta(days=1),
                    end=test_start,
                ),
                test=ResearchInterval(
                    start=test_start,
                    end=test_end,
                ),
            ),
        ),
        holdout=FrozenHoldout(
            holdout_id="us8-frozen-holdout",
            interval=ResearchInterval(
                start=start,
                end=end,
            ),
        ),
    )


def us8_holdout_criteria(**changes):
    values = dict(
        config_version="research-evidence-criteria-v1",
        minimum_completed_development_trades=2,
        minimum_completed_holdout_trades=2,
        minimum_net_expectancy=Decimal("-1000"),
        maximum_drawdown_fraction=Decimal("1"),
        minimum_profit_factor=None,
        minimum_net_expectancy_lower_bound=Decimal("999999"),
    )
    values.update(changes)
    return ResearchEvidenceCriteria(**values)


def us8_holdout_report(d, plan, cutoff, row, *, criteria=None):
    from app.us.research_validation import evaluate_us_replay_frozen_holdout

    return evaluate_us_replay_frozen_holdout(
        d,
        plan,
        us8_performance_config(),
        criteria or us8_holdout_criteria(),
        evaluation_cutoff_at=cutoff,
        research_built_at=row.request.research_built_at,
    )


def us8_holdout_scenario_report(
    d,
    plan,
    cutoff,
    row,
    *,
    scenario_ids=("stress",),
    criteria=None,
):
    from app.us.research_validation import (
        evaluate_us_replay_frozen_holdout_scenarios,
    )

    return evaluate_us_replay_frozen_holdout_scenarios(
        d,
        plan,
        us8_performance_config(),
        criteria or us8_holdout_criteria(),
        scenario_ids,
        evaluation_cutoff_at=cutoff,
        research_built_at=row.request.research_built_at,
    )


def test_frozen_holdout_is_half_open_and_has_no_bootstrap():
    before = candidate(
        request=us8_make_request(
            start=US8_OPEN - timedelta(minutes=30),
        )
    )
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    at_end = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=60),
        )
    )

    assert len(
        {
            before.observation_id,
            first.observation_id,
            second.observation_id,
            at_end.observation_id,
        }
    ) == 4

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=at_end.decision_at,
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
        at_end.evidence_available_at,
    )

    report = us8_holdout_report(
        dataset(at_end, second, before, first),
        plan,
        cutoff,
        first,
    )

    assert report.observation_ids == (
        first.observation_id,
        second.observation_id,
    )
    assert report.replay_observation_ids == report.observation_ids
    assert report.incompatible_observation_ids == ()
    assert report.status == "MEETS_DECLARED_CRITERIA"
    assert report.performance.performance.summary.completed_count == 2
    assert "bootstrap" not in report.model_dump()


def test_frozen_holdout_rejects_partial_peek_before_interval_end():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    plan = us8_holdout_plan(
        start=first.decision_at,
        end=first.decision_at + timedelta(hours=1),
    )

    with pytest.raises(
        ValueError,
        match="holdout evaluation cutoff must be at or after holdout end",
    ):
        us8_holdout_report(
            dataset(first),
            plan,
            plan.holdout.interval.end - timedelta(microseconds=1),
            first,
        )


def test_holdout_uses_holdout_minimum_and_ignores_development_lower_bound():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )
    d = dataset(second, first)

    passing = us8_holdout_report(
        d,
        plan,
        cutoff,
        first,
    )

    assert passing.status == "MEETS_DECLARED_CRITERIA"
    assert tuple(
        check.criterion
        for check in passing.checks
    ) == (
        "completed_trades",
        "net_expectancy",
        "drawdown_fraction",
    )
    assert all(
        check.criterion != "net_expectancy_lower_bound"
        for check in passing.checks
    )

    insufficient = us8_holdout_report(
        d,
        plan,
        cutoff,
        first,
        criteria=us8_holdout_criteria(
            minimum_completed_holdout_trades=3,
        ),
    )

    assert insufficient.status == "INSUFFICIENT_EVIDENCE"
    completed = next(
        check
        for check in insufficient.checks
        if check.criterion == "completed_trades"
    )
    assert completed.actual == Decimal("2")
    assert completed.threshold == Decimal("3")
    assert completed.passed is None
    assert completed.unavailable_reason == "BELOW_DECLARED_SAMPLE_MINIMUM"


def test_holdout_baseline_incompatible_is_explicit_and_never_fake_m7_1_observation():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    incompatible = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=45),
            actions=(
                us8_action(
                    USCorporateActionType.SPLIT,
                    (US8_OPEN + timedelta(minutes=45)).date()
                    + timedelta(days=1),
                ),
            ),
        )
    )
    assert incompatible.result.status == "INCOMPATIBLE"

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=incompatible.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
        incompatible.evidence_available_at,
    )

    report = us8_holdout_report(
        dataset(incompatible, second, first),
        plan,
        cutoff,
        first,
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.observation_ids == (
        first.observation_id,
        second.observation_id,
        incompatible.observation_id,
    )
    assert report.replay_observation_ids == (
        first.observation_id,
        second.observation_id,
    )
    assert report.incompatible_observation_ids == (
        incompatible.observation_id,
    )
    assert report.performance.performance.summary.total_observations == 2
    assert all(check.passed is True for check in report.checks)


def test_complete_holdout_slippage_scenario_uses_exact_set_and_m7_1_economics():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    first_stress = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN,
    )
    second_stress = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN + timedelta(minutes=30),
    )

    assert first_stress.observation_id == first.observation_id
    assert second_stress.observation_id == second.observation_id

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )

    report = us8_holdout_scenario_report(
        dataset(
            second_stress,
            first,
            first_stress,
            second,
        ),
        plan,
        cutoff,
        first,
    )
    coverage = report.scenarios[0]

    assert report.holdout.status == "MEETS_DECLARED_CRITERIA"
    assert report.status == "EVALUATED"
    assert coverage.status == "EVALUATED"
    assert coverage.baseline_observation_ids == report.holdout.observation_ids
    assert coverage.replay_observation_ids == report.holdout.observation_ids
    assert coverage.unavailable_observation_ids == ()
    assert coverage.incompatible_observation_ids == ()
    assert coverage.baseline_incompatible_observation_ids == ()
    assert len(report.performance.slippage_sensitivity) == 1
    sensitivity = report.performance.slippage_sensitivity[0]
    assert sensitivity.scenario.scenario_id == "stress"
    assert tuple(
        row.replay_input.trade_plan.trade_plan_id
        for row in sensitivity.scenario.observations
    ) == report.holdout.observation_ids
    assert (
        sensitivity.total_net_pnl
        < report.holdout.performance.performance.summary.total_net_pnl
    )


def test_missing_holdout_required_scenario_blocks_profitable_subset_and_final_gate():
    from app.us.research_validation import (
        evaluate_us_replay_frozen_holdout_validation,
    )

    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    first_stress = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN,
    )

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )
    d = dataset(
        first_stress,
        second,
        first,
    )

    report = evaluate_us_replay_frozen_holdout_validation(
        d,
        plan,
        us8_performance_config(),
        us8_holdout_criteria(),
        ("stress",),
        evaluation_cutoff_at=cutoff,
        research_built_at=first.request.research_built_at,
    )
    coverage = report.scenarios.scenarios[0]

    assert report.evidence.status == "MEETS_DECLARED_CRITERIA"
    assert report.scenarios.status == "INSUFFICIENT_EVIDENCE"
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert coverage.status == "INCOMPLETE"
    assert coverage.replay_observation_ids == (
        first.observation_id,
    )
    assert coverage.unavailable_observation_ids == (
        second.observation_id,
    )
    assert report.scenarios.performance.slippage_sensitivity == ()


def test_future_only_holdout_scenario_version_cannot_repair_current_coverage():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    visible_first = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN,
    )
    future_second = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        rows=(US8_ENTRY, US8_TARGET, US8_TARGET),
        start=US8_OPEN + timedelta(minutes=30),
    )

    assert future_second.observation_id == second.observation_id
    assert future_second.evidence_available_at > second.evidence_available_at

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )

    base = us8_holdout_scenario_report(
        dataset(
            visible_first,
            second,
            first,
        ),
        plan,
        cutoff,
        first,
    )
    with_future = us8_holdout_scenario_report(
        dataset(
            future_second,
            first,
            visible_first,
            second,
        ),
        plan,
        cutoff,
        first,
    )

    assert with_future == base
    coverage = with_future.scenarios[0]
    assert coverage.status == "INCOMPLETE"
    assert coverage.replay_observation_ids == (
        first.observation_id,
    )
    assert coverage.unavailable_observation_ids == (
        second.observation_id,
    )
    assert with_future.performance.slippage_sensitivity == ()


def test_incompatible_holdout_scenario_is_explicit_and_never_subset_evaluated():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    first_stress = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN,
    )
    second_incompatible = us8_scenario_row(
        cfg=us8_execution_config(
            target_slippage_bps=Decimal("100"),
        ),
        start=US8_OPEN + timedelta(minutes=30),
        actions=(
            us8_action(
                USCorporateActionType.SPLIT,
                (US8_OPEN + timedelta(minutes=30)).date()
                + timedelta(days=1),
            ),
        ),
    )
    assert second_incompatible.result.status == "INCOMPATIBLE"

    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
        second_incompatible.evidence_available_at,
    )

    report = us8_holdout_scenario_report(
        dataset(
            second_incompatible,
            first_stress,
            second,
            first,
        ),
        plan,
        cutoff,
        first,
    )
    coverage = report.scenarios[0]

    assert report.holdout.status == "MEETS_DECLARED_CRITERIA"
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert coverage.status == "INCOMPLETE"
    assert coverage.replay_observation_ids == (
        first.observation_id,
    )
    assert coverage.incompatible_observation_ids == (
        second.observation_id,
    )
    assert coverage.unavailable_observation_ids == ()
    assert report.performance.slippage_sensitivity == ()


def test_holdout_report_is_caller_order_and_build_clock_invariant():
    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )

    from app.us.research_validation import evaluate_us_replay_frozen_holdout

    expected = evaluate_us_replay_frozen_holdout(
        dataset(second, first),
        plan,
        us8_performance_config(),
        us8_holdout_criteria(),
        evaluation_cutoff_at=cutoff,
        research_built_at=first.request.research_built_at,
    )
    actual = evaluate_us_replay_frozen_holdout(
        dataset(first, second),
        plan,
        us8_performance_config(),
        us8_holdout_criteria(),
        evaluation_cutoff_at=cutoff,
        research_built_at=first.request.research_built_at + timedelta(days=30),
    )

    assert actual == expected
    assert actual.identity == expected.identity


def test_development_and_holdout_apis_remain_explicitly_isolated(monkeypatch):
    import app.us.research_validation as validation

    baseline = candidate(
        request=us8_make_request()
    )
    d = dataset(baseline)
    partition = partition_for(
        d,
        baseline,
        test_start=baseline.decision_at,
        evaluation_cutoff_at=baseline.evidence_available_at,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("cross-boundary evaluator must not run implicitly")

    with monkeypatch.context() as m:
        m.setattr(
            validation,
            "evaluate_us_replay_frozen_holdout",
            forbidden,
        )
        validation.evaluate_us_replay_development_validation(
            d,
            partition,
            us8_performance_config(),
            us8_bootstrap_config(),
            us8_holdout_criteria(
                minimum_net_expectancy_lower_bound=None,
            ),
            (),
            research_built_at=baseline.request.research_built_at,
        )

    first = candidate(
        request=us8_make_request(
            start=US8_OPEN,
        )
    )
    second = candidate(
        request=us8_make_request(
            start=US8_OPEN + timedelta(minutes=30),
        )
    )
    plan = us8_holdout_plan(
        start=first.decision_at,
        end=second.decision_at + timedelta(seconds=1),
    )
    cutoff = max(
        first.evidence_available_at,
        second.evidence_available_at,
    )

    with monkeypatch.context() as m:
        m.setattr(
            validation,
            "evaluate_us_replay_development_validation",
            forbidden,
        )
        report = validation.evaluate_us_replay_frozen_holdout(
            dataset(second, first),
            plan,
            us8_performance_config(),
            us8_holdout_criteria(),
            evaluation_cutoff_at=cutoff,
            research_built_at=first.request.research_built_at,
        )
    assert report.status == "MEETS_DECLARED_CRITERIA"

# Stage 5B: final frozen US8 protocol/report identity and adversarial boundaries.

def us8_final_protocol(
    *,
    baseline_rows,
    plan,
    development_cutoff,
    holdout_cutoff,
    scenario_ids=(),
    criteria=None,
):
    from app.us.research_validation_models import USReplayFrozenResearchProtocol

    first = baseline_rows[0]
    return USReplayFrozenResearchProtocol(
        protocol_id="us8-final-protocol",
        strategy_id=first.strategy_id,
        strategy_version=first.strategy_version,
        plan=plan,
        performance_config=us8_performance_config(),
        bootstrap=us8_bootstrap_config(),
        criteria=criteria or us8_holdout_criteria(
            minimum_net_expectancy_lower_bound=None,
        ),
        scenario_ids=tuple(sorted(scenario_ids)),
        development_evaluation_cutoff_at=development_cutoff,
        holdout_evaluation_cutoff_at=holdout_cutoff,
    )


def us8_final_components(*, scenario_ids=(), include_scenarios=True, criteria=None):
    dev_start = US8_OPEN
    holdout_start = US8_OPEN + timedelta(days=2)
    starts = (
        dev_start,
        dev_start + timedelta(minutes=30),
        holdout_start,
        holdout_start + timedelta(minutes=30),
    )
    baseline = tuple(
        candidate(
            request=us8_make_request(
                rows=(US8_TARGET,),
                start=start,
            )
        )
        for start in starts
    )
    dev_first, dev_second, hold_first, hold_second = baseline

    plan = WalkForwardPlan(
        plan_id="us8-final-plan",
        folds=(
            WalkForwardFold(
                fold_id="final-dev",
                train=ResearchInterval(
                    start=dev_first.decision_at - timedelta(hours=2),
                    end=dev_first.decision_at - timedelta(hours=1),
                ),
                test=ResearchInterval(
                    start=dev_first.decision_at,
                    end=dev_second.decision_at + timedelta(seconds=1),
                ),
            ),
        ),
        holdout=FrozenHoldout(
            holdout_id="us8-final-holdout",
            interval=ResearchInterval(
                start=hold_first.decision_at,
                end=hold_second.decision_at + timedelta(seconds=1),
            ),
        ),
    )

    # Deliberately leave room for a later, still-visible evidence version so
    # report identity can be tested independently of the protocol identity.
    development_cutoff = (
        max(dev_first.evidence_available_at, dev_second.evidence_available_at)
        + timedelta(days=1)
    )
    holdout_cutoff = max(
        hold_first.evidence_available_at,
        hold_second.evidence_available_at,
    )

    rows = list(baseline)
    if include_scenarios:
        for scenario_id in scenario_ids:
            rows.extend(
                us8_scenario_row(
                    scenario_id,
                    cfg=us8_execution_config(
                        target_slippage_bps=Decimal("100"),
                    ),
                    rows=(US8_TARGET,),
                    start=start,
                )
                for start in starts
            )

    d = dataset(*rows)
    protocol = us8_final_protocol(
        baseline_rows=baseline,
        plan=plan,
        development_cutoff=development_cutoff,
        holdout_cutoff=holdout_cutoff,
        scenario_ids=scenario_ids,
        criteria=criteria,
    )
    return d, protocol, baseline, baseline[0].request.research_built_at


def us8_final_evaluate(d, protocol, *, research_built_at):
    from app.us.research_validation import evaluate_us_replay_research_validation

    return evaluate_us_replay_research_validation(
        d,
        protocol,
        research_built_at=research_built_at,
    )


def test_final_report_meets_and_binds_exact_selected_baseline_evidence():
    d, protocol, baseline, build = us8_final_components()

    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )

    assert report.status == "MEETS_DECLARED_CRITERIA"
    assert report.development.status == "MEETS_DECLARED_CRITERIA"
    assert report.holdout.status == "MEETS_DECLARED_CRITERIA"
    assert len(report.report_id) == 64
    assert {
        row.scope
        for row in report.selected_evidence
    } == {
        "DEVELOPMENT_BASELINE",
        "HOLDOUT_BASELINE",
    }
    assert {
        row.candidate_id
        for row in report.selected_evidence
    } == {
        row.identity
        for row in baseline
    }


def test_final_report_meets_with_complete_declared_slippage_scenario():
    d, protocol, _, build = us8_final_components(
        scenario_ids=("stress",),
    )

    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )

    assert report.status == "MEETS_DECLARED_CRITERIA"
    assert report.development.scenarios.status == "EVALUATED"
    assert report.holdout.scenarios.status == "EVALUATED"
    assert {
        row.scope
        for row in report.selected_evidence
        if row.scenario_id == "stress"
    } == {
        "DEVELOPMENT_SCENARIO",
        "HOLDOUT_SCENARIO",
    }


def test_final_report_is_caller_order_and_build_clock_invariant():
    d, protocol, _, build = us8_final_components(
        scenario_ids=("stress",),
    )

    expected = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )
    reordered = USReplayResearchDataset(
        strategy_id=d.strategy_id,
        strategy_version=d.strategy_version,
        candidates=tuple(reversed(d.candidates)),
    )
    actual = us8_final_evaluate(
        reordered,
        protocol,
        research_built_at=build + timedelta(days=30),
    )

    assert actual == expected
    assert actual.report_id == expected.report_id


def test_final_report_ignores_valid_future_candidate_after_frozen_cutoff():
    d, protocol, baseline, build = us8_final_components()
    dev_first = baseline[0]
    future = candidate(
        request=us8_make_request(
            rows=(US8_TARGET, US8_TARGET, US8_TARGET),
            start=dev_first.decision_at,
        )
    )
    assert future.observation_id == dev_first.observation_id
    assert (
        future.evidence_available_at
        > protocol.development_evaluation_cutoff_at
    )

    expected = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )
    with_future = dataset(*(d.candidates + (future,)))
    actual = us8_final_evaluate(
        with_future,
        protocol,
        research_built_at=build,
    )

    assert actual == expected
    assert actual.report_id == expected.report_id


def test_final_report_rejects_malformed_future_candidate_before_filtering():
    d, protocol, baseline, build = us8_final_components()
    dev_first = baseline[0]
    future = candidate(
        request=us8_make_request(
            rows=(US8_TARGET, US8_TARGET, US8_TARGET),
            start=dev_first.decision_at,
        )
    )
    forged_result = future.result.model_copy(
        update={
            "provenance": future.result.provenance.model_copy(
                update={"request_id": "0" * 64},
            ),
        },
    )
    forged_candidate = future.model_copy(
        update={"result": forged_result},
    )
    bypass = USReplayResearchDataset.model_construct(
        strategy_id=d.strategy_id,
        strategy_version=d.strategy_version,
        candidates=d.candidates + (forged_candidate,),
    )

    with pytest.raises(
        ValueError,
        match="candidate result/provenance mismatch|noncanonical",
    ):
        us8_final_evaluate(
            bypass,
            protocol,
            research_built_at=build,
        )


def test_final_report_id_changes_when_selected_snapshot_changes_under_same_protocol():
    d, protocol, baseline, build = us8_final_components()
    dev_first = baseline[0]
    later = candidate(
        request=us8_make_request(
            rows=(US8_TARGET, US8_TARGET),
            start=dev_first.decision_at,
        )
    )
    assert later.observation_id == dev_first.observation_id
    assert (
        later.evidence_available_at
        <= protocol.development_evaluation_cutoff_at
    )
    assert later.identity != dev_first.identity

    original = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )
    revised = us8_final_evaluate(
        dataset(*(d.candidates + (later,))),
        protocol,
        research_built_at=build,
    )

    assert revised.report_id != original.report_id
    assert later.identity in {
        row.candidate_id
        for row in revised.selected_evidence
    }
    assert dev_first.identity not in {
        row.candidate_id
        for row in revised.selected_evidence
        if (
            row.scope == "DEVELOPMENT_BASELINE"
            and row.observation_id == dev_first.observation_id
        )
    }


def test_final_report_rejects_forged_report_id():
    from app.us.research_validation_models import USReplayResearchValidationReport

    d, protocol, _, build = us8_final_components()
    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )

    with pytest.raises(
        ValueError,
        match="report_id mismatch",
    ):
        USReplayResearchValidationReport(
            protocol=report.protocol,
            development=report.development,
            holdout=report.holdout,
            selected_evidence=report.selected_evidence,
            status=report.status,
            report_id="0" * 64,
        )


def test_final_report_rejects_protocol_artifact_mismatch():
    from app.us.research_validation_models import (
        USReplayFrozenResearchProtocol,
        USReplayResearchValidationReport,
        us_replay_validation_report_id,
    )

    d, protocol, _, build = us8_final_components()
    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )
    changed_config = protocol.performance_config.model_copy(
        update={
            "starting_equity":
                protocol.performance_config.starting_equity + Decimal("1"),
        },
    )
    tampered_protocol = USReplayFrozenResearchProtocol(
        protocol_id=protocol.protocol_id,
        strategy_id=protocol.strategy_id,
        strategy_version=protocol.strategy_version,
        plan=protocol.plan,
        performance_config=changed_config,
        bootstrap=protocol.bootstrap,
        criteria=protocol.criteria,
        scenario_ids=protocol.scenario_ids,
        development_evaluation_cutoff_at=(
            protocol.development_evaluation_cutoff_at
        ),
        holdout_evaluation_cutoff_at=(
            protocol.holdout_evaluation_cutoff_at
        ),
    )
    forged_id = us_replay_validation_report_id(
        protocol=tampered_protocol,
        development=report.development,
        holdout=report.holdout,
        selected_evidence=report.selected_evidence,
        status=report.status,
    )

    with pytest.raises(
        ValueError,
        match="development performance config differs from frozen protocol",
    ):
        USReplayResearchValidationReport(
            protocol=tampered_protocol,
            development=report.development,
            holdout=report.holdout,
            selected_evidence=report.selected_evidence,
            status=report.status,
            report_id=forged_id,
        )


def test_final_report_missing_required_scenario_is_insufficient():
    d, protocol, _, build = us8_final_components(
        scenario_ids=("stress",),
        include_scenarios=False,
    )

    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )

    assert report.development.evidence.status == "MEETS_DECLARED_CRITERIA"
    assert report.holdout.evidence.status == "MEETS_DECLARED_CRITERIA"
    assert report.development.scenarios.status == "INSUFFICIENT_EVIDENCE"
    assert report.holdout.scenarios.status == "INSUFFICIENT_EVIDENCE"
    assert report.status == "INSUFFICIENT_EVIDENCE"


def test_final_report_evaluable_criterion_failure_is_failure_not_insufficient():
    criteria = us8_holdout_criteria(
        minimum_net_expectancy=Decimal("999999"),
        minimum_net_expectancy_lower_bound=None,
    )
    d, protocol, _, build = us8_final_components(
        criteria=criteria,
    )

    report = us8_final_evaluate(
        d,
        protocol,
        research_built_at=build,
    )

    assert report.development.status == "FAILS_DECLARED_CRITERIA"
    assert report.holdout.status == "FAILS_DECLARED_CRITERIA"
    assert report.status == "FAILS_DECLARED_CRITERIA"


def test_final_report_rejects_build_before_frozen_holdout_cutoff():
    d, protocol, _, _ = us8_final_components()

    with pytest.raises(
        ValueError,
        match="research_built_at cannot precede frozen holdout evaluation cutoff",
    ):
        us8_final_evaluate(
            d,
            protocol,
            research_built_at=(
                protocol.holdout_evaluation_cutoff_at
                - timedelta(seconds=1)
            ),
        )
