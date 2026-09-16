from app.main import app, system
from app.ui.system import (
    load_system_state,
    render_system_dashboard,
)


def test_system_state_is_explicit_and_fail_closed():
    state = load_system_state()

    assert state["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert state["engineering_status"] == "ENGINEERING HANDOFF COMPLETE"
    assert state["canonical_pit_admission"] == "NO_GO"
    assert state["empirical_validation"] == "NOT RUN"

    assert len(state["blockers"]) == 6

    names = {
        row[0]: row[1]
        for row in state["boundaries"]
    }

    assert names["US1 Historical Identity"] == "NO_GO"
    assert names["US2 Historical Sessions"] == "NO_GO"
    assert names["US4 Corporate Actions"] == "NO_GO"
    assert names["US5A Historical Universe"] == "NO_GO"

    assert names["M7 Performance"] == "ENGINE READY"
    assert (
        names["M8 / US8 Validation"]
        == "ENGINE READY / EMPIRICAL NOT RUN"
    )


def test_system_dashboard_is_truthful_and_non_scoring():
    page = render_system_dashboard()

    assert "System · Data Readiness" in page
    assert "EXPERIMENTAL / PAPER ONLY" in page
    assert "ENGINEERING HANDOFF COMPLETE" in page
    assert "Canonical PIT" in page
    assert "NO_GO" in page
    assert "AUTHORIZED AUTHENTIC DATA / EVIDENCE" in page

    assert "US1 Historical Identity" in page
    assert "US2 Historical Sessions" in page
    assert "US3 Historical Daily" in page
    assert "US4 Corporate Actions" in page
    assert "US5A Historical Universe" in page
    assert "US5B Retrospective PIT" in page
    assert "US7B Historical Intraday" in page
    assert "US7C Paper Replay" in page
    assert "M7 Performance" in page
    assert "M8 / US8 Validation" in page
    assert "Shadow Portfolio" in page

    assert "does not calculate signals" in page
    assert "hit rate" in page
    assert "real-money readiness" in page

    assert "BUY" not in page
    assert "SELL" not in page
    assert "Sharpe" not in page
    assert "Profit Factor" not in page


def test_system_navigation_links_only_existing_pages():
    page = render_system_dashboard()

    assert 'href="/"' in page
    assert 'href="/performance"' in page
    assert 'href="/system"' in page

    assert 'href="/research"' not in page
    assert 'href="/live"' not in page


def test_system_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/system" in paths

    response = system()

    assert response.status_code == 200
    assert b"System" in response.body
    assert b"Data Readiness" in response.body
    assert b"canonical" in response.body.lower()
    assert b"NO_GO" in response.body
