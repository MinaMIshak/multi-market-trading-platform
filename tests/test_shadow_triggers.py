"""Artificial trigger fixtures only; no market facts, fills, or recommendations."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.paper import shadow_facts, shadow_triggers
from tests.test_shadow_facts import prepared


def admitted(tmp_path, monkeypatch, bar_changes=None):
    item, packages, facts, fact_packages, available = prepared(tmp_path, monkeypatch)
    if bar_changes:
        bar = facts.bars[0].model_copy(update=bar_changes)
        facts = facts.model_copy(update={"bars": (bar,)})
    now = available + timedelta(minutes=1)
    monkeypatch.setattr(shadow_facts, "_now", lambda: now)
    shadow_facts.append_forward_fact_event(tmp_path, item, packages, facts, fact_packages)
    monkeypatch.setattr(shadow_triggers, "_now", lambda: now)
    return item, packages, facts, fact_packages, now


def test_appends_and_audits_trigger_without_fill(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, _ = admitted(tmp_path, monkeypatch)
    path = shadow_triggers.append_trigger_event(
        tmp_path, item, packages, facts, fact_packages,
    )
    event = json.loads(path.read_bytes())
    assert event["evaluation"]["status"] == "TRIGGERED"
    assert event["evaluation"]["trigger_reference_price"] == "100"
    assert event["execution_status"] == "NO FILL OR POSITION CREATED"
    assert event["scoring"] == "NOT SCORED"
    assert shadow_triggers.audit_trigger_event(
        tmp_path, item, packages, facts, fact_packages,
    ) == event


@pytest.mark.parametrize("changes,status,reason", [
    ({"open": Decimal("105"), "high": Decimal("106"), "low": Decimal("102"),
      "close": Decimal("104")}, "NOT_TRIGGERED", "ENTRY_ZONE_NOT_TOUCHED"),
    ({"open": Decimal("94"), "high": Decimal("100"), "low": Decimal("90"),
      "close": Decimal("96")}, "INVALIDATED_OPEN_GAP", "OPEN_AT_OR_BELOW_STOP_BEFORE_ENTRY"),
    ({"open": Decimal("110"), "high": Decimal("112"), "low": Decimal("100"),
      "close": Decimal("105")}, "TARGET_PASSED_OPEN_GAP", "OPEN_AT_OR_ABOVE_TARGET_BEFORE_ENTRY"),
    ({"open": Decimal("100"), "high": Decimal("111"), "low": Decimal("94"),
      "close": Decimal("101")}, "TRIGGERED_AMBIGUOUS_BAR",
     "ENTRY_TOUCHED_WITH_PATH_ORDER_UNKNOWN"),
    ({"open": Decimal("96"), "high": Decimal("101"), "low": Decimal("94"),
      "close": Decimal("100")}, "TRIGGERED_AMBIGUOUS_BAR",
     "ENTRY_TOUCHED_WITH_PATH_ORDER_UNKNOWN"),
])
def test_conservative_trigger_outcomes(tmp_path, monkeypatch, changes, status, reason):
    item, packages, facts, fact_packages, _ = admitted(tmp_path, monkeypatch, changes)
    result = shadow_triggers.evaluate_trigger(item, facts)
    assert result.status == status
    assert result.reason == reason
    if status.startswith("TRIGGERED"):
        assert result.trigger_sequence == 1
    else:
        assert result.trigger_sequence is result.trigger_reference_price is None


def test_requires_buy_candidate_and_exact_identity(tmp_path, monkeypatch):
    item, _, facts, _, _ = admitted(tmp_path, monkeypatch)
    watch_candidate = item.candidates[0].model_copy(update={
        "decision_status": "WATCH", "paper_quantity": 0,
        "paper_risk_status": "NOT_EVALUATED",
    })
    watch = item.model_copy(update={"candidates": (watch_candidate,)})
    with pytest.raises(ValueError, match="BUY_CANDIDATE"):
        shadow_triggers.evaluate_trigger(watch, facts)
    values = facts.model_dump(mode="python")
    values["identity"]["ticker"] = "TWTR"
    values["bars"][0]["ticker"] = "TWTR"
    changed = shadow_facts.ForwardFactBundle(**values)
    with pytest.raises(ValueError, match="identity mismatch"):
        shadow_triggers.evaluate_trigger(item, changed)


def test_audit_rejects_tampering_and_duplicate_fields(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, _ = admitted(tmp_path, monkeypatch)
    path = shadow_triggers.append_trigger_event(
        tmp_path, item, packages, facts, fact_packages,
    )
    original = path.read_text()
    path.write_text('{"scoring":"SCORED",' + original[1:])
    with pytest.raises(ValueError, match="duplicate trigger-event field"):
        shadow_triggers.audit_trigger_event(
            tmp_path, item, packages, facts, fact_packages,
        )


def test_refuses_publication_before_authenticated_bar_availability(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, now = admitted(tmp_path, monkeypatch)
    monkeypatch.setattr(
        shadow_triggers, "_now", lambda: facts.bars[-1].available_at - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="precedes fact availability"):
        shadow_triggers.append_trigger_event(
            tmp_path, item, packages, facts, fact_packages,
        )
    assert not (tmp_path / "trigger-events").exists()
