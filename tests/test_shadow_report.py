"""Software-only report fixtures; no empirical recommendations or results."""
import json
from decimal import Context, Decimal, localcontext

import pytest

from app.paper import shadow_collection, shadow_exits, shadow_ledger, shadow_positions
from app.paper.shadow_report import (
    exit_evaluation_view, missed_collection_view, position_open_view,
    watchlist_collection_view,
)
from tests.test_shadow_collection import authenticated_watchlist
from tests.test_shadow_exits import prepared_exit
from tests.test_shadow_ledger import completed
from tests.test_shadow_positions import setup_position


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


def test_position_view_reaudits_fill_and_does_not_claim_current_position(tmp_path, monkeypatch):
    args, _, _ = setup_position(tmp_path, monkeypatch)
    shadow_positions.append_position_open_event(tmp_path, *args)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = position_open_view(tmp_path, *args)
    event = shadow_positions.audit_position_open_event(tmp_path, *args)
    assert view["collection_status"] == "SIMULATED OPEN AT ENTRY"
    assert view["current_position_status"] == "UNKNOWN / EXIT NOT AUDITED BY THIS VIEW"
    assert view["position"]["entry"] == event["entry"]
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert view["performance"]["realized_return"] is None
    json.dumps(view, allow_nan=False)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_exit_view_preserves_audited_open_evaluation_without_mark_or_pnl(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    view = exit_evaluation_view(tmp_path, *args, policy, evidence)
    assert view["position_status"] == "OPEN"
    assert view["exit_evaluation"] == {
        "status": "OPEN", "reason": "NO_EXIT_OBSERVED",
        "evaluated_through_sequence": 1, "exit": None,
    }
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert all(value is None for key, value in view["performance"].items() if key != "status")


def test_exit_view_preserves_closed_outcome(tmp_path, monkeypatch):
    changes = {"high": Decimal("110")}
    args, _, policy, evidence = prepared_exit(
        tmp_path, monkeypatch, bar_changes=changes,
    )
    shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    view = exit_evaluation_view(tmp_path, *args, policy, evidence)
    assert view["position_status"] == "CLOSED"
    assert view["exit_evaluation"]["exit"] is not None
    trade = view["closed_paper_trades"]["trade"]
    assert view["closed_paper_trades"]["status"] == "ONE AUTHENTICATED CLOSED PAPER TRADE"
    assert trade["currency"] == "USD"
    assert Decimal(trade["gross_pnl"]) == (
        Decimal(trade["exit_notional"]) - Decimal(trade["entry_notional"])
    )
    assert Decimal(trade["net_pnl"]) == (
        Decimal(trade["gross_pnl"]) - Decimal(trade["entry_cost"]) - Decimal(trade["exit_cost"])
    )
    with localcontext(Context(prec=34)):
        assert Decimal(trade["net_return"]) == Decimal(trade["net_pnl"]) / (
            Decimal(trade["entry_notional"]) + Decimal(trade["entry_cost"])
        )
    assert view["performance"]["nav"] is None


def test_position_and_exit_views_fail_closed_on_tampered_events(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    position_path = next((tmp_path / "position-open-events").glob("*.json"))
    position = json.loads(position_path.read_bytes())
    position["initial_stop"] = "1"
    position_path.write_text(json.dumps(position))
    with pytest.raises(ValueError):
        position_open_view(tmp_path, *args)
    with pytest.raises(ValueError):
        exit_evaluation_view(tmp_path, *args, policy, evidence)
