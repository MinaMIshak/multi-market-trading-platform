"""US8 immutable replay-aware research validation contracts.

This layer is additive.  It binds already-admitted US7C-B historical replay
truth for research evaluation without converting it to legacy M6/M7/M8
evidence and without performing acquisition, persistence, or live execution.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import computed_field, Field, field_validator, model_validator

from app.performance.models import PerformanceConfig
from app.performance.replay_models import ReplayPerformanceReport
from app.research.evidence import (
    BootstrapReport,
    CriterionCheck,
    ResearchEvidenceCriteria,
)
from app.research.models import WalkForwardFold, WalkForwardPlan
from app.strategies.contracts import Contract
from app.us.paper_replay import replay_us_research_trade
from app.us.paper_replay_models import (
    USPaperReplayRequest,
    USPaperReplayResult,
    canonical,
    exact_equal,
    exact_utc,
    semantic_hash,
)


def _identity(value, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"canonical nonblank {field_name} required")
    return value


def _candidate_sort_key(row: "USReplayResearchCandidate"):
    return (
        "" if row.scenario_id is None else row.scenario_id,
        row.decision_at,
        str(row.observation_id),
        row.evidence_available_at,
        row.request.identity,
        row.result.identity,
    )


class USReplayResearchCandidate(Contract):
    """One exact replay-evidence version for one trade/scenario.

    `scenario_id=None` is the research baseline.  A non-None scenario ID is a
    candidate execution-sensitivity version and is not admitted as a valid
    M7.1 slippage scenario merely by existing here; US8 evaluation performs
    the complete cross-scenario binding later.
    """

    schema_version: Literal[
        "us-replay-research-candidate-v1"
    ] = "us-replay-research-candidate-v1"

    scenario_id: str | None = None
    request: USPaperReplayRequest
    result: USPaperReplayResult

    @field_validator("scenario_id", mode="before")
    @classmethod
    def scenario_identity(cls, value):
        if value is None:
            return None
        return _identity(
            value,
            field_name="scenario_id",
        )

    @field_validator("request", "result", mode="before")
    @classmethod
    def canonical_replay_objects(cls, value, info):
        kind = (
            USPaperReplayRequest
            if info.field_name == "request"
            else USPaperReplayResult
        )
        return canonical(value, kind)

    @model_validator(mode="after")
    def replay_binding(self):
        # Re-run the complete US7C-B boundary.  This revalidates historical
        # listing/session/action/intraday evidence, planning/risk binding,
        # checkpoints, provenance, and the shared M6.1 replay result.
        expected = replay_us_research_trade(self.request)

        if not exact_equal(expected, self.result):
            raise ValueError(
                "US replay research candidate result/provenance mismatch"
            )

        if (
            self.result.status == "REPLAYED"
            and self.result.replay_result.state == "COMPLETED"
        ):
            exit_fill = self.result.replay_result.position.exit

            if exit_fill is None:
                raise ValueError(
                    "completed replay requires exact exit evidence"
                )

            if (
                exit_fill.known_at_utc
                > self.request.evidence_cutoff_at
            ):
                raise ValueError(
                    "completed label known after replay evidence cutoff"
                )

        return self

    @computed_field
    @property
    def observation_id(self) -> UUID:
        return self.request.plan.trade_plan_id

    @computed_field
    @property
    def decision_at(self) -> datetime:
        # Research admission time is the historical risk decision, not the
        # later execution-evidence cutoff and not the local research build.
        return self.request.risk_admission.risk_decision_at

    @computed_field
    @property
    def evidence_available_at(self) -> datetime:
        # Even if an economic exit occurred earlier, US8 may not use the
        # replay artifact before the exact execution-evidence cutoff that
        # produced and bound this historical replay version.
        return self.request.evidence_cutoff_at

    @computed_field
    @property
    def strategy_id(self) -> str:
        return self.request.plan.strategy_id

    @computed_field
    @property
    def strategy_version(self) -> str:
        return self.request.plan.strategy_version

    @computed_field
    @property
    def economic_label(
        self,
    ) -> Literal["WIN", "LOSS", "BREAKEVEN"] | None:
        if self.result.status != "REPLAYED":
            return None

        replay = self.result.replay_result

        if replay.state != "COMPLETED":
            return None

        net = replay.metrics.net_pnl

        if net > 0:
            return "WIN"
        if net < 0:
            return "LOSS"
        return "BREAKEVEN"

    @computed_field
    @property
    def label_available_at(self) -> datetime | None:
        if self.economic_label is None:
            return None
        return self.evidence_available_at

    @property
    def identity(self) -> str:
        return semantic_hash({
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "request_id": self.request.identity,
            "result_id": self.result.identity,
        })


class USReplayResearchDataset(Contract):
    """Canonical versioned replay candidates for one strategy version."""

    schema_version: Literal[
        "us-replay-research-dataset-v1"
    ] = "us-replay-research-dataset-v1"

    strategy_id: str
    strategy_version: str
    candidates: tuple[USReplayResearchCandidate, ...] = Field(
        default=(),
    )

    @field_validator(
        "strategy_id",
        "strategy_version",
        mode="before",
    )
    @classmethod
    def strategy_identity(cls, value, info):
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator("candidates", mode="before")
    @classmethod
    def canonical_candidates(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplayResearchCandidate required"
            )

        rows = tuple(
            canonical(
                row,
                USReplayResearchCandidate,
            )
            for row in value
        )

        return tuple(
            sorted(
                rows,
                key=_candidate_sort_key,
            )
        )

    @model_validator(mode="after")
    def bindings(self):
        seen = set()

        for row in self.candidates:
            if (
                row.strategy_id,
                row.strategy_version,
            ) != (
                self.strategy_id,
                self.strategy_version,
            ):
                raise ValueError(
                    "mixed replay research strategy identity/version"
                )

            version_key = (
                row.scenario_id,
                row.observation_id,
                row.evidence_available_at,
            )

            if version_key in seen:
                raise ValueError(
                    "duplicate replay research candidate version"
                )

            seen.add(version_key)

        return self


def _uuid_tuple(value, *, field_name: str):
    if type(value) is not tuple:
        raise ValueError(
            f"canonical UUID tuple required for {field_name}"
        )

    if any(type(item) is not UUID for item in value):
        raise ValueError(
            f"canonical UUID tuple required for {field_name}"
        )

    if len(set(value)) != len(value):
        raise ValueError(
            f"duplicate UUID in {field_name}"
        )

    return value


class USReplayFoldPartition(Contract):
    """One replay-aware development fold with explicit exclusion semantics."""

    schema_version: Literal[
        "us-replay-fold-partition-v1"
    ] = "us-replay-fold-partition-v1"

    fold: WalkForwardFold

    training_ids: tuple[UUID, ...]
    purged_training_ids: tuple[UUID, ...]
    unavailable_training_ids: tuple[UUID, ...]
    incompatible_training_ids: tuple[UUID, ...]

    test_ids: tuple[UUID, ...]
    incompatible_test_ids: tuple[UUID, ...]

    @field_validator("fold", mode="before")
    @classmethod
    def canonical_fold(cls, value):
        return canonical(
            value,
            WalkForwardFold,
        )

    @field_validator(
        "training_ids",
        "purged_training_ids",
        "unavailable_training_ids",
        "incompatible_training_ids",
        "test_ids",
        "incompatible_test_ids",
        mode="before",
    )
    @classmethod
    def canonical_ids(cls, value, info):
        return _uuid_tuple(
            value,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def membership(self):
        training_groups = (
            set(self.training_ids),
            set(self.purged_training_ids),
            set(self.unavailable_training_ids),
            set(self.incompatible_training_ids),
        )

        for i, left in enumerate(training_groups):
            for right in training_groups[i + 1:]:
                if left & right:
                    raise ValueError(
                        "training partition categories must be disjoint"
                    )

        training_union = set().union(
            *training_groups
        )

        if training_union & set(self.test_ids):
            raise ValueError(
                "training and test membership must be disjoint"
            )

        if not set(
            self.incompatible_test_ids
        ).issubset(
            set(self.test_ids)
        ):
            raise ValueError(
                "incompatible_test_ids must be a subset of test_ids"
            )

        return self


class USReplayDevelopmentPartition(Contract):
    """Replay-aware walk-forward membership at one explicit evidence cutoff."""

    schema_version: Literal[
        "us-replay-development-partition-v1"
    ] = "us-replay-development-partition-v1"

    strategy_id: str
    strategy_version: str
    plan: WalkForwardPlan
    evaluation_cutoff_at: datetime
    folds: tuple[USReplayFoldPartition, ...]

    @field_validator(
        "strategy_id",
        "strategy_version",
        mode="before",
    )
    @classmethod
    def strategy_identity(cls, value, info):
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator("plan", mode="before")
    @classmethod
    def canonical_plan(cls, value):
        return canonical(
            value,
            WalkForwardPlan,
        )

    @field_validator(
        "evaluation_cutoff_at",
        mode="before",
    )
    @classmethod
    def canonical_cutoff(cls, value):
        return exact_utc(value)

    @field_validator("folds", mode="before")
    @classmethod
    def canonical_folds(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplayFoldPartition required"
            )

        return tuple(
            canonical(
                row,
                USReplayFoldPartition,
            )
            for row in value
        )

    @model_validator(mode="after")
    def exact_plan_binding(self):
        if len(self.folds) != len(self.plan.folds):
            raise ValueError(
                "partition fold count does not match walk-forward plan"
            )

        for supplied, expected in zip(
            self.folds,
            self.plan.folds,
        ):
            if not exact_equal(
                supplied.fold,
                expected,
            ):
                raise ValueError(
                    "partition fold differs from walk-forward plan"
                )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )

class USReplayFoldOOSReport(Contract):
    """One fold's exact M7.1 replay economics plus explicit US exclusions."""

    schema_version: Literal[
        "us-replay-fold-oos-report-v1"
    ] = "us-replay-fold-oos-report-v1"

    fold_id: str
    test_ids: tuple[UUID, ...]
    replay_observation_ids: tuple[UUID, ...]
    incompatible_observation_ids: tuple[UUID, ...]
    performance: ReplayPerformanceReport
    status: Literal["EVALUATED", "INSUFFICIENT_EVIDENCE"]

    @field_validator("fold_id", mode="before")
    @classmethod
    def canonical_fold_id(cls, value):
        return _identity(
            value,
            field_name="fold_id",
        )

    @field_validator(
        "test_ids",
        "replay_observation_ids",
        "incompatible_observation_ids",
        mode="before",
    )
    @classmethod
    def canonical_ids(cls, value, info):
        return _uuid_tuple(
            value,
            field_name=info.field_name,
        )

    @field_validator("performance", mode="before")
    @classmethod
    def canonical_performance(cls, value):
        return canonical(
            value,
            ReplayPerformanceReport,
        )

    @model_validator(mode="after")
    def exact_coverage(self):
        replay = set(
            self.replay_observation_ids
        )
        incompatible = set(
            self.incompatible_observation_ids
        )
        tests = set(
            self.test_ids
        )

        if replay & incompatible:
            raise ValueError(
                "replay and incompatible OOS IDs must be disjoint"
            )

        if replay | incompatible != tests:
            raise ValueError(
                "OOS replay/incompatible IDs must exactly cover test_ids"
            )

        expected_status = (
            "INSUFFICIENT_EVIDENCE"
            if incompatible
            else "EVALUATED"
        )

        if self.status != expected_status:
            raise ValueError(
                "OOS fold status does not match incompatibility"
            )

        return self


