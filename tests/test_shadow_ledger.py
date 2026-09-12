"""Offline candidate-ledger fixtures; no market facts, fills or results."""
import json
from datetime import timedelta

import pytest

from app.paper import shadow_collection, shadow_freeze, shadow_ledger
from tests.test_shadow_collection import authenticated_watchlist


def completed(tmp_path, monkeypatch, *, empty=False):
    item, packages = authenticated_watchlist()
    if empty:
        item = item.model_copy(update={
            "candidates": (), "evidence": (item.evidence[0],),
        })
        packages = packages[:1]
    at = item.generated_at + timedelta(minutes=30)
    monkeypatch.setattr(shadow_collection, "_now", lambda: at)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: at)
    shadow_collection.complete_watchlist(tmp_path, item, packages)
    monkeypatch.setattr(shadow_ledger, "_now", lambda: at)
    return item, packages


def test_append_records_exact_candidates_without_execution_inference(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    path = shadow_ledger.append_candidate_event(tmp_path, item, packages)
    event = json.loads(path.read_bytes())
    assert event["candidates"] == [item.candidates[0].model_dump(mode="json")]
    assert event["candidate_count"] == 1
    assert event["scoring"] == "NOT SCORED"
    assert event["execution_status"] == "NO EXECUTION INFERENCE"
    assert path.name == event["event_id"] + ".json"


def test_empty_watchlist_is_an_explicit_ledger_event(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch, empty=True)
    event = json.loads(
        shadow_ledger.append_candidate_event(tmp_path, item, packages).read_bytes()
    )
    assert event["candidate_count"] == 0
    assert event["candidates"] == []


def test_ledger_requires_completed_authenticated_watchlist(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_ledger, "_now", lambda: item.generated_at)
    with pytest.raises(FileNotFoundError):
        shadow_ledger.append_candidate_event(tmp_path, item, packages)
    assert not (tmp_path / "candidate-ledger").exists()


def test_audit_is_read_only_and_rejects_candidate_substitution(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    path = shadow_ledger.append_candidate_event(tmp_path, item, packages)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    event = shadow_ledger.audit_candidate_event(tmp_path, item, packages)
    assert event == json.loads(path.read_bytes())
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    altered = item.model_copy(update={
        "candidates": (item.candidates[0].model_copy(update={"thesis": "changed"}),),
    })
    with pytest.raises(ValueError, match="does not bind watchlist"):
        shadow_ledger.audit_candidate_event(tmp_path, altered, packages)


@pytest.mark.parametrize("field,value", [
    ("scoring", "SCORED"), ("execution_status", "FILLED"),
    ("candidate_count", 0), ("watchlist_sha256", "0" * 64),
    ("recorded_at", "2099-01-01T00:00:00+00:00"), ("extra", True),
])
def test_audit_rejects_tampered_events(tmp_path, monkeypatch, field, value):
    item, packages = completed(tmp_path, monkeypatch)
    path = shadow_ledger.append_candidate_event(tmp_path, item, packages)
    event = json.loads(path.read_bytes())
    event[field] = value
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError):
        shadow_ledger.audit_candidate_event(tmp_path, item, packages)


def test_audit_rejects_duplicate_fields(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    path = shadow_ledger.append_candidate_event(tmp_path, item, packages)
    path.write_text('{"scoring":"SCORED",' + path.read_text()[1:])
    with pytest.raises(ValueError, match="duplicate ledger field"):
        shadow_ledger.audit_candidate_event(tmp_path, item, packages)


def test_append_rejects_clock_before_completion(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_ledger, "_now", lambda: item.generated_at)
    with pytest.raises(ValueError, match="clock ordering"):
        shadow_ledger.append_candidate_event(tmp_path, item, packages)
    assert not (tmp_path / "candidate-ledger").exists()
