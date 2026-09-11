"""Versioned M7 paper-performance contracts; pure, offline and in-memory."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .extensions import PeriodicReturnSeries, RiskAdjustedSummary

from app.domain.enums import MarketRegimeType
from app.paper.models import PaperSimulationInput, PaperSimulationResult
from app.strategies.contracts import Contract


class PerformanceConfig(Contract):
    config_version: Literal["performance-v1"]
    starting_equity: Decimal = Field(gt=0)

    @field_validator("starting_equity")
    @classmethod
    def finite_starting_equity(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("starting_equity must be finite")
        return value


class PerformanceObservation(Contract):
    schema_version: Literal["performance-observation-v1"]
    paper_input: PaperSimulationInput
    paper_result: PaperSimulationResult
    market_regime: MarketRegimeType
    strategy_id: str = Field(min_length=1, pattern=r"\S")
    strategy_version: str = Field(min_length=1, pattern=r"\S")

    @field_validator("paper_input", "paper_result", mode="before")
    @classmethod
    def canonical_m6_objects(cls, value, info):
        kind = {
            "paper_input": PaperSimulationInput,
            "paper_result": PaperSimulationResult,
        }[info.field_name]
        if not isinstance(value, kind):
            raise ValueError(f"canonical {kind.__name__} required")
        return kind(
            **{
                name: getattr(value, name)
                for name in kind.model_fields
            }
        )

    @field_validator("strategy_id", "strategy_version")
    @classmethod
    def canonical_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("nonblank strategy identity required")
        return value

    @model_validator(mode="after")
    def m6_result_must_match_input(self):
        from app.paper.simulator import simulate_paper

        expected = simulate_paper(self.paper_input)
        if expected != self.paper_result:
            raise ValueError("M6 input/result mismatch")
        return self


class SlippageScenario(Contract):
    schema_version: Literal["slippage-scenario-v1"]
    scenario_id: str = Field(min_length=1, pattern=r"^\S(?:.*\S)?$")
    observations: tuple[PerformanceObservation, ...]

    @field_validator("observations", mode="before")
    @classmethod
    def canonical_observations(cls, value):
        return PerformanceAnalysisInput.canonical_observations(value)


class PerformanceAnalysisInput(Contract):
    schema_version: Literal["performance-analysis-v1"]
    config: PerformanceConfig
    observations: tuple[PerformanceObservation, ...]

    periodic_returns: PeriodicReturnSeries | None = None
    slippage_scenarios: tuple[SlippageScenario, ...] = ()
    baseline_scenario_id: str | None = None

    @field_validator("periodic_returns", mode="before")
    @classmethod
    def canonical_periodic(cls, value):
        if value is None:
            return None
        if not isinstance(value, PeriodicReturnSeries):
            raise ValueError("canonical PeriodicReturnSeries required")
        return PeriodicReturnSeries(**{n: getattr(value, n) for n in PeriodicReturnSeries.model_fields})

    @field_validator("slippage_scenarios", mode="before")
    @classmethod
    def canonical_scenarios(cls, value):
        if not isinstance(value, tuple) or any(not isinstance(s, SlippageScenario) for s in value):
            raise ValueError("tuple of canonical SlippageScenario required")
        return tuple(SlippageScenario(**{n: getattr(s, n) for n in SlippageScenario.model_fields}) for s in value)

    @field_validator("config", mode="before")
    @classmethod
    def canonical_config(cls, value):
        if not isinstance(value, PerformanceConfig):
            raise ValueError("canonical PerformanceConfig required")
        return PerformanceConfig(
            **{
                name: getattr(value, name)
                for name in PerformanceConfig.model_fields
            }
        )

    @field_validator("observations", mode="before")
    @classmethod
    def canonical_observations(cls, value):
        if (
            not isinstance(value, tuple)
            or any(not isinstance(row, PerformanceObservation) for row in value)
        ):
            raise ValueError("tuple of canonical PerformanceObservation required")
        return tuple(
            PerformanceObservation(
                **{
                    name: getattr(row, name)
                    for name in PerformanceObservation.model_fields
                }
            )
            for row in value
        )


class EconomicSummary(Contract):
    total_observations: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    no_fill_count: int = Field(ge=0)
    open_count: int = Field(ge=0)
    incomplete_count: int = Field(ge=0)

    win_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    breakeven_count: int = Field(ge=0)

    total_gross_pnl: Decimal
    total_costs: Decimal
    total_net_pnl: Decimal

    net_expectancy: Decimal | None
    r_expectancy: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    economic_win_rate: Decimal | None = Field(default=None, ge=0, le=1)

    average_mae: Decimal | None = Field(default=None, ge=0)
    average_mfe: Decimal | None = Field(default=None, ge=0)
    average_mae_r: Decimal | None = Field(default=None, ge=0)
    average_mfe_r: Decimal | None = Field(default=None, ge=0)


class DrawdownSummary(Contract):
    starting_equity: Decimal
    ending_equity: Decimal
    peak_equity: Decimal
    max_drawdown_amount: Decimal = Field(ge=0)
    max_drawdown_pct: Decimal = Field(ge=0)


class MonthlyPerformance(Contract):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    summary: EconomicSummary


class MonthlyConsistency(Contract):
    evaluated_months: int = Field(ge=0)
    positive_months: int = Field(ge=0)
    negative_months: int = Field(ge=0)
    flat_months: int = Field(ge=0)


class RegimePerformance(Contract):
    market_regime: MarketRegimeType
    summary: EconomicSummary


class SlippageSensitivityResult(Contract):
    scenario: SlippageScenario
    completed_count: int
    total_net_pnl: Decimal
    net_expectancy: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_amount: Decimal
    max_drawdown_pct: Decimal
    delta_total_net_pnl: Decimal | None = None
    delta_net_expectancy: Decimal | None = None


class PerformanceReport(Contract):
    schema_version: Literal["performance-report-v1"] = "performance-report-v1"
    summary: EconomicSummary
    drawdown: DrawdownSummary
    months: tuple[MonthlyPerformance, ...]
    monthly_consistency: MonthlyConsistency
    regimes: tuple[RegimePerformance, ...]
    risk_adjusted: RiskAdjustedSummary = Field(default_factory=RiskAdjustedSummary)
    slippage_sensitivity: tuple[SlippageSensitivityResult, ...] = ()
    baseline_scenario_id: str | None = None