class USReplayDevelopmentOOSReport(Contract):
    """Development OOS replay report without legacy M8 observation conversion."""

    schema_version: Literal[
        "us-replay-development-oos-report-v1"
    ] = "us-replay-development-oos-report-v1"

    strategy_id: str
    strategy_version: str
    partition: USReplayDevelopmentPartition
    performance_config: PerformanceConfig
    folds: tuple[USReplayFoldOOSReport, ...]
    fold_count: int
    test_ids: tuple[UUID, ...]
    replay_observation_ids: tuple[UUID, ...]
    incompatible_observation_ids: tuple[UUID, ...]
    performance: ReplayPerformanceReport
    status: Literal["EVALUATED", "INSUFFICIENT_EVIDENCE"]

    @field_validator(
        "strategy_id",
        "strategy_version",
        mode="before",
    )
    @classmethod
    def strategy_identity(cls, value, info):
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator("partition", mode="before")
    @classmethod
    def canonical_partition(cls, value):
        return canonical(
            value,
            USReplayDevelopmentPartition,
        )

    @field_validator(
        "performance_config",
        mode="before",
    )
    @classmethod
    def canonical_performance_config(cls, value):
        return canonical(
            value,
            PerformanceConfig,
        )

    @field_validator("folds", mode="before")
    @classmethod
    def canonical_folds(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplayFoldOOSReport required"
            )

        return tuple(
            canonical(
                row,
                USReplayFoldOOSReport,
            )
            for row in value
        )

    @field_validator(
        "test_ids",
        "replay_observation_ids",
        "incompatible_observation_ids",
        mode="before",
    )
    @classmethod
    def canonical_ids(cls, value, info):
        return _uuid_tuple(
            value,
            field_name=info.field_name,
        )

    @field_validator("fold_count", mode="before")
    @classmethod
    def exact_fold_count(cls, value):
        if type(value) is not int:
            raise ValueError(
                "strict Python integer required for fold_count"
            )

        if value < 0:
            raise ValueError(
                "fold_count must be nonnegative"
            )

        return value

    @field_validator("performance", mode="before")
    @classmethod
    def canonical_performance(cls, value):
        return canonical(
            value,
            ReplayPerformanceReport,
        )

    @model_validator(mode="after")
    def exact_coverage(self):
        if (
            self.strategy_id
            != self.partition.strategy_id
            or self.strategy_version
            != self.partition.strategy_version
        ):
            raise ValueError(
                "OOS strategy identity differs from partition"
            )

        if self.fold_count != len(self.folds):
            raise ValueError(
                "fold_count does not match OOS folds"
            )

        if len(self.folds) != len(
            self.partition.folds
        ):
            raise ValueError(
                "OOS fold count differs from partition"
            )

        for report, partition in zip(
            self.folds,
            self.partition.folds,
        ):
            if (
                report.fold_id
                != partition.fold.fold_id
            ):
                raise ValueError(
                    "OOS fold identity differs from partition"
                )

            if (
                report.test_ids
                != partition.test_ids
            ):
                raise ValueError(
                    "OOS fold test_ids differ from partition"
                )

        test_ids = tuple(
            item
            for fold in self.folds
            for item in fold.test_ids
        )
        replay_ids = tuple(
            item
            for fold in self.folds
            for item in fold.replay_observation_ids
        )
        incompatible_ids = tuple(
            item
            for fold in self.folds
            for item in fold.incompatible_observation_ids
        )

        if len(set(test_ids)) != len(test_ids):
            raise ValueError(
                "duplicate OOS trade across folds"
            )

        if self.test_ids != test_ids:
            raise ValueError(
                "aggregate OOS test_ids mismatch"
            )

        if self.replay_observation_ids != replay_ids:
            raise ValueError(
                "aggregate OOS replay IDs mismatch"
            )

        if (
            self.incompatible_observation_ids
            != incompatible_ids
        ):
            raise ValueError(
                "aggregate OOS incompatible IDs mismatch"
            )

        expected_status = (
            "INSUFFICIENT_EVIDENCE"
            if incompatible_ids
            else "EVALUATED"
        )

        if self.status != expected_status:
            raise ValueError(
                "development OOS status does not match incompatibility"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )

