"""M8B declarations and immutable report contracts; no operational readiness claim."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.performance.models import PerformanceConfig, PerformanceReport
from app.strategies.contracts import Contract
from .models import DevelopmentPartition, WalkForwardPlan, _canonical

EvidenceStatus = Literal[
    'MEETS_DECLARED_CRITERIA', 'FAILS_DECLARED_CRITERIA', 'INSUFFICIENT_EVIDENCE',
]


class BootstrapConfig(Contract):
    config_version: Literal['research-bootstrap-v1']
    seed: int
    replications: int = Field(gt=0)
    confidence_level: Decimal = Field(gt=0, lt=1)
    block_size: int = Field(gt=0)

    @field_validator('seed', 'replications', 'block_size', mode='before')
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError('strict Python integer required')
        return value


class ResearchEvidenceCriteria(Contract):
    config_version: Literal['research-evidence-criteria-v1']
    minimum_completed_development_trades: int = Field(ge=2)
    minimum_completed_holdout_trades: int = Field(ge=2)
    minimum_net_expectancy: Decimal
    maximum_drawdown_fraction: Decimal = Field(ge=0)
    minimum_profit_factor: Decimal | None
    minimum_net_expectancy_lower_bound: Decimal | None

    @field_validator('minimum_profit_factor')
    @classmethod
    def nonnegative_pf(cls, value):
        if value is not None and value < 0:
            raise ValueError('profit factor threshold must be nonnegative')
        return value


class FrozenResearchProtocol(Contract):
    schema_version: Literal['frozen-research-protocol-v1']
    protocol_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    strategy_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    strategy_version: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    plan: WalkForwardPlan
    performance_config: PerformanceConfig
    bootstrap: BootstrapConfig
    criteria: ResearchEvidenceCriteria

    @field_validator('plan', 'performance_config', 'bootstrap', 'criteria', mode='before')
    @classmethod
    def canonical_settings(cls, value, info):
        kind = {'plan': WalkForwardPlan, 'performance_config': PerformanceConfig,
                'bootstrap': BootstrapConfig, 'criteria': ResearchEvidenceCriteria}[info.field_name]
        return _canonical(kind, value)


class ConfidenceInterval(Contract):
    schema_version: Literal['research-confidence-interval-v1'] = 'research-confidence-interval-v1'
    estimate: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    valid_replications: int = Field(ge=0)
    unavailable_reason: Literal['ZERO_COMPLETED', 'ONE_COMPLETED', 'BLOCK_EXCEEDS_SAMPLE'] | None


class BootstrapReport(Contract):
    schema_version: Literal['research-bootstrap-report-v1'] = 'research-bootstrap-report-v1'
    config: BootstrapConfig
    completed_observation_ids: tuple[UUID, ...]
    sample_count: int = Field(ge=0)
    net_expectancy: ConfidenceInterval
    total_net_pnl: ConfidenceInterval
    max_drawdown_amount: ConfidenceInterval


class CriterionCheck(Contract):
    criterion: Literal['completed_trades', 'net_expectancy', 'drawdown_fraction',
                       'profit_factor', 'net_expectancy_lower_bound']
    actual: Decimal | None
    threshold: Decimal
    comparison: Literal['GE', 'LE']
    passed: bool | None
    unavailable_reason: str | None


class FoldOOSReport(Contract):
    schema_version: Literal['fold-oos-report-v1'] = 'fold-oos-report-v1'
    fold_id: str
    observation_ids: tuple[UUID, ...]
    performance: PerformanceReport


class DevelopmentOOSReport(Contract):
    schema_version: Literal['development-oos-report-v1'] = 'development-oos-report-v1'
    protocol: FrozenResearchProtocol
    partition: DevelopmentPartition
    folds: tuple[FoldOOSReport, ...]
    fold_count: int
    observation_ids: tuple[UUID, ...]
    performance: PerformanceReport
    positive_folds: int
    negative_folds: int
    flat_folds: int
    undefined_fold_ids: tuple[str, ...]
    bootstrap: BootstrapReport
    checks: tuple[CriterionCheck, ...]
    status: EvidenceStatus


class HoldoutEvidenceReport(Contract):
    schema_version: Literal['holdout-evidence-report-v1'] = 'holdout-evidence-report-v1'
    protocol: FrozenResearchProtocol
    observation_ids: tuple[UUID, ...]
    performance: PerformanceReport
    checks: tuple[CriterionCheck, ...]
    status: EvidenceStatus


LIMITATIONS = (
    'Engineering fixtures are not historical market evidence.',
    'Meeting declared criteria is not proven alpha, robustness, profitability, or live-money readiness.',
    'Protocol freezing is not cryptographic proof of preregistration or single-use holdout enforcement.',
    'Caller owns data authenticity, point-in-time strategy/regime provenance and block appropriateness.',
    'Realized completed-only equity excludes open-position marks and retains M6 paper execution limitations.',
    'Bootstrap intervals describe uncertainty; they are not a guarantee and need not contain the estimate.',
    'Changing strategy after holdout inspection requires a new research cycle and untouched future evidence.',
)


class ResearchEvidenceReport(Contract):
    schema_version: Literal['research-evidence-report-v1'] = 'research-evidence-report-v1'
    protocol: FrozenResearchProtocol
    development: DevelopmentOOSReport | None
    holdout: HoldoutEvidenceReport | None
    status: EvidenceStatus
    limitations: tuple[str, ...] = LIMITATIONS
