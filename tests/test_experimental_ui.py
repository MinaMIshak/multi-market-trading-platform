"""Offline UI fixtures only; no empirical evidence or runtime data."""
import json
from pathlib import Path

import pytest

from app.experimental import create_experimental_app
from app.paper import shadow_ledger
from app.ui.shadow import ShadowWatchlistInput, render_shadow_watchlist
from tests.test_shadow_ledger import completed


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


@pytest.mark.parametrize("route", ["/", "/performance", "/shadow"])
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
    assert {r.path for r in app.routes} == {
        "/", "/api/today", "/performance", "/health", "/shadow", "/api/shadow",
    }
    assert all(r.methods == {"GET"} for r in app.routes)
    assert not app.router.on_startup
    assert not app.router.on_shutdown


def prepared_shadow(tmp_path, monkeypatch, *, empty=False):
    directory = tmp_path / 'shadow'
    directory.mkdir()
    watchlist, packages = completed(directory, monkeypatch, empty=empty)
    ledger = shadow_ledger.append_candidate_event(directory, watchlist, packages)
    app = create_experimental_app(
        state_directory=tmp_path, build_commit=COMMIT,
        shadow_input=ShadowWatchlistInput(watchlist, packages),
    )
    return app, ledger, watchlist, packages


def test_missing_input_is_unavailable_without_writes(tmp_path):
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    state = endpoint(app, '/api/shadow')()
    assert state['available'] is False and state['collection'] is None
    assert state['build_commit'] == COMMIT
    assert list(tmp_path.iterdir()) == []
    assert 'UNAVAILABLE' in endpoint(app, '/shadow')().body.decode()


@pytest.mark.parametrize('empty', [False, True])
def test_audited_record_preserves_candidates_and_unknown_execution(tmp_path, monkeypatch, empty):
    app, _, watchlist, _ = prepared_shadow(tmp_path, monkeypatch, empty=empty)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = endpoint(app, '/api/shadow')()
    assert state['available'] is True
    collection = state['collection']
    assert collection['candidate_count'] == len(watchlist.candidates)
    assert collection['performance']['nav'] is None
    assert collection['open_paper_positions']['status'] == 'NOT EVALUATED'
    body = endpoint(app, '/shadow')().body.decode()
    assert COMMIT in body and 'EXPERIMENTAL / PAPER ONLY' in body
    assert 'NOT SCORED' in body
    if empty:
        assert 'zero candidates' in body
    else:
        candidate = collection['candidates'][0]
        assert candidate['execution_status'] == 'NOT EVALUATED'
        assert watchlist.candidates[0].ticker in body
        assert 'NOT_YET_VALIDATED' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['tamper', 'remove', 'symlink'])
def test_each_request_reaudits_without_stale_candidates(tmp_path, monkeypatch, damage):
    app, ledger, _, _ = prepared_shadow(tmp_path, monkeypatch)
    assert endpoint(app, '/api/shadow')()['available'] is True
    if damage == 'tamper':
        payload = json.loads(ledger.read_bytes())
        payload['scoring'] = 'SCORED'
        ledger.write_text(json.dumps(payload))
    elif damage == 'remove':
        ledger.unlink()
    else:
        original = ledger.read_bytes()
        ledger.unlink()
        other = tmp_path / 'other.json'
        other.write_bytes(original)
        ledger.symlink_to(other)
    state = endpoint(app, '/api/shadow')()
    assert state['available'] is False and state['collection'] is None
    assert str(tmp_path) not in json.dumps(state)
    assert 'UNAVAILABLE' in endpoint(app, '/shadow')().body.decode()


def test_missing_packages_fail_closed(tmp_path, monkeypatch):
    _, _, watchlist, _ = prepared_shadow(tmp_path, monkeypatch)
    app = create_experimental_app(
        state_directory=tmp_path, build_commit=COMMIT,
        shadow_input=ShadowWatchlistInput(watchlist, ()),
    )
    assert endpoint(app, '/api/shadow')()['available'] is False


