"""Deterministic aggregate analytics over canonical M6 paper results."""
from __future__ import annotations

from collections import defaultdict
from decimal import Context, Decimal, localcontext

from app.domain.enums import MarketRegimeType

from .extensions import risk_adjusted

from .models import (
    DrawdownSummary,
    EconomicSummary,
    MonthlyConsistency,
    MonthlyPerformance,
    PerformanceAnalysisInput,
    PerformanceObservation,
    PerformanceReport,
    RegimePerformance,
    SlippageSensitivityResult,
)

D = Decimal


def analyze_performance(
    request: PerformanceAnalysisInput,
) -> PerformanceReport:
    if not isinstance(request, PerformanceAnalysisInput):
        raise TypeError("PerformanceAnalysisInput required")

    request = PerformanceAnalysisInput(
        **{
            name: getattr(request, name)
            for name in PerformanceAnalysisInput.model_fields
        }
    )

    seen = set()
    for observation in request.observations:
        trade_plan_id = observation.paper_input.trade_plan.trade_plan_id
        if trade_plan_id in seen:
            raise ValueError("duplicate trade_plan_id observation")
        seen.add(trade_plan_id)

    with localcontext(Context(prec=34)):
        summary = _summary(request.observations)
        drawdown = _drawdown(
            request.observations,
            request.config.starting_equity,
        )
        months, consistency = _monthly(request.observations)
        regimes = _regimes(request.observations)

        return PerformanceReport(
            summary=summary,
            drawdown=drawdown,
            months=months,
            monthly_consistency=consistency,
            regimes=regimes,
            risk_adjusted=risk_adjusted(request.periodic_returns),
            slippage_sensitivity=_sensitivity(request),
            baseline_scenario_id=request.baseline_scenario_id,
        )


def _completed(observations):
    return tuple(
        observation
        for observation in observations
        if observation.paper_result.state == "COMPLETED"
    )


def _mean(values):
    values = tuple(values)
    if not values:
        return None
    return sum(values, D(0)) / D(len(values))


def _summary(observations) -> EconomicSummary:
    observations = tuple(observations)

    counts = {
        "COMPLETED": 0,
        "REJECTED": 0,
        "NO_FILL": 0,
        "OPEN": 0,
        "INCOMPLETE": 0,
    }

    for observation in observations:
        counts[observation.paper_result.state] += 1

    completed = _completed(observations)

    metrics = []
    for observation in completed:
        metric = observation.paper_result.metrics
        if metric is None:
            raise ValueError("completed M6 result requires metrics")
        metrics.append(metric)

    net_values = tuple(metric.net_pnl for metric in metrics)
    positive = tuple(value for value in net_values if value > 0)
    negative = tuple(value for value in net_values if value < 0)
    breakeven = tuple(value for value in net_values if value == 0)

    total_gross = sum(
        (metric.gross_pnl for metric in metrics),
        D(0),
    )
    total_costs = sum(
        (
            metric.entry_cost + metric.exit_cost
            for metric in metrics
        ),
        D(0),
    )
    total_net = sum(net_values, D(0))

    gains = sum(positive, D(0))
    loss_abs = -sum(negative, D(0))

    if not metrics:
        profit_factor = None
    elif loss_abs == 0:
        profit_factor = None
    elif gains == 0:
        profit_factor = D(0)
    else:
        profit_factor = gains / loss_abs

    completed_count = len(metrics)

    return EconomicSummary(
        total_observations=len(observations),
        completed_count=completed_count,
        rejected_count=counts["REJECTED"],
        no_fill_count=counts["NO_FILL"],
        open_count=counts["OPEN"],
        incomplete_count=counts["INCOMPLETE"],
        win_count=len(positive),
        loss_count=len(negative),
        breakeven_count=len(breakeven),
        total_gross_pnl=total_gross,
        total_costs=total_costs,
        total_net_pnl=total_net,
        net_expectancy=_mean(net_values),
        r_expectancy=_mean(
            metric.r_multiple for metric in metrics
        ),
        profit_factor=profit_factor,
        average_win=_mean(positive),
        average_loss=_mean(negative),
        economic_win_rate=(
            D(len(positive)) / D(completed_count)
            if completed_count
            else None
        ),
        average_mae=_mean(metric.mae for metric in metrics),
        average_mfe=_mean(metric.mfe for metric in metrics),
        average_mae_r=_mean(metric.mae_r for metric in metrics),
        average_mfe_r=_mean(metric.mfe_r for metric in metrics),
    )


def _exit_key(observation: PerformanceObservation):
    result = observation.paper_result
    if (
        result.state != "COMPLETED"
        or result.position is None
        or result.position.exit is None
    ):
        raise ValueError("completed M6 result requires exit")

    exit_fill = result.position.exit

    return (
        exit_fill.known_at,
        exit_fill.interval_start,
        str(observation.paper_input.trade_plan.trade_plan_id),
    )


