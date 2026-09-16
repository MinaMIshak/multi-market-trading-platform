from app.main import app, portfolio
from app.ui.portfolio import (
    load_portfolio_state,
    render_portfolio_dashboard,
)


def test_portfolio_state_fails_closed_without_runtime_source():
    state = load_portfolio_state()

    assert state["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert state["available"] is False

    assert (
        state["source_status"]
        == "NO AUTHENTICATED RUNTIME SNAPSHOT SOURCE"
    )

    assert state["valuation_status"] == "UNAVAILABLE"
    assert state["performance_status"] == "VALUATION ONLY / NOT SCORED"

    assert state["gross_marked_nav"] is None
    assert state["cash"] is None
    assert state["currency"] is None
    assert state["open_position_count"] is None
    assert state["snapshot_date_utc"] is None


def test_portfolio_empty_state_does_not_invent_values():
    page = render_portfolio_dashboard()

    assert "Shadow Portfolio" in page
    assert "Authenticated Valuation" in page
    assert "EXPERIMENTAL / PAPER ONLY" in page
    assert "VALUATION ONLY / NOT SCORED" in page

    assert "Gross Marked NAV" in page
    assert "Cash" in page
    assert "Open Positions" in page
    assert "UNAVAILABLE" in page

    assert "NO AUTHENTICATED RUNTIME SNAPSHOT SOURCE" in page

    assert "No NAV, cash balance, position count" in page
    assert "synthesized" in page

    assert "BUY" not in page
    assert "SELL" not in page
    assert "Profit Factor" not in page
    assert "Sharpe" not in page


def test_portfolio_navigation_links_only_existing_pages():
    page = render_portfolio_dashboard()

    assert 'href="/"' in page
    assert 'href="/performance"' in page
    assert 'href="/portfolio"' in page
    assert 'href="/system"' in page

    assert 'href="/research"' not in page
    assert 'href="/live"' not in page


def test_portfolio_route_is_registered_and_unavailable_by_default():
    paths = {route.path for route in app.routes}

    assert "/portfolio" in paths

    response = portfolio()

    assert response.status_code == 200
    assert b"Shadow Portfolio" in response.body
    assert b"UNAVAILABLE" in response.body
    assert b"NOT SCORED" in response.body
