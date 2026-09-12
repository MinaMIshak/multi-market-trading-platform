"""Pure M7.1 realized analytics over revalidated multi-session replay evidence."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

from app.paper.replay_models import PaperReplayResult

from .analyzer import _summary as _m7_summary
from .extensions import risk_adjusted
from .models import (
    DrawdownSummary, MonthlyConsistency, MonthlyPerformance, PerformanceReport,
    RegimePerformance,
)
from .replay_models import (
    ReplayPerformanceAnalysisInput, ReplayPerformanceReport,
    ReplaySlippageSensitivityResult, _canonical,
)


D = Decimal


@dataclass(frozen=True)
class _EconomicObservation:
    # A private read-only view for M7's state/metrics-only arithmetic helper.
    # This is NOT a legacy PerformanceObservation or simulated M6 evidence.
    paper_result: PaperReplayResult


def _summary(observations):
    # Canonical reduction order makes rounded sums independent of caller order.
    ordered = sorted(observations, key=lambda row: str(row.replay_input.trade_plan.trade_plan_id))
    return _m7_summary(tuple(_EconomicObservation(row.replay_result) for row in ordered))


def _completed(observations):
    return tuple(row for row in observations if row.replay_result.state == "COMPLETED")


def _exit_key(row):
    exit_fill = row.replay_result.position.exit
    return (exit_fill.known_at_utc, exit_fill.interval_start_utc,
            str(row.replay_input.trade_plan.trade_plan_id))


def _drawdown(observations, starting_equity):
    equity = peak = starting_equity
    amount = percent = D(0)
    for row in sorted(_completed(observations), key=_exit_key):
        equity += row.replay_result.metrics.net_pnl
        peak = max(peak, equity)
        amount = max(amount, peak - equity)
        percent = max(percent, (peak - equity) / peak)
    return DrawdownSummary(
        starting_equity=starting_equity, ending_equity=equity, peak_equity=peak,
        max_drawdown_amount=amount, max_drawdown_pct=percent,
    )


def _monthly(observations):
    groups = defaultdict(list)
    for row in _completed(observations):
        day = row.replay_result.position.exit.market_date
        groups[f"{day.year:04d}-{day.month:02d}"].append(row)
    months = tuple(MonthlyPerformance(month=month, summary=_summary(groups[month]))
                   for month in sorted(groups))
    return months, MonthlyConsistency(
        evaluated_months=len(months),
        positive_months=sum(month.summary.total_net_pnl > 0 for month in months),
        negative_months=sum(month.summary.total_net_pnl < 0 for month in months),
        flat_months=sum(month.summary.total_net_pnl == 0 for month in months),
    )


def _regimes(observations):
    groups = defaultdict(list)
    for row in observations:
        groups[row.market_regime].append(row)
    return tuple(RegimePerformance(market_regime=regime, summary=_summary(groups[regime]))
                 for regime in sorted(groups, key=lambda value: value.value))


def _sensitivity(request):
    # Membership, identities, evidence, and allowed slippage changes have all
    # been checked by ReplayPerformanceAnalysisInput before any aggregation.
    results = {}
    for scenario in request.slippage_scenarios:
        summary = _summary(scenario.observations)
        drawdown = _drawdown(scenario.observations, request.config.starting_equity)
        results[scenario.scenario_id] = ReplaySlippageSensitivityResult(
            scenario=scenario, completed_count=summary.completed_count,
            total_net_pnl=summary.total_net_pnl, net_expectancy=summary.net_expectancy,
            profit_factor=summary.profit_factor,
            max_drawdown_amount=drawdown.max_drawdown_amount,
            max_drawdown_pct=drawdown.max_drawdown_pct,
        )
    if request.baseline_scenario_id is not None:
        base = results[request.baseline_scenario_id]
        results = {key: row.model_copy(update={
            "delta_total_net_pnl": row.total_net_pnl - base.total_net_pnl,
            "delta_net_expectancy": (
                row.net_expectancy - base.net_expectancy
                if row.net_expectancy is not None and base.net_expectancy is not None else None
            ),
        }) for key, row in results.items()}
    return tuple(results[key] for key in sorted(results))


def analyze_replay_performance(request: ReplayPerformanceAnalysisInput) -> ReplayPerformanceReport:
    if type(request) is not ReplayPerformanceAnalysisInput:
        raise TypeError("ReplayPerformanceAnalysisInput required")
    with localcontext(Context(prec=34)):
        # Reconstruct all input boundaries, including model_copy/model_construct
        # and mutable domain objects, and recompute every supplied replay result.
        request = _canonical(request, ReplayPerformanceAnalysisInput)
        months, consistency = _monthly(request.observations)
        return ReplayPerformanceReport(
            performance=PerformanceReport(
                summary=_summary(request.observations),
                drawdown=_drawdown(request.observations, request.config.starting_equity),
                months=months, monthly_consistency=consistency,
                regimes=_regimes(request.observations),
                risk_adjusted=risk_adjusted(request.periodic_returns),
            ),
            slippage_sensitivity=_sensitivity(request),
            baseline_scenario_id=request.baseline_scenario_id,
        )