def _drawdown(
    observations,
    starting_equity: Decimal,
) -> DrawdownSummary:
    completed = sorted(
        _completed(observations),
        key=_exit_key,
    )

    equity = starting_equity
    peak = starting_equity
    max_drawdown = D(0)
    max_drawdown_pct = D(0)

    for observation in completed:
        metrics = observation.paper_result.metrics
        if metrics is None:
            raise ValueError("completed M6 result requires metrics")

        equity += metrics.net_pnl

        if equity > peak:
            peak = equity

        drawdown = peak - equity
        drawdown_pct = drawdown / peak

        if drawdown > max_drawdown:
            max_drawdown = drawdown

        if drawdown_pct > max_drawdown_pct:
            max_drawdown_pct = drawdown_pct

    return DrawdownSummary(
        starting_equity=starting_equity,
        ending_equity=equity,
        peak_equity=peak,
        max_drawdown_amount=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
    )


def _monthly(observations):
    groups = defaultdict(list)

    for observation in _completed(observations):
        market_date = observation.paper_input.market_date
        key = f"{market_date.year:04d}-{market_date.month:02d}"
        groups[key].append(observation)

    months = tuple(
        MonthlyPerformance(
            month=month,
            summary=_summary(tuple(groups[month])),
        )
        for month in sorted(groups)
    )

    positive = sum(
        month.summary.total_net_pnl > 0
        for month in months
    )
    negative = sum(
        month.summary.total_net_pnl < 0
        for month in months
    )
    flat = sum(
        month.summary.total_net_pnl == 0
        for month in months
    )

    return (
        months,
        MonthlyConsistency(
            evaluated_months=len(months),
            positive_months=positive,
            negative_months=negative,
            flat_months=flat,
        ),
    )


def _regimes(observations):
    groups: dict[
        MarketRegimeType,
        list[PerformanceObservation],
    ] = defaultdict(list)

    for observation in observations:
        groups[observation.market_regime].append(observation)

    return tuple(
        RegimePerformance(
            market_regime=regime,
            summary=_summary(tuple(groups[regime])),
        )
        for regime in sorted(
            groups,
            key=lambda value: value.value,
        )
    )


def _sensitivity(request):
    allowed = {'entry_slippage_bps', 'stop_slippage_bps', 'target_slippage_bps',
               'scheduled_exit_slippage_bps'}
    originals = {o.paper_input.trade_plan.trade_plan_id: o for o in request.observations}
    results = {}
    for scenario in request.slippage_scenarios:
        if scenario.scenario_id in results:
            raise ValueError('duplicate scenario_id')
        rows = {o.paper_input.trade_plan.trade_plan_id: o for o in scenario.observations}
        if len(rows) != len(scenario.observations) or rows.keys() != originals.keys():
            raise ValueError('scenario observation set mismatch')
        for key, row in rows.items():
            original = originals[key]
            if any(getattr(row, name) != getattr(original, name)
                   for name in ('schema_version', 'strategy_id', 'strategy_version', 'market_regime')):
                raise ValueError('scenario identity mismatch')
            for name in type(row.paper_input).model_fields:
                if name != 'config' and getattr(row.paper_input, name) != getattr(original.paper_input, name):
                    raise ValueError('scenario underlying input mismatch')
            for name in type(row.paper_input.config).model_fields:
                if name not in allowed and getattr(row.paper_input.config, name) != getattr(original.paper_input.config, name):
                    raise ValueError('unauthorized scenario config change')
        summary = _summary(scenario.observations)
        drawdown = _drawdown(scenario.observations, request.config.starting_equity)
        results[scenario.scenario_id] = SlippageSensitivityResult(
            scenario=scenario, completed_count=summary.completed_count,
            total_net_pnl=summary.total_net_pnl, net_expectancy=summary.net_expectancy,
            profit_factor=summary.profit_factor,
            max_drawdown_amount=drawdown.max_drawdown_amount,
            max_drawdown_pct=drawdown.max_drawdown_pct,
        )
    baseline = request.baseline_scenario_id
    if baseline is not None:
        if baseline not in results:
            raise ValueError('baseline scenario missing')
        base = results[baseline]
        results = {key: row.model_copy(update={
            'delta_total_net_pnl': row.total_net_pnl - base.total_net_pnl,
            'delta_net_expectancy': (row.net_expectancy - base.net_expectancy
                                     if row.net_expectancy is not None and base.net_expectancy is not None else None),
        }) for key, row in results.items()}
    return tuple(results[key] for key in sorted(results))
