"""Software-only report fixtures; no empirical recommendations or results."""
import json
from decimal import Context, Decimal, localcontext

import pytest

from app.paper import shadow_collection, shadow_exits, shadow_ledger, shadow_positions
from app.paper.shadow_report import (
    capital_settlement_view, continuation_capital_settlement_view,
    continuation_exit_evaluation_view, exit_evaluation_view,
    missed_collection_view, position_open_view, trigger_evaluation_view,
    watchlist_collection_view,
)
from tests.test_shadow_collection import authenticated_watchlist
from tests.test_shadow_exits import prepared_exit
from tests.test_shadow_ledger import completed
from tests.test_shadow_positions import setup_position
from tests.test_shadow_allocations import (
    setup_continuation_settlement, setup_settlement,
)


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


@pytest.mark.parametrize("close", ["99", "100.100", "101"])
def test_exit_view_reports_gross_mark_pnl_without_net_liquidation_claim(tmp_path, monkeypatch, close):
    args, _, policy, evidence = prepared_exit(
        tmp_path, monkeypatch, bar_changes={"close": Decimal(close)},
    )
    shadow_exits.append_exit_event(tmp_path, *args, policy, evidence)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with localcontext(Context(prec=3)):
        view = exit_evaluation_view(tmp_path, *args, policy, evidence)
    assert view["position_status"] == "OPEN"
    assert view["exit_evaluation"] == {
        "status": "OPEN", "reason": "NO_EXIT_OBSERVED",
        "evaluated_through_sequence": 1, "exit": None,
    }
    open_position = view["open_paper_positions"]
    assert open_position["status"] == "ONE AUTHENTICATED OPEN POSITION AS OF OBSERVED BAR"
    mark = open_position["position"]
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    assert mark["mark_price"] == close
    assert Decimal(mark["gross_market_value"]) == (
        Decimal(mark["quantity"]) * Decimal(mark["mark_price"])
    )
    assert mark["marked_through_sequence"] == 1
    assert mark["mark_interval_end"] == args[2].bars[-1].interval_end.isoformat()
    assert mark["mark_known_at"] == args[2].bars[-1].available_at.isoformat()
    assert mark["initial_stop"] == position["initial_stop"]
    assert mark["initial_targets"] == position["initial_targets"]
    assert mark["holding_window"] == position["holding_window"]
    assert mark["observed_bar_end_elapsed"] == "0:00:00"
    assert "INTRABAR FILL TIME UNKNOWN" in mark["observed_bar_end_elapsed_status"]
    assert mark["unrealized_pnl"] is None
    with localcontext(Context(prec=34)):
        expected = Decimal(mark["quantity"]) * Decimal(close) - Decimal(position["entry"]["notional"])
        expected_return = expected / Decimal(position["entry"]["notional"])
    assert Decimal(mark["gross_unrealized_pnl"]) == expected
    assert Decimal(mark["gross_unrealized_return"]) == expected_return
    assert "BEFORE FEES AND LIQUIDATION SLIPPAGE" in mark["gross_unrealized_pnl_status"]
    assert "UNKNOWN" in mark["unrealized_pnl_status"]
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert all(value is None for key, value in view["performance"].items() if key != "status")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


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
    position = shadow_positions.audit_position_open_event(tmp_path, *args)
    assert trade["initial_stop"] == position["initial_stop"]
    assert trade["initial_targets"] == position["initial_targets"]
    assert trade["holding_window"] == position["holding_window"]
    assert trade["observed_bar_end_elapsed"] == "0:00:00"
    assert "INTRABAR FILL TIME UNKNOWN" in trade["observed_bar_end_elapsed_status"]
    with localcontext(Context(prec=34)):
        assert Decimal(trade["gross_return"]) == (
            Decimal(trade["gross_pnl"]) / Decimal(trade["entry_notional"])
        )
        assert Decimal(trade["net_return"]) == Decimal(trade["net_pnl"]) / (
            Decimal(trade["entry_notional"]) + Decimal(trade["entry_cost"])
        )
    assert view["performance"]["nav"] is None


def test_settlement_view_reports_native_cash_without_performance(tmp_path, monkeypatch):
    from app.paper import shadow_allocations

    args, portfolio, exit_policy, evidence, _ = setup_settlement(tmp_path, monkeypatch)
    shadow_allocations.append_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence,
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = capital_settlement_view(
        tmp_path, *args, portfolio, exit_policy, evidence,
    )
    assert view["collection_status"] == "CAPITAL SETTLED"
    assert view["position_status"] == "CLOSED"
    cash = view["capital_settlement"]
    assert cash == {
        "status": "AUTHENTICATED NATIVE CASH FLOW / NO NAV OR PERFORMANCE",
        "market": "US", "currency": "USD", "capital_released": "101.15005",
        "risk_released": "7.19755", "exit_notional": "94.810",
        "exit_cost": "1.047405", "net_exit_proceeds": "93.762595",
        "recorded_at": cash["recorded_at"],
    }
    assert view["audit_references"]["settlement_id"]
    assert view["audit_references"]["reservation_id"]
    assert all(value is None for key, value in view["performance"].items() if key != "status")
    json.dumps(view, allow_nan=False)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


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


