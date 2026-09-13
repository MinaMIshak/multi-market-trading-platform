"""Artificial fill fixtures only; no market facts, orders, or recommendations."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.paper import shadow_fills, shadow_triggers
from app.paper.shadow_fills import (
    COST_FIELDS, PARTICIPATION_FIELDS, SLIPPAGE_FIELDS, ShadowFillPolicy,
)
from app.paper.shadow_records import ShadowEvidenceReference
from tests.test_shadow_collection import package
from tests.test_shadow_triggers import admitted


def prepared(tmp_path, monkeypatch, *, bar_changes=None, participation=Decimal("0.10"),
             slippage=Decimal("10"), complete_session=False):
    item, packages, facts, fact_packages, now = admitted(
        tmp_path, monkeypatch, bar_changes, complete_session=complete_session,
    )
    fill_packages = []
    for char, fields in zip(
        "def", (SLIPPAGE_FIELDS, COST_FIELDS, PARTICIPATION_FIELDS), strict=True,
    ):
        reference = ShadowEvidenceReference(
            evidence_id=char * 64, source_authority="official fixture authority",
            source_locator=f"fixture://fill/{char}", artifact_sha256=char * 64,
            available_at=item.information_cutoff,
        )
        fill_packages.append(package(char, reference, tuple(fields)))
    policy = ShadowFillPolicy(
        market="US", currency="USD", entry_slippage_bps=slippage,
        cost_bps_per_side=Decimal("5"), fixed_cost_per_side=Decimal("1"),
        max_volume_participation_pct=participation,
        slippage_evidence_package_id=fill_packages[0].identity,
        cost_evidence_package_id=fill_packages[1].identity,
        participation_evidence_package_id=fill_packages[2].identity,
    )
    selection_at = item.generated_at + timedelta(minutes=31)
    monkeypatch.setattr(shadow_fills, "_now", lambda: selection_at)
    shadow_fills.freeze_fill_policy_selection(
        tmp_path, item, packages, policy, tuple(fill_packages),
    )
    monkeypatch.setattr(shadow_triggers, "_now", lambda: now)
    shadow_triggers.append_trigger_event(tmp_path, item, packages, facts, fact_packages)
    monkeypatch.setattr(shadow_fills, "_now", lambda: now)
    return item, packages, facts, fact_packages, policy, tuple(fill_packages), now


def test_fill_requires_immutable_pre_session_policy_selection(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, now = prepared(
        tmp_path, monkeypatch,
    )
    selection = next((tmp_path / "fill-policy-selections").glob("*.json"))
    selection.unlink()
    with pytest.raises(FileNotFoundError):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )
    selection_at = item.generated_at + timedelta(minutes=31)
    monkeypatch.setattr(shadow_fills, "_now", lambda: selection_at)
    shadow_fills.freeze_fill_policy_selection(
        tmp_path, item, packages, policy, fill_packages,
    )
    alternate = policy.model_copy(update={"entry_slippage_bps": Decimal("20")})
    with pytest.raises(FileExistsError):
        shadow_fills.freeze_fill_policy_selection(
            tmp_path, item, packages, alternate, fill_packages,
        )
    with pytest.raises(ValueError, match="does not bind pre-session selection"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, alternate, fill_packages,
        )


def test_fill_rejects_tampered_or_post_open_policy_selection(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, now = prepared(
        tmp_path, monkeypatch,
    )
    path = next((tmp_path / "fill-policy-selections").glob("*.json"))
    original = path.read_text()
    path.write_text('{"market":"US",' + original[1:])
    with pytest.raises(ValueError, match="duplicate fill-policy-selection field"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )
    event = json.loads(original)
    event["selected_at"] = item.session.opens_at.isoformat()
    path.write_text(json.dumps(event))
    monkeypatch.setattr(shadow_fills, "_now", lambda: now)
    with pytest.raises(ValueError, match="does not bind pre-session selection"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )


def test_appends_and_audits_evidenced_simulated_fill(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch,
    )
    path = shadow_fills.append_fill_event(
        tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
    )
    event = json.loads(path.read_bytes())
    assert event["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert event["execution_status"] == "SIMULATED ENTRY FILL CREATED"
    assert event["position_status"] == "POSITION EVENT NOT CREATED"
    expected = {
        "raw_price": "100", "fill_price": "100.100", "quantity": 1,
        "notional": "100.100", "entry_cost": "1.05005", "currency": "USD",
    }
    assert {key: event["fill"][key] for key in expected} == expected
    assert shadow_fills.audit_fill_event(
        tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
    ) == event


def test_capacity_is_floored_and_creates_no_fill(tmp_path, monkeypatch):
    args = prepared(tmp_path, monkeypatch, participation=Decimal("0.0009"))
    item, packages, facts, fact_packages, policy, fill_packages, _ = args
    path = shadow_fills.append_fill_event(
        tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
    )
    event = json.loads(path.read_bytes())
    assert event["execution_status"] == "NO FILL: INSUFFICIENT CAPACITY"
    assert event["fill"] is None


@pytest.mark.parametrize("changes", [
    {"open": Decimal("100"), "high": Decimal("111"), "low": Decimal("94"),
     "close": Decimal("101")},
    {"open": Decimal("105"), "high": Decimal("106"), "low": Decimal("102"),
     "close": Decimal("104")},
])
def test_only_unambiguous_trigger_can_reach_fill(tmp_path, monkeypatch, changes):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch, bar_changes=changes,
    )
    with pytest.raises(ValueError, match="unambiguous authenticated trigger"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )
    assert not (tmp_path / "fill-events").exists()


def test_policy_evidence_must_be_complete_and_predeclared(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch,
    )
    with pytest.raises(ValueError, match="match references exactly"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages[:2],
        )
    insufficient = package("9", ShadowEvidenceReference(
        evidence_id="9" * 64, source_authority="fixture", source_locator="fixture://bad",
        artifact_sha256="9" * 64, available_at=item.information_cutoff,
    ))
    changed = policy.model_copy(update={"slippage_evidence_package_id": insufficient.identity})
    with pytest.raises(ValueError, match="fill-policy fields"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, changed,
            (insufficient, fill_packages[1], fill_packages[2]),
        )


def test_late_policy_evidence_is_rejected(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch,
    )
    late_ref = ShadowEvidenceReference(
        evidence_id="8" * 64, source_authority="fixture", source_locator="fixture://late",
        artifact_sha256="8" * 64, available_at=item.information_cutoff + timedelta(seconds=1),
    )
    late = package("8", late_ref, tuple(SLIPPAGE_FIELDS))
    changed = policy.model_copy(update={"slippage_evidence_package_id": late.identity})
    with pytest.raises(ValueError, match="not safely known"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, changed,
            (late, fill_packages[1], fill_packages[2]),
        )


def test_slippage_cannot_cross_target(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch, slippage=Decimal("1000"),
    )
    with pytest.raises(ValueError, match="outside candidate geometry"):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )


def test_public_boundary_revalidates_copied_policy(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, _ = prepared(
        tmp_path, monkeypatch,
    )
    bypassed = policy.model_copy(update={"max_volume_participation_pct": Decimal("2")})
    with pytest.raises(ValidationError):
        shadow_fills.append_fill_event(
            tmp_path, item, packages, facts, fact_packages, bypassed, fill_packages,
        )


def test_audit_rejects_tampering_duplicate_fields_and_backdating(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, policy, fill_packages, now = prepared(
        tmp_path, monkeypatch,
    )
    path = shadow_fills.append_fill_event(
        tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
    )
    original = path.read_text()
    path.write_text('{"scoring":"SCORED",' + original[1:])
    with pytest.raises(ValueError, match="duplicate fill-event field"):
        shadow_fills.audit_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )
    path.write_text(original)
    event = json.loads(original)
    event["recorded_at"] = (now - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="does not bind authenticated inputs"):
        shadow_fills.audit_fill_event(
            tmp_path, item, packages, facts, fact_packages, policy, fill_packages,
        )


@pytest.mark.parametrize("field,value", [
    ("entry_slippage_bps", Decimal("NaN")),
    ("cost_bps_per_side", Decimal("-1")),
    ("max_volume_participation_pct", Decimal("1.01")),
])
def test_policy_rejects_invalid_exact_economics(field, value):
    values = dict(
        market="US", currency="USD", entry_slippage_bps=Decimal(0),
        cost_bps_per_side=Decimal(0), fixed_cost_per_side=Decimal(0),
        max_volume_participation_pct=Decimal("0.1"),
        slippage_evidence_package_id="a" * 64, cost_evidence_package_id="b" * 64,
        participation_evidence_package_id="c" * 64,
    )
    with pytest.raises(ValidationError):
        ShadowFillPolicy(**(values | {field: value}))
