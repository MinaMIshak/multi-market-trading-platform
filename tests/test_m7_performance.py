"""M7 core arithmetic fixtures; not strategy-validation evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from uuid import UUID

import pytest

from app.domain.enums import MarketRegimeType, RiskDecisionType
from app.paper import simulate_paper
from app.performance import (
    PerformanceAnalysisInput,
    PerformanceConfig,
    PerformanceObservation,
    analyze_performance,
)
from test_m6_paper_execution import (
    AT,
    bar,
    config as paper_config,
    plan,
    request as paper_request,
    risk,
)

D = Decimal


def div(a, b):
    with localcontext(Context(prec=34)):
        return D(a) / D(b)


def _bar(at, n, o, h, l, c):
    return bar(
        n,
        o=o,
        h=h,
        l=l,
        c=c,
        interval_start=at + timedelta(minutes=n),
        interval_end=at + timedelta(minutes=n + 1),
        available_at=at + timedelta(minutes=n + 1),
        market_date=at.date(),
        sequence=n + 1,
    )


def observation(
    kind,
    *,
    trade_id=1,
    at=AT,
    regime=MarketRegimeType.RISK_ON,
):
    trade_plan_id = UUID(int=trade_id)

    p = plan(
        trade_plan_id=trade_plan_id,
        created_at=at,
        valid_until=at + timedelta(minutes=2),
    )

    r = risk(
        risk_decision_id=UUID(int=1000 + trade_id),
        trade_plan_id=trade_plan_id,
        risk_budget=D(10),
        approved_risk=D(10),
        quantity=10,
        max_position_value=D(100),
    )

    cfg = paper_config()
    first = _bar(at, 0, 10, 11, 10, 10)

    if kind == "WIN":
        bars = (
            first,
            _bar(at, 1, 10, 12, 10, 12),
        )
    elif kind == "LOSS":
        bars = (
            first,
            _bar(at, 1, 10, 10, 9, 9),
        )
    elif kind == "FLAT":
        cfg = paper_config(
            time_exit_at=at + timedelta(minutes=1),
        )
        bars = (
            first,
            _bar(at, 1, 10, 10.5, 9.5, 10),
        )
    elif kind == "TIME_WIN":
        cfg = paper_config(
            time_exit_at=at + timedelta(minutes=1),
        )
        bars = (
            first,
            _bar(at, 1, 11, 11.5, 10.5, 11),
        )
    elif kind == "TIME_LOSS":
        cfg = paper_config(
            time_exit_at=at + timedelta(minutes=1),
        )
        bars = (
            first,
            _bar(at, 1, 9.5, 10, 9.25, 9.5),
        )
    elif kind == "OPEN":
        bars = (first,)
    elif kind == "INCOMPLETE":
        bars = ()
    elif kind == "NO_FILL":
        bars = (
            _bar(at, 0, 13, 14, 13, 13),
            _bar(at, 1, 13, 14, 13, 13),
        )
    elif kind == "REJECTED":
        r = risk(
            risk_decision_id=UUID(int=1000 + trade_id),
            trade_plan_id=trade_plan_id,
            decision=RiskDecisionType.BLOCK,
            risk_budget=D(10),
            approved_risk=D(0),
            quantity=0,
            max_position_value=D(0),
        )
        bars = (first,)
    else:
        raise AssertionError(kind)

    request = paper_request(
        bars,
        trade_plan=p,
        risk_decision=r,
        config=cfg,
        admission_time=at,
        market_date=at.date(),
    )
    result = simulate_paper(request)

    return PerformanceObservation(
        schema_version="performance-observation-v1",
        paper_input=request,
        paper_result=result,
        market_regime=regime,
        strategy_id="fixture-strategy",
        strategy_version="1",
    )


def analyze(rows, starting_equity="100"):
    return analyze_performance(
        PerformanceAnalysisInput(
            schema_version="performance-analysis-v1",
            config=PerformanceConfig(
                config_version="performance-v1",
                starting_equity=D(starting_equity),
            ),
            observations=tuple(rows),
        )
    )


def test_config_is_explicit_and_versioned():
    with pytest.raises(ValueError):
        PerformanceConfig(config_version="wrong", starting_equity=D(100))
    with pytest.raises(ValueError):
        PerformanceConfig(
            config_version="performance-v1",
            starting_equity=D(0),
        )
    with pytest.raises(ValueError):
        PerformanceConfig(config_version="performance-v1")


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity"],
)
def test_starting_equity_rejects_nonfinite_values(value):
    with pytest.raises(ValueError):
        PerformanceConfig(
            config_version="performance-v1",
            starting_equity=D(value),
        )


def test_observation_rejects_mismatched_m6_result():
    row = observation("WIN")
    bad = row.paper_result.model_copy(update={"state": "OPEN"})

    with pytest.raises(ValueError, match="M6 input/result mismatch"):
        PerformanceObservation(
            schema_version="performance-observation-v1",
            paper_input=row.paper_input,
            paper_result=bad,
            market_regime=row.market_regime,
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
        )


def test_noncompleted_states_do_not_pollute_economics():
    report = analyze(
        [
            observation("WIN", trade_id=1),
            observation("REJECTED", trade_id=2),
            observation("NO_FILL", trade_id=3),
            observation("OPEN", trade_id=4),
            observation("INCOMPLETE", trade_id=5),
        ]
    )

    s = report.summary
    assert s.total_observations == 5
    assert s.completed_count == 1
    assert s.rejected_count == 1
    assert s.no_fill_count == 1
    assert s.open_count == 1
    assert s.incomplete_count == 1
    assert s.total_net_pnl == D(20)
    assert s.net_expectancy == D(20)


def test_exact_core_metrics_and_breakeven():
    report = analyze(
        [
            observation("WIN", trade_id=1),
            observation("LOSS", trade_id=2),
            observation("FLAT", trade_id=3),
        ]
    )

    s = report.summary

    assert (s.win_count, s.loss_count, s.breakeven_count) == (1, 1, 1)
    assert s.total_gross_pnl == D(10)
    assert s.total_costs == D(0)
    assert s.total_net_pnl == D(10)

    assert s.net_expectancy == div(10, 3)
    assert s.r_expectancy == div(1, 3)
    assert s.profit_factor == D(2)
    assert s.average_win == D(20)
    assert s.average_loss == D(-10)
    assert s.economic_win_rate == div(1, 3)


def test_profit_factor_edge_semantics():
    assert analyze([observation("WIN")]).summary.profit_factor is None
    assert analyze([observation("LOSS")]).summary.profit_factor == 0
    assert analyze([observation("FLAT")]).summary.profit_factor is None
    assert analyze([]).summary.profit_factor is None


def test_time_exit_is_classified_by_net_pnl_sign():
    win = observation("TIME_WIN", trade_id=1)
    loss = observation("TIME_LOSS", trade_id=2)

    assert win.paper_result.outcome == "TIME_EXIT"
    assert loss.paper_result.outcome == "TIME_EXIT"

    s = analyze([win, loss]).summary
    assert s.win_count == 1
    assert s.loss_count == 1
    assert s.breakeven_count == 0


def test_mae_mfe_are_aggregated_from_m6_metrics():
    report = analyze(
        [
            observation("WIN", trade_id=1),
            observation("LOSS", trade_id=2),
        ]
    )

    s = report.summary
    assert s.average_mae == D(".5")
    assert s.average_mfe == D("1.5")
    assert s.average_mae_r == D(".5")
    assert s.average_mfe_r == D("1.5")


def test_realized_equity_drawdown_and_tie_order_are_deterministic():
    win = observation("WIN", trade_id=1)
    loss = observation("LOSS", trade_id=2)

    first = analyze([loss, win], starting_equity="100")
    second = analyze([win, loss], starting_equity="100")

    assert first == second

    d = first.drawdown
    assert d.starting_equity == D(100)
    assert d.ending_equity == D(110)
    assert d.peak_equity == D(120)
    assert d.max_drawdown_amount == D(10)
    assert d.max_drawdown_pct == div(1, 12)


def test_monthly_consistency_keeps_year_and_month():
    jan_2026 = datetime(2026, 1, 31, 10, tzinfo=timezone.utc)
    feb_2026 = datetime(2026, 2, 1, 10, tzinfo=timezone.utc)
    jan_2027 = datetime(2027, 1, 31, 10, tzinfo=timezone.utc)

    report = analyze(
        [
            observation("WIN", trade_id=1, at=jan_2026),
            observation("LOSS", trade_id=2, at=feb_2026),
            observation("FLAT", trade_id=3, at=jan_2027),
        ]
    )

    assert tuple(row.month for row in report.months) == (
        "2026-01",
        "2026-02",
        "2027-01",
    )

    c = report.monthly_consistency
    assert c.evaluated_months == 3
    assert c.positive_months == 1
    assert c.negative_months == 1
    assert c.flat_months == 1


def test_regime_grouping_keeps_noncompleted_counts_out_of_economics():
    report = analyze(
        [
            observation(
                "WIN",
                trade_id=1,
                regime=MarketRegimeType.RISK_ON,
            ),
            observation(
                "OPEN",
                trade_id=2,
                regime=MarketRegimeType.RISK_ON,
            ),
            observation(
                "LOSS",
                trade_id=3,
                regime=MarketRegimeType.RISK_OFF,
            ),
        ]
    )

    rows = {
        row.market_regime: row.summary
        for row in report.regimes
    }

    risk_on = rows[MarketRegimeType.RISK_ON]
    assert risk_on.total_observations == 2
    assert risk_on.completed_count == 1
    assert risk_on.open_count == 1
    assert risk_on.total_net_pnl == D(20)

    risk_off = rows[MarketRegimeType.RISK_OFF]
    assert risk_off.completed_count == 1
    assert risk_off.total_net_pnl == D(-10)


def test_duplicate_trade_plan_is_rejected():
    row = observation("WIN")

    with pytest.raises(ValueError, match="duplicate trade_plan_id"):
        analyze([row, row])


def test_analysis_boundary_revalidates_model_copy():
    request = PerformanceAnalysisInput(
        schema_version="performance-analysis-v1",
        config=PerformanceConfig(
            config_version="performance-v1",
            starting_equity=D(100),
        ),
        observations=(observation("WIN"),),
    )

    with pytest.raises(ValueError):
        analyze_performance(
            request.model_copy(
                update={"schema_version": "invalid"}
            )
        )