class USReplayDevelopmentEvidenceReport(Contract):
    """Declared-criteria development evidence over exact US replay OOS economics."""

    schema_version: Literal[
        "us-replay-development-evidence-report-v1"
    ] = "us-replay-development-evidence-report-v1"

    oos: USReplayDevelopmentOOSReport
    bootstrap: BootstrapReport
    criteria: ResearchEvidenceCriteria
    checks: tuple[CriterionCheck, ...]
    status: Literal[
        "MEETS_DECLARED_CRITERIA",
        "FAILS_DECLARED_CRITERIA",
        "INSUFFICIENT_EVIDENCE",
    ]

    @field_validator("oos", mode="before")
    @classmethod
    def canonical_oos(cls, value):
        return canonical(
            value,
            USReplayDevelopmentOOSReport,
        )

    @field_validator("bootstrap", mode="before")
    @classmethod
    def canonical_bootstrap(cls, value):
        return canonical(
            value,
            BootstrapReport,
        )

    @field_validator("criteria", mode="before")
    @classmethod
    def canonical_criteria(cls, value):
        return canonical(
            value,
            ResearchEvidenceCriteria,
        )

    @field_validator("checks", mode="before")
    @classmethod
    def canonical_checks(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical CriterionCheck required"
            )

        return tuple(
            canonical(
                row,
                CriterionCheck,
            )
            for row in value
        )

    @model_validator(mode="after")
    def exact_status(self):
        completed_ids = set(
            self.bootstrap.completed_observation_ids
        )
        replay_ids = set(
            self.oos.replay_observation_ids
        )

        if not completed_ids.issubset(
            replay_ids
        ):
            raise ValueError(
                "bootstrap completed IDs must be replay OOS IDs"
            )

        if (
            self.bootstrap.sample_count
            != len(self.bootstrap.completed_observation_ids)
        ):
            raise ValueError(
                "bootstrap sample_count differs from completed IDs"
            )

        if (
            self.oos.status
            == "INSUFFICIENT_EVIDENCE"
            or self.bootstrap.net_expectancy.lower
            is None
            or any(
                check.passed is None
                for check in self.checks
            )
        ):
            expected = "INSUFFICIENT_EVIDENCE"

        elif any(
            not check.passed
            for check in self.checks
        ):
            expected = "FAILS_DECLARED_CRITERIA"

        else:
            expected = "MEETS_DECLARED_CRITERIA"

        if self.status != expected:
            raise ValueError(
                "development evidence status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )

class USReplayScenarioCoverage(Contract):
    """Exact as-of coverage of one declared US replay slippage scenario."""

    schema_version: Literal[
        "us-replay-scenario-coverage-v1"
    ] = "us-replay-scenario-coverage-v1"

    scenario_id: str
    baseline_observation_ids: tuple[UUID, ...]
    replay_observation_ids: tuple[UUID, ...]
    unavailable_observation_ids: tuple[UUID, ...]
    incompatible_observation_ids: tuple[UUID, ...]
    baseline_incompatible_observation_ids: tuple[UUID, ...]
    status: Literal["EVALUATED", "INCOMPLETE"]

    @field_validator("scenario_id", mode="before")
    @classmethod
    def canonical_scenario_id(cls, value):
        return _identity(
            value,
            field_name="scenario_id",
        )

    @field_validator(
        "baseline_observation_ids",
        "replay_observation_ids",
        "unavailable_observation_ids",
        "incompatible_observation_ids",
        "baseline_incompatible_observation_ids",
        mode="before",
    )
    @classmethod
    def canonical_ids(cls, value, info):
        return _uuid_tuple(
            value,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def exact_coverage(self):
        baseline = set(
            self.baseline_observation_ids
        )
        buckets = (
            set(self.replay_observation_ids),
            set(self.unavailable_observation_ids),
            set(self.incompatible_observation_ids),
            set(self.baseline_incompatible_observation_ids),
        )

        for index, left in enumerate(buckets):
            for right in buckets[index + 1:]:
                if left & right:
                    raise ValueError(
                        "scenario coverage buckets must be disjoint"
                    )

        covered = set().union(*buckets)

        if covered != baseline:
            raise ValueError(
                "scenario coverage must exactly cover baseline OOS IDs"
            )

        expected = (
            "EVALUATED"
            if (
                tuple(self.replay_observation_ids)
                == tuple(self.baseline_observation_ids)
                and not self.unavailable_observation_ids
                and not self.incompatible_observation_ids
                and not self.baseline_incompatible_observation_ids
            )
            else "INCOMPLETE"
        )

        if self.status != expected:
            raise ValueError(
                "scenario coverage status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )


class USReplayDevelopmentScenarioReport(Contract):
    """Exact-set US scenario coverage with economics owned only by M7.1."""

    schema_version: Literal[
        "us-replay-development-scenario-report-v1"
    ] = "us-replay-development-scenario-report-v1"

    oos: USReplayDevelopmentOOSReport
    scenario_ids: tuple[str, ...]
    scenarios: tuple[USReplayScenarioCoverage, ...]
    performance: ReplayPerformanceReport
    status: Literal["EVALUATED", "INSUFFICIENT_EVIDENCE"]

    @field_validator("oos", mode="before")
    @classmethod
    def canonical_oos(cls, value):
        return canonical(
            value,
            USReplayDevelopmentOOSReport,
        )

    @field_validator("scenario_ids", mode="before")
    @classmethod
    def canonical_scenario_ids(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical scenario IDs required"
            )

        rows = tuple(
            _identity(
                row,
                field_name="scenario_id",
            )
            for row in value
        )

        if len(set(rows)) != len(rows):
            raise ValueError(
                "duplicate scenario_id"
            )

        if rows != tuple(sorted(rows)):
            raise ValueError(
                "scenario IDs must use canonical sorted order"
            )

        return rows

    @field_validator("scenarios", mode="before")
    @classmethod
    def canonical_scenarios(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplayScenarioCoverage required"
            )

        return tuple(
            canonical(
                row,
                USReplayScenarioCoverage,
            )
            for row in value
        )

    @field_validator("performance", mode="before")
    @classmethod
    def canonical_performance(cls, value):
        return canonical(
            value,
            ReplayPerformanceReport,
        )

    @model_validator(mode="after")
    def exact_binding(self):
        coverage_ids = tuple(
            row.scenario_id
            for row in self.scenarios
        )

        if coverage_ids != self.scenario_ids:
            raise ValueError(
                "scenario coverage IDs differ from declaration"
            )

        if (
            self.performance.performance
            != self.oos.performance.performance
        ):
            raise ValueError(
                "scenario report baseline economics differ from OOS"
            )

        if self.performance.baseline_scenario_id is not None:
            raise ValueError(
                "US8 scenario report must not invent an M7.1 baseline scenario"
            )

        evaluated_ids = tuple(
            row.scenario_id
            for row in self.scenarios
            if row.status == "EVALUATED"
        )

        sensitivity_ids = tuple(
            row.scenario.scenario_id
            for row in self.performance.slippage_sensitivity
        )

        if sensitivity_ids != evaluated_ids:
            raise ValueError(
                "M7.1 sensitivity set differs from complete US scenarios"
            )

        expected = (
            "INSUFFICIENT_EVIDENCE"
            if (
                self.oos.status == "INSUFFICIENT_EVIDENCE"
                or any(
                    row.status == "INCOMPLETE"
                    for row in self.scenarios
                )
            )
            else "EVALUATED"
        )

        if self.status != expected:
            raise ValueError(
                "development scenario report status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )


class USReplayDevelopmentValidationReport(Contract):
    """Development evidence plus declared scenario completeness as one US8 gate."""

    schema_version: Literal[
        "us-replay-development-validation-report-v1"
    ] = "us-replay-development-validation-report-v1"

    evidence: USReplayDevelopmentEvidenceReport
    scenarios: USReplayDevelopmentScenarioReport
    status: Literal[
        "MEETS_DECLARED_CRITERIA",
        "FAILS_DECLARED_CRITERIA",
        "INSUFFICIENT_EVIDENCE",
    ]

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return canonical(
            value,
            USReplayDevelopmentEvidenceReport,
        )

    @field_validator("scenarios", mode="before")
    @classmethod
    def canonical_scenario_report(cls, value):
        return canonical(
            value,
            USReplayDevelopmentScenarioReport,
        )

    @model_validator(mode="after")
    def exact_status(self):
        if self.scenarios.oos != self.evidence.oos:
            raise ValueError(
                "scenario OOS differs from development evidence OOS"
            )

        expected = (
            "INSUFFICIENT_EVIDENCE"
            if (
                self.evidence.status == "INSUFFICIENT_EVIDENCE"
                or self.scenarios.status == "INSUFFICIENT_EVIDENCE"
            )
            else self.evidence.status
        )

        if self.status != expected:
            raise ValueError(
                "development validation status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import field_validator, model_validator

from app.performance.models import PerformanceConfig
from app.performance.replay_models import ReplayPerformanceReport
from app.research.evidence import CriterionCheck, ResearchEvidenceCriteria
from app.research.models import FrozenHoldout


class USReplayFrozenHoldoutReport(Contract):
    """Frozen US replay holdout evidence; no bootstrap and no scenario economics."""

    schema_version: Literal[
        "us-replay-frozen-holdout-report-v1"
    ] = "us-replay-frozen-holdout-report-v1"

    strategy_id: str
    strategy_version: str
    holdout: FrozenHoldout
    evaluation_cutoff_at: datetime
    performance_config: PerformanceConfig
    observation_ids: tuple[UUID, ...]
    replay_observation_ids: tuple[UUID, ...]
    incompatible_observation_ids: tuple[UUID, ...]
    performance: ReplayPerformanceReport
    criteria: ResearchEvidenceCriteria
    checks: tuple[CriterionCheck, ...]
    status: Literal[
        "MEETS_DECLARED_CRITERIA",
        "FAILS_DECLARED_CRITERIA",
        "INSUFFICIENT_EVIDENCE",
    ]

    @field_validator(
        "strategy_id",
        "strategy_version",
        mode="before",
    )
    @classmethod
    def strategy_identity(cls, value, info):
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator("holdout", mode="before")
    @classmethod
    def canonical_holdout(cls, value):
        return canonical(
            value,
            FrozenHoldout,
        )

    @field_validator("evaluation_cutoff_at", mode="before")
    @classmethod
    def canonical_cutoff(cls, value):
        if (
            type(value) is not datetime
            or value.tzinfo is not timezone.utc
        ):
            raise ValueError(
                "exact datetime.timezone.utc required"
            )
        return value

    @field_validator("performance_config", mode="before")
    @classmethod
    def canonical_performance_config(cls, value):
        return canonical(
            value,
            PerformanceConfig,
        )

    @field_validator(
        "observation_ids",
        "replay_observation_ids",
        "incompatible_observation_ids",
        mode="before",
    )
    @classmethod
    def canonical_ids(cls, value, info):
        return _uuid_tuple(
            value,
            field_name=info.field_name,
        )

    @field_validator("performance", mode="before")
    @classmethod
    def canonical_performance(cls, value):
        return canonical(
            value,
            ReplayPerformanceReport,
        )

    @field_validator("criteria", mode="before")
    @classmethod
    def canonical_criteria(cls, value):
        return canonical(
            value,
            ResearchEvidenceCriteria,
        )

    @field_validator("checks", mode="before")
    @classmethod
    def canonical_checks(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical CriterionCheck required"
            )
        return tuple(
            canonical(
                row,
                CriterionCheck,
            )
            for row in value
        )

    @model_validator(mode="after")
    def exact_binding(self):
        if self.evaluation_cutoff_at < self.holdout.interval.end:
            raise ValueError(
                "holdout evaluation cutoff must be at or after holdout end"
            )

        replay = set(
            self.replay_observation_ids
        )
        incompatible = set(
            self.incompatible_observation_ids
        )
        admitted = set(
            self.observation_ids
        )

        if replay & incompatible:
            raise ValueError(
                "holdout replay and incompatible IDs must be disjoint"
            )

        if replay | incompatible != admitted:
            raise ValueError(
                "holdout replay/incompatible IDs must exactly cover observation_ids"
            )

        if self.performance.slippage_sensitivity:
            raise ValueError(
                "baseline holdout report cannot contain slippage sensitivity"
            )

        if self.performance.baseline_scenario_id is not None:
            raise ValueError(
                "baseline holdout report cannot declare M7.1 scenario baseline"
            )

        if (
            self.incompatible_observation_ids
            or any(
                check.passed is None
                for check in self.checks
            )
        ):
            expected = "INSUFFICIENT_EVIDENCE"
        elif any(
            not check.passed
            for check in self.checks
        ):
            expected = "FAILS_DECLARED_CRITERIA"
        else:
            expected = "MEETS_DECLARED_CRITERIA"

        if self.status != expected:
            raise ValueError(
                "frozen holdout evidence status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )


class USReplayFrozenHoldoutScenarioReport(Contract):
    """Exact-set holdout scenario coverage; M7.1 remains economics authority."""

    schema_version: Literal[
        "us-replay-frozen-holdout-scenario-report-v1"
    ] = "us-replay-frozen-holdout-scenario-report-v1"

    holdout: USReplayFrozenHoldoutReport
    scenario_ids: tuple[str, ...]
    scenarios: tuple[USReplayScenarioCoverage, ...]
    performance: ReplayPerformanceReport
    status: Literal["EVALUATED", "INSUFFICIENT_EVIDENCE"]

    @field_validator("holdout", mode="before")
    @classmethod
    def canonical_holdout_report(cls, value):
        return canonical(
            value,
            USReplayFrozenHoldoutReport,
        )

    @field_validator("scenario_ids", mode="before")
    @classmethod
    def canonical_scenario_ids(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical scenario IDs required"
            )

        rows = tuple(
            _identity(
                row,
                field_name="scenario_id",
            )
            for row in value
        )

        if len(set(rows)) != len(rows):
            raise ValueError(
                "duplicate scenario_id"
            )

        if rows != tuple(sorted(rows)):
            raise ValueError(
                "scenario IDs must use canonical sorted order"
            )

        return rows

    @field_validator("scenarios", mode="before")
    @classmethod
    def canonical_scenarios(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplayScenarioCoverage required"
            )
        return tuple(
            canonical(
                row,
                USReplayScenarioCoverage,
            )
            for row in value
        )

    @field_validator("performance", mode="before")
    @classmethod
    def canonical_performance(cls, value):
        return canonical(
            value,
            ReplayPerformanceReport,
        )

    @model_validator(mode="after")
    def exact_binding(self):
        coverage_ids = tuple(
            row.scenario_id
            for row in self.scenarios
        )

        if coverage_ids != self.scenario_ids:
            raise ValueError(
                "holdout scenario coverage IDs differ from declaration"
            )

        for row in self.scenarios:
            if (
                row.baseline_observation_ids
                != self.holdout.observation_ids
            ):
                raise ValueError(
                    "holdout scenario baseline set mismatch"
                )

        if (
            self.performance.performance
            != self.holdout.performance.performance
        ):
            raise ValueError(
                "holdout scenario baseline economics differ from holdout"
            )

        if self.performance.baseline_scenario_id is not None:
            raise ValueError(
                "US8 holdout scenario report must not invent an M7.1 baseline scenario"
            )

        evaluated_ids = tuple(
            row.scenario_id
            for row in self.scenarios
            if row.status == "EVALUATED"
        )
        sensitivity_ids = tuple(
            row.scenario.scenario_id
            for row in self.performance.slippage_sensitivity
        )

        if sensitivity_ids != evaluated_ids:
            raise ValueError(
                "M7.1 holdout sensitivity set differs from complete US scenarios"
            )

        expected = (
            "INSUFFICIENT_EVIDENCE"
            if (
                self.holdout.status
                == "INSUFFICIENT_EVIDENCE"
                or any(
                    row.status == "INCOMPLETE"
                    for row in self.scenarios
                )
            )
            else "EVALUATED"
        )

        if self.status != expected:
            raise ValueError(
                "frozen holdout scenario report status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )


class USReplayFrozenHoldoutValidationReport(Contract):
    """Frozen holdout declared criteria plus exact required scenario completeness."""

    schema_version: Literal[
        "us-replay-frozen-holdout-validation-report-v1"
    ] = "us-replay-frozen-holdout-validation-report-v1"

    evidence: USReplayFrozenHoldoutReport
    scenarios: USReplayFrozenHoldoutScenarioReport
    status: Literal[
        "MEETS_DECLARED_CRITERIA",
        "FAILS_DECLARED_CRITERIA",
        "INSUFFICIENT_EVIDENCE",
    ]

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return canonical(
            value,
            USReplayFrozenHoldoutReport,
        )

    @field_validator("scenarios", mode="before")
    @classmethod
    def canonical_scenario_report(cls, value):
        return canonical(
            value,
            USReplayFrozenHoldoutScenarioReport,
        )

    @model_validator(mode="after")
    def exact_status(self):
        if self.scenarios.holdout != self.evidence:
            raise ValueError(
                "holdout scenario evidence differs from frozen holdout evidence"
            )

        expected = (
            "INSUFFICIENT_EVIDENCE"
            if (
                self.evidence.status
                == "INSUFFICIENT_EVIDENCE"
                or self.scenarios.status
                == "INSUFFICIENT_EVIDENCE"
            )
            else self.evidence.status
        )

        if self.status != expected:
            raise ValueError(
                "frozen holdout validation status mismatch"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )

from app.research.evidence import BootstrapConfig


def _sha256_identity(value, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"canonical lowercase SHA256 {field_name} required")
    return value


class USReplaySelectedEvidence(Contract):
    """One exact candidate version that materially contributed to the final US8 artifact."""

    schema_version: Literal[
        "us-replay-selected-evidence-v1"
    ] = "us-replay-selected-evidence-v1"

    scope: Literal[
        "DEVELOPMENT_BASELINE",
        "DEVELOPMENT_TRAIN",
        "DEVELOPMENT_SCENARIO",
        "HOLDOUT_BASELINE",
        "HOLDOUT_SCENARIO",
    ]
    fold_id: str | None
    scenario_id: str | None
    observation_id: UUID
    decision_at: datetime
    evidence_available_at: datetime
    candidate_id: str
    request_id: str
    result_id: str

    @field_validator("fold_id", "scenario_id", mode="before")
    @classmethod
    def optional_identity(cls, value, info):
        if value is None:
            return None
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "decision_at",
        "evidence_available_at",
        mode="before",
    )
    @classmethod
    def utc_times(cls, value):
        return exact_utc(value)

    @field_validator(
        "candidate_id",
        "request_id",
        "result_id",
        mode="before",
    )
    @classmethod
    def hash_identity(cls, value, info):
        return _sha256_identity(
            value,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def exact_binding(self):
        expected_shape = {
            "DEVELOPMENT_BASELINE": (False, False),
            "DEVELOPMENT_TRAIN": (True, False),
            "DEVELOPMENT_SCENARIO": (False, True),
            "HOLDOUT_BASELINE": (False, False),
            "HOLDOUT_SCENARIO": (False, True),
        }[self.scope]

        if (
            (self.fold_id is not None)
            != expected_shape[0]
            or (self.scenario_id is not None)
            != expected_shape[1]
        ):
            raise ValueError(
                "selected evidence scope/fold/scenario shape mismatch"
            )

        if self.evidence_available_at < self.decision_at:
            raise ValueError(
                "selected evidence cannot precede decision"
            )

        expected_candidate_id = semantic_hash({
            "schema_version": "us-replay-research-candidate-v1",
            "scenario_id": self.scenario_id,
            "request_id": self.request_id,
            "result_id": self.result_id,
        })

        if self.candidate_id != expected_candidate_id:
            raise ValueError(
                "selected candidate identity mismatch"
            )

        return self


def _selected_evidence_sort_key(row: USReplaySelectedEvidence):
    return (
        row.scope,
        "" if row.fold_id is None else row.fold_id,
        "" if row.scenario_id is None else row.scenario_id,
        row.decision_at,
        str(row.observation_id),
        row.evidence_available_at,
        row.candidate_id,
    )


class USReplayFrozenResearchProtocol(Contract):
    """Fully frozen US8 research protocol; runtime build clocks are deliberately excluded."""

    schema_version: Literal[
        "us-replay-frozen-research-protocol-v1"
    ] = "us-replay-frozen-research-protocol-v1"

    protocol_id: str
    strategy_id: str
    strategy_version: str
    plan: WalkForwardPlan
    performance_config: PerformanceConfig
    bootstrap: BootstrapConfig
    criteria: ResearchEvidenceCriteria
    scenario_ids: tuple[str, ...]
    development_evaluation_cutoff_at: datetime
    holdout_evaluation_cutoff_at: datetime

    @field_validator(
        "protocol_id",
        "strategy_id",
        "strategy_version",
        mode="before",
    )
    @classmethod
    def identities(cls, value, info):
        return _identity(
            value,
            field_name=info.field_name,
        )

    @field_validator("plan", mode="before")
    @classmethod
    def canonical_plan(cls, value):
        return canonical(
            value,
            WalkForwardPlan,
        )

    @field_validator("performance_config", mode="before")
    @classmethod
    def canonical_performance_config(cls, value):
        return canonical(
            value,
            PerformanceConfig,
        )

    @field_validator("bootstrap", mode="before")
    @classmethod
    def canonical_bootstrap(cls, value):
        return canonical(
            value,
            BootstrapConfig,
        )

    @field_validator("criteria", mode="before")
    @classmethod
    def canonical_criteria(cls, value):
        return canonical(
            value,
            ResearchEvidenceCriteria,
        )

    @field_validator("scenario_ids", mode="before")
    @classmethod
    def canonical_scenario_ids(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical scenario IDs required"
            )

        rows = tuple(
            _identity(
                row,
                field_name="scenario_id",
            )
            for row in value
        )

        if len(set(rows)) != len(rows):
            raise ValueError(
                "duplicate scenario_id"
            )

        if rows != tuple(sorted(rows)):
            raise ValueError(
                "scenario IDs must use canonical sorted order"
            )

        return rows

    @field_validator(
        "development_evaluation_cutoff_at",
        "holdout_evaluation_cutoff_at",
        mode="before",
    )
    @classmethod
    def canonical_cutoffs(cls, value):
        return exact_utc(value)

    @model_validator(mode="after")
    def chronology(self):
        development_end = max(
            fold.test.end
            for fold in self.plan.folds
        )

        if (
            self.development_evaluation_cutoff_at
            < development_end
        ):
            raise ValueError(
                "development evaluation cutoff must be at or after final test end"
            )

        if (
            self.development_evaluation_cutoff_at
            > self.plan.holdout.interval.start
        ):
            raise ValueError(
                "development evaluation cutoff cannot enter frozen holdout"
            )

        if (
            self.holdout_evaluation_cutoff_at
            < self.plan.holdout.interval.end
        ):
            raise ValueError(
                "holdout evaluation cutoff must be at or after holdout end"
            )

        if (
            self.holdout_evaluation_cutoff_at
            < self.development_evaluation_cutoff_at
        ):
            raise ValueError(
                "holdout evaluation cutoff cannot precede development cutoff"
            )

        return self

    @property
    def identity(self) -> str:
        return semantic_hash(
            self.model_dump(
                mode="json",
            )
        )


def us_replay_validation_report_id(
    *,
    protocol,
    development,
    holdout,
    selected_evidence,
    status,
) -> str:
    return semantic_hash({
        "schema_version": "us-replay-research-validation-report-v1",
        "protocol": protocol.model_dump(
            mode="json",
        ),
        "development": development.model_dump(
            mode="json",
        ),
        "holdout": holdout.model_dump(
            mode="json",
        ),
        "selected_evidence": [
            row.model_dump(
                mode="json",
            )
            for row in selected_evidence
        ],
        "status": status,
    })


class USReplayResearchValidationReport(Contract):
    """Final reproducible US8 research-validation artifact."""

    schema_version: Literal[
        "us-replay-research-validation-report-v1"
    ] = "us-replay-research-validation-report-v1"

    protocol: USReplayFrozenResearchProtocol
    development: USReplayDevelopmentValidationReport
    holdout: USReplayFrozenHoldoutValidationReport
    selected_evidence: tuple[USReplaySelectedEvidence, ...]
    status: Literal[
        "MEETS_DECLARED_CRITERIA",
        "FAILS_DECLARED_CRITERIA",
        "INSUFFICIENT_EVIDENCE",
    ]
    report_id: str

    @field_validator("protocol", mode="before")
    @classmethod
    def canonical_protocol(cls, value):
        return canonical(
            value,
            USReplayFrozenResearchProtocol,
        )

    @field_validator("development", mode="before")
    @classmethod
    def canonical_development(cls, value):
        return canonical(
            value,
            USReplayDevelopmentValidationReport,
        )

    @field_validator("holdout", mode="before")
    @classmethod
    def canonical_holdout(cls, value):
        return canonical(
            value,
            USReplayFrozenHoldoutValidationReport,
        )

    @field_validator("selected_evidence", mode="before")
    @classmethod
    def canonical_selected_evidence(cls, value):
        if type(value) is not tuple:
            raise ValueError(
                "tuple of canonical USReplaySelectedEvidence required"
            )

        rows = tuple(
            canonical(
                row,
                USReplaySelectedEvidence,
            )
            for row in value
        )

        if rows != tuple(
            sorted(
                rows,
                key=_selected_evidence_sort_key,
            )
        ):
            raise ValueError(
                "selected evidence must use canonical sorted order"
            )

        keys = tuple(
            (
                row.scope,
                row.fold_id,
                row.scenario_id,
                row.observation_id,
            )
            for row in rows
        )

        if len(set(keys)) != len(keys):
            raise ValueError(
                "duplicate selected evidence key"
            )

        return rows

    @field_validator("report_id", mode="before")
    @classmethod
    def canonical_report_id(cls, value):
        return _sha256_identity(
            value,
            field_name="report_id",
        )

    @model_validator(mode="after")
    def exact_binding(self):
        protocol = self.protocol
        development = self.development
        holdout = self.holdout

        dev_oos = development.evidence.oos
        dev_partition = dev_oos.partition

        if (
            dev_partition.strategy_id,
            dev_partition.strategy_version,
        ) != (
            protocol.strategy_id,
            protocol.strategy_version,
        ):
            raise ValueError(
                "development strategy differs from frozen protocol"
            )

        if not exact_equal(
            dev_partition.plan,
            protocol.plan,
        ):
            raise ValueError(
                "development plan differs from frozen protocol"
            )

        if (
            dev_partition.evaluation_cutoff_at
            != protocol.development_evaluation_cutoff_at
        ):
            raise ValueError(
                "development cutoff differs from frozen protocol"
            )

        if not exact_equal(
            dev_oos.performance_config,
            protocol.performance_config,
        ):
            raise ValueError(
                "development performance config differs from frozen protocol"
            )

        if not exact_equal(
            development.evidence.bootstrap.config,
            protocol.bootstrap,
        ):
            raise ValueError(
                "development bootstrap differs from frozen protocol"
            )

        if not exact_equal(
            development.evidence.criteria,
            protocol.criteria,
        ):
            raise ValueError(
                "development criteria differ from frozen protocol"
            )

        if (
            development.scenarios.scenario_ids
            != protocol.scenario_ids
        ):
            raise ValueError(
                "development scenarios differ from frozen protocol"
            )

        holdout_evidence = holdout.evidence

        if (
            holdout_evidence.strategy_id,
            holdout_evidence.strategy_version,
        ) != (
            protocol.strategy_id,
            protocol.strategy_version,
        ):
            raise ValueError(
                "holdout strategy differs from frozen protocol"
            )

        if not exact_equal(
            holdout_evidence.holdout,
            protocol.plan.holdout,
        ):
            raise ValueError(
                "holdout definition differs from frozen protocol"
            )

        if (
            holdout_evidence.evaluation_cutoff_at
            != protocol.holdout_evaluation_cutoff_at
        ):
            raise ValueError(
                "holdout cutoff differs from frozen protocol"
            )

        if not exact_equal(
            holdout_evidence.performance_config,
            protocol.performance_config,
        ):
            raise ValueError(
                "holdout performance config differs from frozen protocol"
            )

        if not exact_equal(
            holdout_evidence.criteria,
            protocol.criteria,
        ):
            raise ValueError(
                "holdout criteria differ from frozen protocol"
            )

        if (
            holdout.scenarios.scenario_ids
            != protocol.scenario_ids
        ):
            raise ValueError(
                "holdout scenarios differ from frozen protocol"
            )

        fold_ids = {
            fold.fold_id
            for fold in protocol.plan.folds
        }

        for row in self.selected_evidence:
            if (
                row.fold_id is not None
                and row.fold_id not in fold_ids
            ):
                raise ValueError(
                    "selected evidence references unknown fold"
                )
            if (
                row.scenario_id is not None
                and row.scenario_id
                not in protocol.scenario_ids
            ):
                raise ValueError(
                    "selected evidence references undeclared scenario"
                )

        development_baseline_ids = {
            row.observation_id
            for row in self.selected_evidence
            if row.scope == "DEVELOPMENT_BASELINE"
        }
        holdout_baseline_ids = {
            row.observation_id
            for row in self.selected_evidence
            if row.scope == "HOLDOUT_BASELINE"
        }

        if not set(
            dev_oos.test_ids
        ).issubset(
            development_baseline_ids
        ):
            raise ValueError(
                "final evidence omits development OOS baseline selection"
            )

        if not set(
            holdout_evidence.observation_ids
        ).issubset(
            holdout_baseline_ids
        ):
            raise ValueError(
                "final evidence omits holdout baseline selection"
            )

        expected_status = (
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

        if self.status != expected_status:
            raise ValueError(
                "final research validation status mismatch"
            )

        expected_report_id = us_replay_validation_report_id(
            protocol=protocol,
            development=development,
            holdout=holdout,
            selected_evidence=self.selected_evidence,
            status=self.status,
        )

        if self.report_id != expected_report_id:
            raise ValueError(
                "final research validation report_id mismatch"
            )

        return self

__all__ = [
    "USReplayResearchCandidate",
    "USReplayResearchDataset",
    "USReplayFoldPartition",
    "USReplayDevelopmentPartition",
    "USReplayFoldOOSReport",
    "USReplayDevelopmentOOSReport",
    "USReplayDevelopmentEvidenceReport",
    "USReplayScenarioCoverage",
    "USReplayDevelopmentScenarioReport",
    "USReplayDevelopmentValidationReport",
    "USReplayFrozenHoldoutReport",
    "USReplayFrozenHoldoutScenarioReport",
    "USReplayFrozenHoldoutValidationReport",
    "USReplaySelectedEvidence",
    "USReplayFrozenResearchProtocol",
    "USReplayResearchValidationReport",
    "us_replay_validation_report_id",
]