@pytest.mark.parametrize("participation,expected", [
    (Decimal("0.10"), "SIMULATED ENTRY FILL CREATED"),
    (Decimal("0.0009"), "NO FILL: INSUFFICIENT CAPACITY"),
])
def test_entry_view_preserves_fill_and_no_fill_without_position_claim(
    tmp_path, monkeypatch, participation, expected,
):
    from app.paper.shadow_fills import append_fill_event
    from app.paper.shadow_report import entry_fill_view
    from tests.test_shadow_fills import prepared

    args = prepared(tmp_path, monkeypatch, participation=participation)[:-1]
    with pytest.raises(FileNotFoundError):
        entry_fill_view(tmp_path, *args)
    path = append_fill_event(tmp_path, *args)
    event = json.loads(path.read_bytes())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = entry_fill_view(tmp_path, *args)
    assert view["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert view["scoring"] == "NOT SCORED"
    assert view["execution_status"] == expected
    assert view["fill"] == event["fill"]
    assert (view["fill"] is None) == (participation == Decimal("0.0009"))
    assert view["fill_policy"] == event["policy"]
    assert view["audit_references"]["fill_event_id"] == event["event_id"]
    assert view["current_position_status"].startswith("UNKNOWN")
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert all(v is None for k, v in view["performance"].items() if k != "status")
    json.dumps(view, allow_nan=False)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    event["execution_status"] = "fabricated successful fill"
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="does not bind authenticated inputs"):
        entry_fill_view(tmp_path, *args)


@pytest.mark.parametrize("changes,expected", [
    ({"open": Decimal("105"), "high": Decimal("106"), "low": Decimal("102"),
      "close": Decimal("104")}, "NOT_TRIGGERED"),
    ({"open": Decimal("94"), "high": Decimal("100"), "low": Decimal("90"),
      "close": Decimal("96")}, "INVALIDATED_OPEN_GAP"),
    ({"open": Decimal("105"), "high": Decimal("111"), "low": Decimal("100"),
      "close": Decimal("101")}, "TRIGGERED_AMBIGUOUS_BAR"),
])
def test_trigger_view_preserves_non_fill_outcomes_without_scoring(
    tmp_path, monkeypatch, changes, expected,
):
    from app.paper import shadow_triggers
    from tests.test_shadow_triggers import admitted

    args = admitted(tmp_path, monkeypatch, changes)[:4]
    with pytest.raises(FileNotFoundError):
        trigger_evaluation_view(tmp_path, *args)
    path = shadow_triggers.append_trigger_event(tmp_path, *args)
    event = json.loads(path.read_bytes())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = trigger_evaluation_view(tmp_path, *args)
    assert view["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert view["scoring"] == "NOT SCORED"
    assert view["trigger_evaluation"] == event["evaluation"]
    assert view["trigger_evaluation"]["status"] == expected
    assert view["execution_status"] == "NO FILL OR POSITION CREATED"
    assert view["current_position_status"] == "NO FILL OR POSITION CREATED BY THIS EVENT"
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert all(v is None for k, v in view["performance"].items() if k != "status")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    event["evaluation"]["status"] = "TRIGGERED"
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="does not bind evaluation"):
        trigger_evaluation_view(tmp_path, *args)

# --- continuation report integration ---

def test_continuation_exit_view_reports_closed_native_pnl(tmp_path, monkeypatch):
    (
        args, _, chain, chain_packages,
        exit_policy, exit_packages, _,
    ) = setup_continuation_settlement(tmp_path, monkeypatch)

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    view = continuation_exit_evaluation_view(
        tmp_path, *args, chain, chain_packages, exit_policy, exit_packages,
    )

    assert view["collection_status"] == "CONTINUATION EXIT EVALUATED"
    assert view["position_status"] == "CLOSED"
    assert view["entry_session_exit_evaluation"]["status"] == "OPEN"
    assert view["continuation_evaluations"][-1]["status"] == "CLOSED"

    trade = view["closed_paper_trades"]["trade"]
    assert trade["currency"] == "USD"

    assert Decimal(trade["gross_pnl"]) == (
        Decimal(trade["exit_notional"]) - Decimal(trade["entry_notional"])
    )
    assert Decimal(trade["net_pnl"]) == (
        Decimal(trade["gross_pnl"])
        - Decimal(trade["entry_cost"])
        - Decimal(trade["exit_cost"])
    )

    with localcontext(Context(prec=34)):
        assert Decimal(trade["gross_return"]) == (
            Decimal(trade["gross_pnl"]) / Decimal(trade["entry_notional"])
        )
        assert Decimal(trade["net_return"]) == (
            Decimal(trade["net_pnl"])
            / (Decimal(trade["entry_notional"]) + Decimal(trade["entry_cost"]))
        )

    assert trade["observed_bar_end_elapsed"] != "0:00:00"
    assert len(view["audit_references"]["continuation_events"]) == len(chain)
    assert all(
        value is None
        for key, value in view["performance"].items()
        if key != "status"
    )
    json.dumps(view, allow_nan=False)

    assert before == {
        p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }


