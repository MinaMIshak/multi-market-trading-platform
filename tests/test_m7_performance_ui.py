from decimal import Decimal

from app.main import app, performance
from app.performance.models import (
    DrawdownSummary,
    EconomicSummary,
    MonthlyConsistency,
    PerformanceReport,
)
from app.ui.performance import render_performance_dashboard


D = Decimal


def sample_report():
    return PerformanceReport(
        summary=EconomicSummary(
            total_observations=5,
            completed_count=3,
            rejected_count=1,
            no_fill_count=1,
            open_count=0,
            incomplete_count=0,
            win_count=1,
            loss_count=1,
            breakeven_count=1,
            total_gross_pnl=D("12"),
            total_costs=D("2"),
            total_net_pnl=D("10"),
            net_expectancy=D("3.333"),
            r_expectancy=D("0.333"),
            profit_factor=D("2"),
            average_win=D("20"),
            average_loss=D("-10"),
            economic_win_rate=D("0.333"),
            average_mae=D("0.5"),
            average_mfe=D("1.5"),
            average_mae_r=D("0.5"),
            average_mfe_r=D("1.5"),
        ),
        drawdown=DrawdownSummary(
            starting_equity=D("100"),
            ending_equity=D("110"),
            peak_equity=D("120"),
            max_drawdown_amount=D("10"),
            max_drawdown_pct=D("0.083333"),
        ),
        months=(),
        monthly_consistency=MonthlyConsistency(
            evaluated_months=0,
            positive_months=0,
            negative_months=0,
            flat_months=0,
        ),
        regimes=(),
    )


def test_performance_empty_state_is_truthful():
    page = render_performance_dashboard()

    assert "Performance unavailable" in page
    assert "No canonical M7 paper-performance report is available" in page
    assert "No synthetic trades" in page
    assert "PAPER / Observation Mode" in page
    assert "strategy-validation" in page
    assert "BUY" not in page
    assert "SELL" not in page


def test_performance_navigation_contains_required_tabs():
    page = render_performance_dashboard()

    for item in (
        "TODAY",
        "LIVE",
        "PRE-SURGE",
        "SWING",
        "PERFORMANCE",
        "RESEARCH",
        "SYSTEM",
    ):
        assert item in page

    assert 'href="/"' in page
    assert 'href="/performance"' in page


def test_presenter_uses_supplied_report_without_simulation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("UI must not simulate paper execution")

    monkeypatch.setattr(
        "app.paper.simulator.simulate_paper",
        forbidden,
    )

    page = render_performance_dashboard(sample_report())

    assert "Completed-trade economics" in page
    assert "Net P&amp;L" in page
    assert "3.333" in page
    assert "0.083333" in page
    assert "PAPER / Observation Mode" in page


def test_performance_route_is_registered_and_empty_by_default():
    paths = {route.path for route in app.routes}

    assert "/performance" in paths

    response = performance()

    assert response.status_code == 200
    assert b"Performance unavailable" in response.body
    assert b"No synthetic trades" in response.body
