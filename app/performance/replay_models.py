"""Additive M7.1 boundaries: canonical M6.1 observations and bound scenarios."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypeVar

from pydantic import field_validator, model_validator

from app.domain.enums import MarketRegimeType
from app.paper.models import PaperExecutionConfig
from app.paper.replay import simulate_paper_replay
from app.paper.replay_models import PaperReplayInput, PaperReplayResult
from app.strategies.contracts import Contract

from .extensions import PeriodicReturnSeries
from .models import PerformanceConfig, PerformanceReport


T = TypeVar("T", bound=Contract)
SLIPPAGE_FIELDS = frozenset({
    "entry_slippage_bps", "stop_slippage_bps", "target_slippage_bps",
    "scheduled_exit_slippage_bps",
})


def _canonical(value, kind: type[T]) -> T:
    if type(value) is not kind:
        raise ValueError(f"canonical {kind.__name__} required")
    if set(value.__dict__) - kind.model_fields.keys():
        raise ValueError(f"unexpected {kind.__name__} fields")
    return kind(**{name: getattr(value, name) for name in kind.model_fields})


def _identity(value):
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("canonical nonblank identity required")
    return value


class ReplayPerformanceObservation(Contract):
    schema_version: Literal["replay-performance-observation-v1"] = "replay-performance-observation-v1"
    replay_input: PaperReplayInput
    replay_result: PaperReplayResult
    strategy_id: str
    strategy_version: str
    market_regime: MarketRegimeType

    @field_validator("replay_input", "replay_result", mode="before")
    @classmethod
    def canonical_replay(cls, value, info):
        kind = PaperReplayInput if info.field_name == "replay_input" else PaperReplayResult
        return _canonical(value, kind)

    @field_validator("strategy_id", "strategy_version", mode="before")
    @classmethod
    def identities(cls, value):
        return _identity(value)

    @model_validator(mode="after")
    def recompute(self):
        if simulate_paper_replay(self.replay_input) != self.replay_result:
            raise ValueError("M6.1 replay input/result mismatch")
        return self


def _observations(value):
    if type(value) is not tuple:
        raise ValueError("tuple of canonical ReplayPerformanceObservation required")
    return tuple(_canonical(row, ReplayPerformanceObservation) for row in value)


def _by_trade_id(observations):
    rows = {row.replay_input.trade_plan.trade_plan_id: row for row in observations}
    if len(rows) != len(observations):
        raise ValueError("duplicate trade_plan_id observation")
    return rows


class ReplaySlippageScenario(Contract):
    schema_version: Literal["replay-slippage-scenario-v1"] = "replay-slippage-scenario-v1"
    scenario_id: str
    observations: tuple[ReplayPerformanceObservation, ...]

    @field_validator("scenario_id", mode="before")
    @classmethod
    def identity(cls, value):
        return _identity(value)

    @field_validator("observations", mode="before")
    @classmethod
    def canonical_observations(cls, value):
        return _observations(value)


class ReplayPerformanceAnalysisInput(Contract):
    schema_version: Literal["replay-performance-analysis-v1"] = "replay-performance-analysis-v1"
    config: PerformanceConfig
    observations: tuple[ReplayPerformanceObservation, ...]
    periodic_returns: PeriodicReturnSeries | None = None
    slippage_scenarios: tuple[ReplaySlippageScenario, ...] = ()
    baseline_scenario_id: str | None = None

    @field_validator("config", mode="before")
    @classmethod
    def canonical_config(cls, value):
        return _canonical(value, PerformanceConfig)

    @field_validator("observations", mode="before")
    @classmethod
    def canonical_observations(cls, value):
        return _observations(value)

    @field_validator("periodic_returns", mode="before")
    @classmethod
    def canonical_periodic(cls, value):
        return None if value is None else _canonical(value, PeriodicReturnSeries)

    @field_validator("slippage_scenarios", mode="before")
    @classmethod
    def canonical_scenarios(cls, value):
        if type(value) is not tuple:
            raise ValueError("tuple of canonical ReplaySlippageScenario required")
        return tuple(_canonical(row, ReplaySlippageScenario) for row in value)

    @field_validator("baseline_scenario_id", mode="before")
    @classmethod
    def baseline_identity(cls, value):
        return None if value is None else _identity(value)

    @model_validator(mode="after")
    def bind_scenarios(self):
        originals = _by_trade_id(self.observations)
        seen = set()
        for scenario in self.slippage_scenarios:
            if scenario.scenario_id in seen:
                raise ValueError("duplicate scenario_id")
            seen.add(scenario.scenario_id)
            rows = _by_trade_id(scenario.observations)
            if rows.keys() != originals.keys():
                raise ValueError("scenario observation set mismatch")
            for key, row in rows.items():
                original = originals[key]
                for name in ("schema_version", "strategy_id", "strategy_version", "market_regime"):
                    if getattr(row, name) != getattr(original, name):
                        raise ValueError("scenario identity mismatch")
                # Whitelist changes, including future input/config fields: all
                # execution evidence and coverage must remain exactly equal.
                for name in PaperReplayInput.model_fields:
                    if name != "config" and getattr(row.replay_input, name) != getattr(original.replay_input, name):
                        raise ValueError("scenario underlying replay input mismatch")
                for name in PaperExecutionConfig.model_fields:
                    if name not in SLIPPAGE_FIELDS and getattr(row.replay_input.config, name) != getattr(original.replay_input.config, name):
                        raise ValueError("unauthorized scenario config change")
        if self.baseline_scenario_id is not None and self.baseline_scenario_id not in seen:
            raise ValueError("baseline scenario missing")
        return self


class ReplaySlippageSensitivityResult(Contract):
    scenario: ReplaySlippageScenario
    completed_count: int
    total_net_pnl: Decimal
    net_expectancy: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_amount: Decimal
    max_drawdown_pct: Decimal
    delta_total_net_pnl: Decimal | None = None
    delta_net_expectancy: Decimal | None = None


class ReplayPerformanceReport(Contract):
    """Reuse M7 aggregates without putting M6.1 evidence in legacy M6 slots.

    The embedded report has no legacy slippage scenarios/baseline. Replay
    scenarios, their canonical evidence, and their baseline belong here.
    """

    schema_version: Literal["replay-performance-report-v1"] = "replay-performance-report-v1"
    performance: PerformanceReport
    slippage_sensitivity: tuple[ReplaySlippageSensitivityResult, ...] = ()
    baseline_scenario_id: str | None = None