def test_continuation_open_view_reports_authenticated_asof_gross_mark(
    tmp_path, monkeypatch,
):
    (
        args, _, chain, chain_packages,
        exit_policy, exit_packages, _,
    ) = setup_continuation_settlement(
        tmp_path, monkeypatch, result="OPEN",
    )

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    view = continuation_exit_evaluation_view(
        tmp_path, *args, chain, chain_packages, exit_policy, exit_packages,
    )

    assert view["position_status"] == "OPEN"
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}

    opened = view["open_paper_positions"]
    assert opened["status"] == "ONE AUTHENTICATED OPEN POSITION AS OF OBSERVED BAR"

    mark = opened["position"]
    observed = chain[-1].bars[-1]
    position = shadow_positions.audit_position_open_event(tmp_path, *args)

    assert mark["currency"] == "USD"
    assert mark["mark_price"] == str(observed.close)
    assert mark["marked_through_sequence"] == observed.sequence
    assert mark["mark_interval_end"] == observed.interval_end.isoformat()
    assert mark["mark_known_at"] == observed.available_at.isoformat()

    with localcontext(Context(prec=34)):
        expected_value = Decimal(mark["quantity"]) * observed.close
        expected_pnl = expected_value - Decimal(position["entry"]["notional"])
        expected_return = expected_pnl / Decimal(position["entry"]["notional"])

    assert Decimal(mark["gross_market_value"]) == expected_value
    assert Decimal(mark["gross_unrealized_pnl"]) == expected_pnl
    assert Decimal(mark["gross_unrealized_return"]) == expected_return
    assert mark["unrealized_pnl"] is None
    assert "BEFORE FEES AND LIQUIDATION SLIPPAGE" in (
        mark["gross_unrealized_pnl_status"]
    )
    assert "UNKNOWN" in mark["unrealized_pnl_status"]
    assert mark["observed_bar_end_elapsed"] != "0:00:00"

    assert all(
        value is None
        for key, value in view["performance"].items()
        if key != "status"
    )
    json.dumps(view, allow_nan=False)

    assert before == {
        p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }


def test_continuation_unknown_view_exposes_no_mark_or_realized_pnl(
    tmp_path, monkeypatch,
):
    (
        args, _, chain, chain_packages,
        exit_policy, exit_packages, _,
    ) = setup_continuation_settlement(
        tmp_path, monkeypatch, result="UNKNOWN",
    )

    view = continuation_exit_evaluation_view(
        tmp_path, *args, chain, chain_packages, exit_policy, exit_packages,
    )

    assert view["position_status"] == "UNKNOWN"
    assert view["closed_paper_trades"] == {"status": "NOT EVALUATED"}
    assert view["open_paper_positions"] == {"status": "NOT EVALUATED"}

    assert all(
        value is None
        for key, value in view["performance"].items()
        if key != "status"
    )


def test_continuation_settlement_view_reports_cash_and_realized_trade(
    tmp_path, monkeypatch,
):
    from app.paper import shadow_allocations

    (
        args, portfolio, chain, chain_packages,
        exit_policy, exit_packages, _,
    ) = setup_continuation_settlement(tmp_path, monkeypatch)

    shadow_allocations.append_continuation_capital_settlement(
        tmp_path, *args, portfolio, chain, chain_packages,
        exit_policy, exit_packages,
    )

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    view = continuation_capital_settlement_view(
        tmp_path, *args, portfolio, chain, chain_packages,
        exit_policy, exit_packages,
    )

    assert view["collection_status"] == "CAPITAL SETTLED"
    assert view["position_status"] == "CLOSED"

    trade = view["closed_paper_trades"]["trade"]
    cash = view["capital_settlement"]

    assert cash["currency"] == trade["currency"] == "USD"
    assert cash["exit_notional"] == trade["exit_notional"]
    assert cash["exit_cost"] == trade["exit_cost"]
    assert Decimal(cash["net_exit_proceeds"]) == (
        Decimal(cash["exit_notional"]) - Decimal(cash["exit_cost"])
    )

    assert view["audit_references"]["reservation_id"]
    assert view["audit_references"]["settlement_id"]
    assert view["audit_references"]["portfolio_policy_id"]

    assert all(
        value is None
        for key, value in view["performance"].items()
        if key != "status"
    )

    json.dumps(view, allow_nan=False)

    assert before == {
        p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }


