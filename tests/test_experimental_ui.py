from pathlib import Path

import pytest

from app.experimental import create_experimental_app


COMMIT = "a" * 40


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if route.path == path)


def test_isolated_missing_data_does_not_use_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EGX_DB_PATH", str(tmp_path / "unrelated.db"))
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    state = endpoint(app, "/api/today")()
    assert state["available"] is False
    assert state["symbols"] == []
    assert state["build_commit"] == COMMIT
    assert state["mode"] == "EXPERIMENTAL / PAPER ONLY"
    assert list(tmp_path.iterdir()) == []
    assert endpoint(app, "/health")()["data_available"] is False


@pytest.mark.parametrize("route", ["/", "/performance"])
def test_every_page_has_build_and_paper_label(tmp_path, route):
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    body = endpoint(app, route)().body.decode()
    assert "EXPERIMENTAL / PAPER ONLY" in body
    assert COMMIT in body
    assert "NOT YET VALIDATED" in body


@pytest.mark.parametrize("commit", ["", "abc123", "<script>" + "a" * 32, "A" * 40])
def test_invalid_build_rejected(tmp_path, commit):
    with pytest.raises(ValueError):
        create_experimental_app(state_directory=tmp_path, build_commit=commit)


def test_missing_or_relative_state_rejected(tmp_path):
    for path in (Path("relative"), tmp_path / "missing"):
        with pytest.raises(ValueError):
            create_experimental_app(state_directory=path, build_commit=COMMIT)


def test_symlink_added_after_start_is_rejected(tmp_path):
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    (tmp_path / "platform.db").symlink_to(tmp_path / "other.db")
    assert endpoint(app, "/api/today")()["error"] == "UnsafeStatePath"


def test_only_read_routes_and_no_lifecycle_hooks(tmp_path):
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    assert {r.path for r in app.routes} == {"/", "/api/today", "/performance", "/health"}
    assert all(r.methods == {"GET"} for r in app.routes)
    assert not app.router.on_startup
    assert not app.router.on_shutdown
