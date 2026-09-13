"""Software-only report fixtures; no empirical recommendations or results."""
import json

import pytest

from app.paper import shadow_collection, shadow_ledger
from app.paper.shadow_report import missed_collection_view, watchlist_collection_view
from tests.test_shadow_collection import authenticated_watchlist
from tests.test_shadow_ledger import completed


def test_view_preserves_candidate_and_evidence_without_performance_claim(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    shadow_ledger.append_candidate_event(tmp_path, item, packages)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = watchlist_collection_view(tmp_path, item, packages)
    assert view["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert view["collection_status"] == "FROZEN"
    candidate = view["candidates"][0].copy()
    assert candidate.pop("label") == view["label"]
    assert candidate.pop("execution_status") == "NOT EVALUATED"
    assert candidate == item.candidates[0].model_dump(mode="json")
    assert view["evidence_references"] == [e.model_dump(mode="json") for e in item.evidence]
    assert view["performance"]["nav"] is None
    assert view["performance"]["hit_rate"] is None
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert "NOT A COMPLETE DAILY PORTFOLIO" in view["scope"]
    json.dumps(view, allow_nan=False)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_empty_frozen_view_is_distinct_from_missing_collection(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch, empty=True)
    with pytest.raises(FileNotFoundError):
        watchlist_collection_view(tmp_path, item, packages)
    shadow_ledger.append_candidate_event(tmp_path, item, packages)
    view = watchlist_collection_view(tmp_path, item, packages)
    assert view["candidates"] == []
    assert view["candidate_count"] == 0
    assert view["performance"]["cumulative_return"] is None


@pytest.mark.parametrize("field,value", [("candidate_count", 0), ("scoring", "SCORED")])
def test_report_rejects_tampered_ledger(tmp_path, monkeypatch, field, value):
    item, packages = completed(tmp_path, monkeypatch)
    path = shadow_ledger.append_candidate_event(tmp_path, item, packages)
    payload = json.loads(path.read_bytes())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        watchlist_collection_view(tmp_path, item, packages)


def test_report_rejects_substituted_candidate(tmp_path, monkeypatch):
    item, packages = completed(tmp_path, monkeypatch)
    shadow_ledger.append_candidate_event(tmp_path, item, packages)
    changed = item.model_copy(update={"candidates": (
        item.candidates[0].model_copy(update={"thesis": "replacement"}),
    )})
    with pytest.raises(ValueError, match="does not bind watchlist"):
        watchlist_collection_view(tmp_path, changed, packages)


def test_missed_view_requires_receipt_and_never_invents_empty_watchlist(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: item.session.decision_cutoff)
    args = dict(record_id=item.record_id, session=item.session, session_package=packages[0])
    with pytest.raises(FileNotFoundError):
        missed_collection_view(tmp_path, **args)
    shadow_collection.record_missed_session(tmp_path, **args, reason="NO_TIMELY_WATCHLIST")
    view = missed_collection_view(tmp_path, **args)
    assert view["collection_status"] == "MISSED / NOT SCORED"
    assert view["candidates"] is None
    assert view["candidate_count"] is None
    assert view["performance"]["nav"] is None
    assert view["market_status"] == "UNKNOWN / NOT AUDITED BY THIS VIEW"