def test_arbitrary_report_json_is_not_an_input(tmp_path):
    with pytest.raises(ValueError, match='canonical'):
        create_experimental_app(state_directory=tmp_path, build_commit=COMMIT, shadow_input={})
    with pytest.raises(ValueError, match='canonical'):
        ShadowWatchlistInput({}, ())


def test_renderer_escapes_authenticated_text(tmp_path, monkeypatch):
    app, _, _, _ = prepared_shadow(tmp_path, monkeypatch)
    state = endpoint(app, '/api/shadow')()
    # Renderer-only mutation: never published as a valid ledger or source fact.
    state['collection']['candidates'][0]['thesis'] = '<script>alert("x")</script>'
    body = render_shadow_watchlist(state)
    assert '<script>' not in body
    assert '&lt;script&gt;' in body


def disk_shadow(tmp_path, monkeypatch):
    _, ledger, watchlist, packages = prepared_shadow(tmp_path, monkeypatch)
    document = {
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': watchlist.model_dump(mode='json'),
        'evidence_packages': [item.model_dump(mode='json') for item in packages],
    }
    path = tmp_path / 'shadow' / 'input.json'
    path.write_text(json.dumps(document))
    app = create_experimental_app(state_directory=tmp_path, build_commit=COMMIT)
    return app, path, document, ledger


def test_disk_input_round_trip_and_fresh_audit(tmp_path, monkeypatch):
    app, path, document, ledger = disk_shadow(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = endpoint(app, '/api/shadow')()
    assert state['available'] is True
    assert state['collection']['candidate_count'] == len(document['watchlist']['candidates'])
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    ledger.unlink()
    assert endpoint(app, '/api/shadow')()['collection'] is None


@pytest.mark.parametrize('damage', [
    'duplicate', 'float', 'naive', 'offset', 'unknown', 'missing', 'review',
    'thesis', 'decimal', 'bool_quantity', 'symlink', 'oversize', 'nested', 'fifo',
])
def test_disk_input_rejects_malformed_or_unaudited_records(tmp_path, monkeypatch, damage):
    from app.ui.shadow_input import MAX_INPUT_BYTES
    import os

    app, path, document, _ = disk_shadow(tmp_path, monkeypatch)
    assert endpoint(app, '/api/shadow')()['available'] is True
    watchlist = document['watchlist']
    candidate = watchlist['candidates'][0]
    if damage == 'float':
        candidate['entry_low'] = 100.0
    elif damage == 'naive':
        watchlist['generated_at'] = '2026-09-01T10:00:00'
    elif damage == 'offset':
        watchlist['generated_at'] = '2026-09-01T10:00:00+01:00'
    elif damage == 'unknown':
        document['extra'] = True
    elif damage == 'missing':
        del watchlist['schema_version']
    elif damage == 'review':
        document['evidence_packages'][0]['review']['approved'] = False
    elif damage == 'thesis':
        candidate['thesis'] = 'Changed after freeze'
    elif damage == 'decimal':
        candidate['entry_low'] = 'NaN'
    elif damage == 'bool_quantity':
        candidate['paper_quantity'] = True
    path.write_text(json.dumps(document))
    if damage == 'duplicate':
        path.write_text('{"schema_version":"shadow-ui-input-v1",' + path.read_text()[1:])
    elif damage == 'symlink':
        path.unlink()
        path.symlink_to(tmp_path / 'absent')
    elif damage == 'oversize':
        path.write_bytes(b' ' * (MAX_INPUT_BYTES + 1))
    elif damage == 'nested':
        path.write_text('[' * 2000 + ']' * 2000)
    elif damage == 'fifo':
        path.unlink()
        os.mkfifo(path)
    state = endpoint(app, '/api/shadow')()
    assert state['available'] is False and state['collection'] is None
    assert str(tmp_path) not in json.dumps(state)
