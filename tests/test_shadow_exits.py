"""Artificial exit fixtures only; no authentic trades or performance."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.paper import shadow_exits, shadow_positions
from app.paper.shadow_exits import EXIT_COST_FIELDS, EXIT_PARTICIPATION_FIELDS, EXIT_SLIPPAGE_FIELDS, ShadowExitPolicy
from app.paper.shadow_records import ShadowEvidenceReference
from tests.test_shadow_collection import package
from tests.test_shadow_positions import setup_position


def prepared_exit(tmp_path, monkeypatch, **kwargs):
    args, now, _ = setup_position(tmp_path, monkeypatch, **kwargs)
    shadow_positions.append_position_open_event(tmp_path, *args)
    evidence = []
    for char, fields in zip("123", (EXIT_SLIPPAGE_FIELDS, EXIT_COST_FIELDS, EXIT_PARTICIPATION_FIELDS), strict=True):
        ref = ShadowEvidenceReference(
            evidence_id=char * 64, source_authority="official fixture authority",
            source_locator=f"fixture://exit/{char}", artifact_sha256=char * 64,
            available_at=args[0].information_cutoff,
        )
        evidence.append(package(char, ref, tuple(fields)))
    policy = ShadowExitPolicy(
        market="US", currency="USD", stop_slippage_bps=Decimal("20"),
        target_slippage_bps=Decimal("10"), cost_bps_per_side=Decimal("5"),
        fixed_cost_per_side=Decimal("1"),
        max_volume_participation_pct=Decimal("0.1"),
        participation_evidence_package_id=evidence[2].identity,
        slippage_evidence_package_id=evidence[0].identity,
        cost_evidence_package_id=evidence[1].identity,
    )
    monkeypatch.setattr(shadow_exits, "_now", lambda: now)
    return args, now, policy, tuple(evidence)


def test_open_evaluation_is_durable_and_has_no_performance(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    path = shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    event = shadow_exits.audit_exit_event(tmp_path, *args, policy, evidence)
    assert event == json.loads(path.read_bytes())
    assert event["evaluation"] == {
        "status": "OPEN", "reason": "NO_EXIT_OBSERVED",
        "evaluated_through_sequence": 1, "exit": None,
    }
    assert event["performance_status"] == "NO P&L OR NAV"
    with pytest.raises(FileExistsError):
        shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)


def test_conservative_outcomes_from_exact_models(tmp_path, monkeypatch):
    args, _, policy, _ = prepared_exit(tmp_path, monkeypatch)
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    facts = args[2]
    target = facts.model_copy(update={"bars": (facts.bars[0].model_copy(update={"high": Decimal("110")}),)})
    result = shadow_exits.evaluate_exit(position, target, policy)
    assert (result["reason"], result["exit"]["raw_price"], result["exit"]["fill_price"],
            result["exit"]["exit_cost"]) == ("TARGET_1", "110", "109.890", "1.054945")
    stop = facts.model_copy(update={"bars": (facts.bars[0].model_copy(update={"low": Decimal("95")}),)})
    result = shadow_exits.evaluate_exit(position, stop, policy)
    assert (result["reason"], result["exit"]["raw_price"], result["exit"]["fill_price"]) == (
        "STOP", "95", "94.810",
    )
    both = facts.model_copy(update={"bars": (facts.bars[0].model_copy(update={
        "high": Decimal("110"), "low": Decimal("95"),
    }),)})
    assert shadow_exits.evaluate_exit(position, both, policy)["status"] == "UNKNOWN"


def test_audit_rejects_tampering_and_upstream_corruption(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    path = shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    event = json.loads(path.read_bytes())
    event["evaluation"]["status"] = "CLOSED"
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="does not bind"):
        shadow_exits.audit_exit_event(tmp_path, *args, policy, evidence)


def test_policy_evidence_and_exact_decimals_fail_closed(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="match references exactly"):
        shadow_exits.append_exit_event(tmp_path, *args, policy, evidence[:1])
    with pytest.raises(ValidationError):
        ShadowExitPolicy(**(policy.model_dump(mode="python") | {
            "stop_slippage_bps": Decimal("NaN"),
        }))


@pytest.mark.parametrize("volume,expected", [(0, "UNKNOWN"), (19, "UNKNOWN"), (20, "CLOSED")])
def test_same_bar_exit_shares_capacity_with_entry(tmp_path, monkeypatch, volume, expected):
    args, _, policy, _ = prepared_exit(tmp_path, monkeypatch)
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    facts = args[2]
    facts = facts.model_copy(update={"bars": (facts.bars[0].model_copy(update={
        "low": Decimal("95"), "volume": volume,
    }),)})
    result = shadow_exits.evaluate_exit(position, facts, policy)
    assert result["status"] == expected
    if expected == "UNKNOWN":
        assert result == {"status": "UNKNOWN", "reason": "INSUFFICIENT_EXIT_CAPACITY",
                          "evaluated_through_sequence": 1, "exit": None}


@pytest.mark.parametrize("volume,expected", [(0, "UNKNOWN"), (9, "UNKNOWN"), (10, "CLOSED")])
def test_later_gap_exit_requires_its_own_capacity(tmp_path, monkeypatch, volume, expected):
    args, _, policy, _ = prepared_exit(tmp_path, monkeypatch)
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    facts = args[2]
    first = facts.bars[0]
    later = first.model_copy(update={
        "sequence": 2, "open": Decimal("94"), "low": Decimal("93"),
        "high": Decimal("96"), "close": Decimal("95"), "volume": volume,
        "interval_start": first.interval_end,
        "interval_end": first.interval_end + timedelta(minutes=1),
        "available_at": first.available_at + timedelta(minutes=1),
    })
    result = shadow_exits.evaluate_exit(position, facts.model_copy(update={"bars": (first, later)}), policy)
    assert result["status"] == expected
    assert result["evaluated_through_sequence"] == 2
    if expected == "UNKNOWN":
        assert result["reason"] == "INSUFFICIENT_EXIT_CAPACITY"
        assert result["exit"] is None
    else:
        assert result["reason"] == "STOP_GAP"
        assert result["exit"]["raw_price"] == "94"


def test_capacity_unknown_is_durable_and_has_no_reported_pnl(tmp_path, monkeypatch):
    from app.paper.shadow_report import exit_evaluation_view

    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch, bar_changes={"low": Decimal("95")})
    policy = policy.model_copy(update={"max_volume_participation_pct": Decimal("0.001")})
    shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    event = shadow_exits.audit_exit_event(tmp_path, *args, policy, evidence)
    assert event["evaluation"]["reason"] == "INSUFFICIENT_EXIT_CAPACITY"
    view = exit_evaluation_view(tmp_path, *args, policy, evidence)
    assert view["position_status"] == "UNKNOWN"
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert view["performance"]["nav"] is None


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("1.01"), Decimal("NaN"), 0.1])
def test_exit_participation_must_be_exact_bounded_decimal(tmp_path, monkeypatch, value):
    _, _, policy, _ = prepared_exit(tmp_path, monkeypatch)
    with pytest.raises(ValidationError):
        ShadowExitPolicy(**(policy.model_dump(mode="python") | {"max_volume_participation_pct": value}))


def test_exit_participation_requires_evidence_before_publication(tmp_path, monkeypatch):
    args, _, policy, evidence = prepared_exit(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="match references exactly"):
        shadow_exits.append_exit_event(tmp_path, *args, policy, evidence[:2])
    bad = policy.model_copy(update={"participation_evidence_package_id": evidence[0].identity})
    with pytest.raises(ValueError, match="cover exit-policy fields"):
        shadow_exits.append_exit_event(tmp_path, *args, bad, evidence[:2])
    assert not (tmp_path / "exit-events").exists()
