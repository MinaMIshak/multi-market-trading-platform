"""Artificial allocation fixtures only; no authentic capital or performance."""
import json
import hashlib
from datetime import timedelta
from decimal import Decimal

import pytest

from app.paper import shadow_allocations, shadow_exits, shadow_portfolio, shadow_positions
from app.paper.shadow_exits import EXIT_COST_FIELDS, EXIT_SLIPPAGE_FIELDS, ShadowExitPolicy
from app.paper.shadow_records import ShadowEvidenceReference
from tests.test_shadow_collection import package
from tests.test_shadow_positions import setup_position


def rewrite_reservation(path, event):
    basis = {key: value for key, value in event.items() if key not in {"reservation_id", "recorded_at"}}
    event["reservation_id"] = hashlib.sha256(shadow_allocations._canonical(basis)).hexdigest()
    path.write_text(json.dumps(event))


def setup_allocation(tmp_path, monkeypatch, **changes):
    args, now, _ = setup_position(tmp_path, monkeypatch)
    shadow_positions.append_position_open_event(tmp_path, *args)
    item = args[0]
    values = dict(
        base_currency="USD", initial_capital=Decimal("1000"),
        effective_at=item.information_cutoff - timedelta(minutes=1),
        egx_capital_fraction=Decimal(".3"), us_capital_fraction=Decimal(".6"),
        minimum_cash_fraction=Decimal(".1"), max_position_fraction=Decimal(".2"),
        max_position_risk_fraction=Decimal(".1"), max_portfolio_risk_fraction=Decimal(".2"),
    )
    portfolio = shadow_portfolio.ShadowPortfolioPolicy(**(values | changes))
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: item.information_cutoff - timedelta(minutes=2))
    shadow_portfolio.freeze_portfolio_policy(tmp_path, portfolio)
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: now)
    monkeypatch.setattr(shadow_allocations, "_now", lambda: now)
    return args, portfolio, now


def setup_settlement(tmp_path, monkeypatch, *, closed=True, **portfolio_changes):
    args, now, _ = setup_position(
        tmp_path, monkeypatch, bar_changes={"low": Decimal("95")} if closed else None,
    )
    shadow_positions.append_position_open_event(tmp_path, *args)
    item = args[0]
    values = dict(
        base_currency="USD", initial_capital=Decimal("1000"),
        effective_at=item.information_cutoff - timedelta(minutes=1),
        egx_capital_fraction=Decimal(".3"), us_capital_fraction=Decimal(".2"),
        minimum_cash_fraction=Decimal(".1"), max_position_fraction=Decimal(".2"),
        max_position_risk_fraction=Decimal(".1"), max_portfolio_risk_fraction=Decimal(".2"),
    ) | portfolio_changes
    portfolio = shadow_portfolio.ShadowPortfolioPolicy(**values)
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: item.information_cutoff - timedelta(minutes=2))
    shadow_portfolio.freeze_portfolio_policy(tmp_path, portfolio)
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: now)
    monkeypatch.setattr(shadow_allocations, "_now", lambda: now)
    shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    evidence = []
    for char, fields in zip("12", (EXIT_SLIPPAGE_FIELDS, EXIT_COST_FIELDS), strict=True):
        ref = ShadowEvidenceReference(
            evidence_id=char * 64, source_authority="official fixture authority",
            source_locator=f"fixture://settlement/{char}", artifact_sha256=char * 64,
            available_at=item.information_cutoff,
        )
        evidence.append(package(char, ref, tuple(fields)))
    exit_policy = ShadowExitPolicy(
        market="US", currency="USD", stop_slippage_bps=Decimal("20"),
        target_slippage_bps=Decimal("10"), cost_bps_per_side=Decimal("5"),
        fixed_cost_per_side=Decimal("1"),
        slippage_evidence_package_id=evidence[0].identity,
        cost_evidence_package_id=evidence[1].identity,
    )
    monkeypatch.setattr(shadow_exits, "_now", lambda: now)
    shadow_exits.append_exit_event(tmp_path, *args, exit_policy, tuple(evidence))
    return args, portfolio, exit_policy, tuple(evidence), now


def test_reserves_exact_cash_and_conservative_stop_risk(tmp_path, monkeypatch):
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    event = shadow_allocations.audit_capital_reservation(tmp_path, *args, portfolio)
    assert event == json.loads(path.read_bytes())
    assert event["capital_reserved"] == "101.15005"
    assert event["risk_reserved"] == "7.19755"
    assert event["totals_after"] == {"capital_reserved": "101.15005", "risk_reserved": "7.19755"}
    assert event["portfolio_status"] == "SHARED CAPITAL RESERVED / NO NAV OR P&L"
    with pytest.raises(FileExistsError):
        shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)


@pytest.mark.parametrize("field", ["capital_reserved", "risk_reserved", "prior_reservation_ids", "extra"])
def test_audit_rejects_reservation_tampering(tmp_path, monkeypatch, field):
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    event = json.loads(path.read_bytes())
    event[field] = True if field == "extra" else (["0" * 64] if field == "prior_reservation_ids" else "0")
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError):
        shadow_allocations.audit_capital_reservation(tmp_path, *args, portfolio)


