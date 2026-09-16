from app.main import app, research
from app.ui.research import (
    load_research_state,
    render_research_dashboard,
)


def test_research_state_is_explicitly_not_executed():
    state = load_research_state()

    assert state["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert state["available"] is False
    assert state["engine_status"] == "ENGINE READY"
    assert state["canonical_pit_admission"] == "NO_GO"

    assert (
        state["runtime_report_status"]
        == "NO EMPIRICAL RESEARCH REPORT AVAILABLE"
    )

    assert state["development_oos"] == "NOT EXECUTED"
    assert state["bootstrap_confidence"] == "NOT EXECUTED"
    assert state["scenario_validation"] == "NOT EXECUTED"
    assert state["frozen_holdout"] == "NOT EXECUTED"
    assert state["final_evidence"] == "NOT EVALUATED"


def test_research_dashboard_does_not_invent_empirical_results():
    page = render_research_dashboard()

    assert "Research · M8 / US8 Validation" in page
    assert "ENGINE READY" in page
    assert "NO_GO" in page
    assert "NO EMPIRICAL RESEARCH REPORT AVAILABLE" in page

    assert "Development OOS" in page
    assert "Bootstrap / Confidence" in page
    assert "Scenario Validation" in page
    assert "Frozen Holdout" in page
    assert "NOT EXECUTED" in page
    assert "NOT EVALUATED" in page

    assert "ADMISSIBLE AUTHENTIC PIT PACKAGE" in page

    assert "does not run research evaluation" in page
    assert "does not synthesize" in page

    assert "MEETS_DECLARED_CRITERIA" not in page
    assert "FAILS_DECLARED_CRITERIA" not in page

    assert "BUY" not in page
    assert "SELL" not in page


def test_research_navigation_links_only_existing_pages():
    page = render_research_dashboard()

    assert 'href="/"' in page
    assert 'href="/performance"' in page
    assert 'href="/portfolio"' in page
    assert 'href="/research"' in page
    assert 'href="/system"' in page

    assert 'href="/live"' not in page


def test_research_route_is_registered_and_not_executed_by_default():
    paths = {route.path for route in app.routes}

    assert "/research" in paths

    response = research()

    assert response.status_code == 200
    assert b"M8 / US8 Validation" in response.body
    assert b"NO_GO" in response.body
    assert b"NOT EXECUTED" in response.body
    assert b"NOT EVALUATED" in response.body
