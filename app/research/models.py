"""Versioned M8A contracts for executable labels and explicit development splits."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import Field, computed_field, field_validator, model_validator

from app.paper.models import PaperFill, PaperPosition, PaperTradeMetrics
from app.performance.models import PerformanceObservation
from app.strategies.contracts import Contract


def _canonical(kind, value):
    if type(value) is not kind:
        raise ValueError(f'canonical {kind.__name__} required')
    return kind(**{name: getattr(value, name) for name in kind.model_fields})


def _utc(value):
    if value.tzinfo != timezone.utc:
        raise ValueError('canonical UTC timestamp required')
    return value


class ResearchObservation(Contract):
    schema_version: Literal['research-observation-v1'] = 'research-observation-v1'
    performance: PerformanceObservation

    @field_validator('performance', mode='before')
    @classmethod
    def canonical_performance(cls, value):
        # M7 verifies exact replay equality. Also reject dictionary substitutions
        # in result descendants, whose original M6 constructors accept mappings.
        if type(value) is not PerformanceObservation:
            raise ValueError('canonical PerformanceObservation required')
        result = value.paper_result
        from app.paper.models import PaperSimulationResult
        if type(result) is not PaperSimulationResult:
            raise ValueError('canonical PaperSimulationResult required')
        if result.position is not None:
            position = _canonical(PaperPosition, result.position)
            _canonical(PaperFill, result.position.entry)
            if position.exit is not None:
                _canonical(PaperFill, result.position.exit)
        if result.metrics is not None:
            _canonical(PaperTradeMetrics, result.metrics)
        return _canonical(PerformanceObservation, value)

    @model_validator(mode='after')
    def timing(self):
        _utc(self.decision_at)
        if self.label_available_at is not None:
            _utc(self.label_available_at)
            if self.label_available_at <= self.decision_at:
                raise ValueError('label must become available after admission')
        return self

    @computed_field
    @property
    def observation_id(self) -> UUID:
        return self.performance.paper_input.trade_plan.trade_plan_id

    @computed_field
    @property
    def decision_at(self) -> datetime:
        return self.performance.paper_input.admission_time

    @computed_field
    @property
    def label_available_at(self) -> datetime | None:
        result = self.performance.paper_result
        if result.state != 'COMPLETED':
            return None
        return result.position.exit.known_at

    @computed_field
    @property
    def economic_label(self) -> Literal['WIN', 'LOSS', 'BREAKEVEN'] | None:
        result = self.performance.paper_result
        if result.state != 'COMPLETED':
            return None
        net = result.metrics.net_pnl
        return 'WIN' if net > 0 else 'LOSS' if net < 0 else 'BREAKEVEN'


class ResearchInterval(Contract):
    """Explicit UTC half-open [start, end) interval."""
    start: datetime
    end: datetime

    @field_validator('start', 'end')
    @classmethod
    def utc(cls, value):
        return _utc(value)

    @model_validator(mode='after')
    def chronology(self):
        if self.start >= self.end:
            raise ValueError('positive interval required')
        return self


class WalkForwardFold(Contract):
    schema_version: Literal['walk-forward-fold-v1'] = 'walk-forward-fold-v1'
    fold_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    train: ResearchInterval
    test: ResearchInterval

    @field_validator('train', 'test', mode='before')
    @classmethod
    def intervals(cls, value):
        return _canonical(ResearchInterval, value)

    @model_validator(mode='after')
    def chronology(self):
        if self.train.end > self.test.start:
            raise ValueError('training must precede test')
        return self


class FrozenHoldout(Contract):
    schema_version: Literal['frozen-holdout-v1'] = 'frozen-holdout-v1'
    holdout_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    interval: ResearchInterval

    @field_validator('interval', mode='before')
    @classmethod
    def canonical_interval(cls, value):
        return _canonical(ResearchInterval, value)


class WalkForwardPlan(Contract):
    schema_version: Literal['walk-forward-plan-v1'] = 'walk-forward-plan-v1'
    plan_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    folds: tuple[WalkForwardFold, ...] = Field(min_length=1)
    holdout: FrozenHoldout

    @field_validator('folds', mode='before')
    @classmethod
    def canonical_folds(cls, value):
        if not isinstance(value, tuple):
            raise ValueError('tuple of canonical folds required')
        return tuple(_canonical(WalkForwardFold, fold) for fold in value)

    @field_validator('holdout', mode='before')
    @classmethod
    def canonical_holdout(cls, value):
        return _canonical(FrozenHoldout, value)

    @model_validator(mode='after')
    def chronology(self):
        if len({fold.fold_id for fold in self.folds}) != len(self.folds):
            raise ValueError('duplicate fold_id')
        for previous, current in zip(self.folds, self.folds[1:]):
            if previous.test.end > current.test.start:
                raise ValueError('test windows must be ordered and nonoverlapping')
            if (current.train.start < previous.train.start
                    or current.train.end < previous.train.end):
                raise ValueError('training windows must roll forward')
        if any(fold.test.end >= self.holdout.interval.start for fold in self.folds):
            raise ValueError('holdout must be strictly after development test windows')
        return self


class ResearchDataset(Contract):
    schema_version: Literal['research-dataset-v1'] = 'research-dataset-v1'
    strategy_id: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    strategy_version: str = Field(min_length=1, pattern=r'^\S(?:.*\S)?$')
    observations: tuple[ResearchObservation, ...]

    @field_validator('observations', mode='before')
    @classmethod
    def canonical_observations(cls, value):
        if not isinstance(value, tuple):
            raise ValueError('tuple of canonical ResearchObservation required')
        return tuple(_canonical(ResearchObservation, row) for row in value)

    @model_validator(mode='after')
    def identities(self):
        seen = set()
        for row in self.observations:
            if row.observation_id in seen:
                raise ValueError('duplicate observation trade_plan_id')
            seen.add(row.observation_id)
            if (row.performance.strategy_id, row.performance.strategy_version) != (
                    self.strategy_id, self.strategy_version):
                raise ValueError('mixed strategy identity/version')
        return self


class FoldPartition(Contract):
    schema_version: Literal['fold-partition-v1'] = 'fold-partition-v1'
    fold: WalkForwardFold
    training_ids: tuple[UUID, ...]
    purged_training_ids: tuple[UUID, ...]
    unavailable_training_ids: tuple[UUID, ...]
    test_ids: tuple[UUID, ...]

    @computed_field
    @property
    def training_count(self) -> int:
        return len(self.training_ids)

    @computed_field
    @property
    def purged_training_count(self) -> int:
        return len(self.purged_training_ids)

    @computed_field
    @property
    def unavailable_training_count(self) -> int:
        return len(self.unavailable_training_ids)

    @computed_field
    @property
    def test_count(self) -> int:
        return len(self.test_ids)


class DevelopmentPartition(Contract):
    schema_version: Literal['development-partition-v1'] = 'development-partition-v1'
    strategy_id: str
    strategy_version: str
    plan: WalkForwardPlan
    folds: tuple[FoldPartition, ...]