@pytest.mark.parametrize("change,match", [
    ({"max_position_fraction": Decimal(".1")}, "position capital cap"),
    ({"max_position_risk_fraction": Decimal(".005")}, "position risk cap"),
    ({"us_capital_fraction": Decimal(".1")}, "market sleeve cap"),
])
def test_caps_fail_closed(tmp_path, monkeypatch, change, match):
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch, **change)
    with pytest.raises(ValueError, match=match):
        shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    assert not (tmp_path / "capital-reservations").exists()


def test_policy_must_be_effective_by_cutoff(tmp_path, monkeypatch):
    args, _, now = setup_allocation(tmp_path, monkeypatch)
    item = args[0]
    (tmp_path / "portfolio-policy.json").unlink()
    late = shadow_portfolio.ShadowPortfolioPolicy(
        base_currency="USD", initial_capital=Decimal("1000"),
        effective_at=item.information_cutoff + timedelta(seconds=1),
        egx_capital_fraction=Decimal(".3"), us_capital_fraction=Decimal(".6"),
        minimum_cash_fraction=Decimal(".1"), max_position_fraction=Decimal(".2"),
        max_position_risk_fraction=Decimal(".1"), max_portfolio_risk_fraction=Decimal(".2"),
    )
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: item.information_cutoff - timedelta(minutes=1))
    shadow_portfolio.freeze_portfolio_policy(tmp_path, late)
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: now)
    with pytest.raises(ValueError, match="frozen and effective"):
        shadow_allocations.append_capital_reservation(tmp_path, *args, late)


def test_foreign_currency_requires_admitted_pit_fx(tmp_path, monkeypatch):
    args, portfolio, now = setup_allocation(tmp_path, monkeypatch)
    (tmp_path / "portfolio-policy.json").unlink()
    egp_base = portfolio.model_copy(update={"base_currency": "EGP"})
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: args[0].information_cutoff - timedelta(minutes=2))
    shadow_portfolio.freeze_portfolio_policy(tmp_path, egp_base)
    monkeypatch.setattr(shadow_portfolio, "_now", lambda: now)
    with pytest.raises(ValueError, match="point-in-time FX"):
        shadow_allocations.append_capital_reservation(tmp_path, *args, egp_base)


def test_existing_exposure_is_included_in_sleeve_gate(tmp_path, monkeypatch):
    args, portfolio, _ = setup_allocation(
        tmp_path, monkeypatch, us_capital_fraction=Decimal(".2"),
    )
    shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    receipt = shadow_portfolio.audit_portfolio_policy(tmp_path, portfolio)
    existing = shadow_allocations._read_reservations(tmp_path)
    with pytest.raises(ValueError, match="market sleeve cap"):
        shadow_allocations._basis(position, receipt, portfolio, args[4], existing)


@pytest.mark.parametrize("field,value,match", [
    ("totals_after", {"capital_reserved": "0", "risk_reserved": "0"}, "cumulative total"),
    ("totals_after", {"capital_reserved": "101.15005"}, "unexpected.*totals"),
    ("capital_reserved", "NaN", "invalid.*amount"),
    ("risk_reserved", "-1", "invalid.*amount"),
    ("event_type", "CAPITAL_RELEASED", "semantics"),
    ("scoring", "VALIDATED", "semantics"),
    ("position_event_sha256", "bad", "reference"),
])
def test_append_rejects_rehashed_malformed_predecessor(tmp_path, monkeypatch, field, value, match):
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    event = json.loads(path.read_bytes())
    event[field] = value
    rewrite_reservation(path, event)
    with pytest.raises(ValueError, match=match):
        shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)


def test_append_rejects_future_predecessor(tmp_path, monkeypatch):
    args, portfolio, now = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    event = json.loads(path.read_bytes())
    event["recorded_at"] = (now + timedelta(seconds=1)).isoformat()
    rewrite_reservation(path, event)
    with pytest.raises(ValueError, match="future"):
        shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)


def test_reservation_cannot_be_renamed(tmp_path, monkeypatch):
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    path.rename(path.with_name("renamed.json"))
    with pytest.raises(ValueError, match="filename"):
        shadow_allocations._read_reservations(tmp_path)