def test_continuation_report_reaudits_tampered_exit(tmp_path, monkeypatch):
    (
        args, _, chain, chain_packages,
        exit_policy, exit_packages, exit_path,
    ) = setup_continuation_settlement(tmp_path, monkeypatch)

    event = json.loads(exit_path.read_bytes())
    event["result"]["exit"]["notional"] = "999"
    exit_path.write_text(json.dumps(event))

    with pytest.raises(ValueError, match="does not bind authenticated inputs"):
        continuation_exit_evaluation_view(
            tmp_path, *args, chain, chain_packages,
            exit_policy, exit_packages,
        )


def setup_later_fact_settlement(tmp_path, monkeypatch):
    """Settle a position whose CLOSED exit is observed only on later admitted
    same-session facts; the entry-session facts alone remain OPEN."""
    from datetime import timedelta

    from app.paper import shadow_allocations, shadow_facts
    from app.paper.shadow_records import ShadowEvidenceReference
    from tests.test_shadow_collection import package

    args, portfolio, exit_policy, evidence, now = setup_settlement(
        tmp_path, monkeypatch, closed=False,
    )
    bar = args[2].bars[0]
    later_ref = ShadowEvidenceReference(
        evidence_id="d" * 64, source_authority="official fixture authority",
        source_locator="fixture://later-forward-bars", artifact_sha256="d" * 64,
        available_at=bar.available_at + timedelta(minutes=1),
    )
    later_package = package(
        "d", later_ref,
        tuple(shadow_facts.BAR_FIELDS | shadow_facts.TRADING_STATUS_FIELDS),
    )
    later_bar = bar.model_copy(update={
        "sequence": 2, "interval_start": bar.interval_end,
        "interval_end": bar.interval_end + timedelta(minutes=1),
        "available_at": bar.available_at + timedelta(minutes=1),
        "low": Decimal("95"), "evidence_package_id": later_package.identity,
    })
    later_facts = args[2].model_copy(update={
        "bars": (bar, later_bar),
        "trading_status": args[2].trading_status.model_copy(update={
            "coverage_end": later_bar.interval_end,
            "evidence_package_id": later_package.identity,
        }),
    })
    later_packages = args[3] + (later_package,)
    later_now = now + timedelta(minutes=1)
    monkeypatch.setattr(shadow_facts, "_now", lambda: later_now)
    shadow_facts.append_forward_fact_event(
        tmp_path, args[0], args[1], later_facts, later_packages,
    )
    monkeypatch.setattr(shadow_exits, "_now", lambda: later_now)
    monkeypatch.setattr(shadow_allocations, "_now", lambda: later_now)
    extension = {
        "evaluation_facts": later_facts, "evaluation_fact_packages": later_packages,
    }
    shadow_exits.append_exit_event(tmp_path, *args, exit_policy, evidence, **extension)
    shadow_allocations.append_capital_settlement(
        tmp_path, *args, portfolio, exit_policy, evidence, **extension,
    )
    return args, portfolio, exit_policy, evidence, later_facts, extension


def test_settlement_view_reaudits_later_same_session_fact_evaluation(tmp_path, monkeypatch):
    """The settlement must be displayable through the same evaluation facts it
    was audited against, and must still fail closed without them."""
    args, portfolio, exit_policy, evidence, later_facts, extension = (
        setup_later_fact_settlement(tmp_path, monkeypatch)
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    view = capital_settlement_view(
        tmp_path, *args, portfolio, exit_policy, evidence, **extension,
    )

    assert view["collection_status"] == "CAPITAL SETTLED"
    assert view["position_status"] == "CLOSED"
    assert view["exit_evaluation"]["evaluated_through_sequence"] == 2
    assert view["audit_references"]["fact_record_id"] == later_facts.record_id
    trade = view["closed_paper_trades"]["trade"]
    assert view["capital_settlement"]["exit_notional"] == trade["exit_notional"]
    assert view["capital_settlement"]["exit_cost"] == trade["exit_cost"]
    assert all(value is None for key, value in view["performance"].items() if key != "status")
    with pytest.raises(ValueError):
        capital_settlement_view(tmp_path, *args, portfolio, exit_policy, evidence)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
