"""M7 deterministic paper-performance analysis."""
from .analyzer import analyze_performance
from .models import (
    DrawdownSummary,
    EconomicSummary,
    MonthlyConsistency,
    MonthlyPerformance,
    PerformanceAnalysisInput,
    PerformanceConfig,
    PerformanceObservation,
    PerformanceReport,
    RegimePerformance,
    PeriodicReturnSeries,
    RiskAdjustedSummary,
    SlippageScenario,
    SlippageSensitivityResult,
)

__all__ = [
    "DrawdownSummary",
    "EconomicSummary",
    "MonthlyConsistency",
    "MonthlyPerformance",
    "PerformanceAnalysisInput",
    "PerformanceConfig",
    "PerformanceObservation",
    "PerformanceReport",
    "RegimePerformance",
    "PeriodicReturnSeries",
    "RiskAdjustedSummary",
    "SlippageScenario",
    "SlippageSensitivityResult",
    "analyze_performance",
]