@pytest.mark.parametrize("violation", ["clock", "position", "total", "none"])
def test_chain_checks_every_predecessor(tmp_path, monkeypatch, violation):
    args, portfolio, now = setup_allocation(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    first = json.loads(path.read_bytes())
    second = first | {
        "candidate_position_key": "a" * 64,
        "position_event_id": "b" * 64,
        "prior_reservation_ids": [first["reservation_id"]],
        "totals_after": {"capital_reserved": "202.30010", "risk_reserved": "14.39510"},
    }
    if violation == "clock":
        second["recorded_at"] = (now - timedelta(seconds=1)).isoformat()
    elif violation == "position":
        second["position_event_id"] = first["position_event_id"]
    elif violation == "total":
        second["totals_after"] = first["totals_after"]
    rewrite_reservation(path.with_name(second["candidate_position_key"] + ".json"), second)
    if violation == "none":
        assert len(shadow_allocations._read_reservations(tmp_path)) == 2
    else:
        with pytest.raises(ValueError, match={"clock": "clock ordering", "position": "duplicate", "total": "cumulative total"}[violation]):
            shadow_allocations._read_reservations(tmp_path)


def test_closed_exit_settles_native_cash_and_releases_risk(tmp_path, monkeypatch):
    args, portfolio, exit_policy, evidence, _ = setup_settlement(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence,
    )
    event = json.loads(path.read_bytes())
    assert event["capital_released"] == "101.15005"
    assert event["risk_released"] == "7.19755"
    assert event["exit_notional"] == "94.810"
    assert event["exit_cost"] == "1.047405"
    assert event["net_exit_proceeds"] == "93.762595"
    assert event["performance_status"] == "NATIVE CASH FLOW / NO NAV OR PERFORMANCE"
    reservations = shadow_allocations._read_reservations(tmp_path)
    assert shadow_allocations._read_settlements(tmp_path, reservations) == [event]
    assert shadow_allocations.audit_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence,
    ) == event
    with pytest.raises(FileExistsError):
        shadow_allocations.append_capital_settlement(
            tmp_path, *args, portfolio, exit_policy, evidence,
        )


def test_settlement_audit_requires_durable_receipt(tmp_path, monkeypatch):
    args, portfolio, exit_policy, evidence, _ = setup_settlement(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError, match="settlement absent"):
        shadow_allocations.audit_capital_settlement(
            tmp_path, *args, portfolio, exit_policy, evidence,
        )


def test_open_or_unknown_exit_cannot_release_capital(tmp_path, monkeypatch):
    args, portfolio, exit_policy, evidence, _ = setup_settlement(
        tmp_path, monkeypatch, closed=False,
    )
    with pytest.raises(ValueError, match="requires authenticated CLOSED exit"):
        shadow_allocations.append_capital_settlement(
            tmp_path, *args, portfolio, exit_policy, evidence,
        )
    assert not (tmp_path / "capital-settlements").exists()


def test_settlement_tampering_blocks_reuse(tmp_path, monkeypatch):
    args, portfolio, exit_policy, evidence, _ = setup_settlement(tmp_path, monkeypatch)
    path = shadow_allocations.append_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence,
    )
    event = json.loads(path.read_bytes())
    event["net_exit_proceeds"] = "1000"
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="cash flow"):
        shadow_allocations._read_settlements(
            tmp_path, shadow_allocations._read_reservations(tmp_path),
        )


def test_excess_settled_loss_halts_reuse_but_preserves_historical_audit(tmp_path, monkeypatch):
    args, portfolio, exit_policy, evidence, _ = setup_settlement(tmp_path, monkeypatch)
    shadow_allocations.append_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence,
    )
    reservations = shadow_allocations._read_reservations(tmp_path)
    settlements = shadow_allocations._read_settlements(tmp_path, reservations)
    position = shadow_positions.audit_position_open_event(tmp_path, *args) | {
        "candidate_position_key": "a" * 64, "event_id": "b" * 64,
    }
    receipt = shadow_portfolio.audit_portfolio_policy(tmp_path, portfolio)
    with pytest.raises(ValueError, match="settled loss exceeds reserved risk"):
        shadow_allocations._basis(position, receipt, portfolio, args[4], reservations, settlements)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError, match="settled loss exceeds reserved risk"):
        shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    assert shadow_allocations.audit_capital_reservation(tmp_path, *args, portfolio) == reservations[0]


@pytest.mark.parametrize("loss,halt", [
    ("0", False), ("7.19754", False), ("7.19755", False),
    ("7.197550000000000000000000000001", True),
])
def test_excess_loss_exact_boundary_and_no_profit_offset(tmp_path, monkeypatch, loss, halt):
    # Artificial arithmetic fixtures; public settlement authentication is tested above.
    from decimal import localcontext
    args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
    shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    reservations = shadow_allocations._read_reservations(tmp_path)
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    receipt = shadow_portfolio.audit_portfolio_policy(tmp_path, portfolio)
    with localcontext() as ctx:
        ctx.prec = 60
        proceeds = str(Decimal("101.15005") - Decimal(loss))
    settlements = [dict(reservation_id=reservations[0]["reservation_id"], market="US",
                        capital_released="101.15005", net_exit_proceeds=proceeds,
                        risk_released="7.19755"),
                   dict(reservation_id="a" * 64, market="EGX", capital_released="100",
                        net_exit_proceeds="200", risk_released="10")]
    with localcontext() as ctx:
        ctx.prec = 6
        if halt:
            with pytest.raises(ValueError, match="settled loss exceeds reserved risk"):
                shadow_allocations._basis(position, receipt, portfolio, args[4], reservations, settlements)
        else:
            result = shadow_allocations._basis(position, receipt, portfolio, args[4], reservations, settlements)
            assert result["capital_reserved"] == "101.15005"
