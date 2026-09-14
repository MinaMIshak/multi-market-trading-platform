"""Software-only immutable portfolio snapshot fixtures."""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.paper import (
    shadow_allocations,
    shadow_daily_snapshots,
)
from app.paper.shadow_daily_portfolio import ContinuationMarkRequest
from app.paper.shadow_daily_snapshots import (
    append_cash_only_daily_portfolio_snapshot,
    append_marked_daily_portfolio_snapshot,
    audit_cash_only_daily_portfolio_snapshot,
    audit_marked_daily_portfolio_snapshot,
)
from tests.test_shadow_allocations import (
    setup_allocation,
    setup_continuation_settlement,
    setup_settlement,
)
from tests.test_shadow_multi_portfolio import (
    _setup_two_authenticated_open_positions,
    _single_session_requests,
)


def setup_cash_snapshot(tmp_path, monkeypatch):
    args, portfolio, now = setup_allocation(
        tmp_path,
        monkeypatch,
        us_capital_fraction=Decimal(".6"),
    )
    snapshot_at = now + timedelta(minutes=1)
    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )
    return args, portfolio, snapshot_at


def test_cash_only_daily_snapshot_is_immutable_and_auditable(
    tmp_path,
    monkeypatch,
):
    _, portfolio, snapshot_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )
    event = json.loads(path.read_bytes())

    assert path.name == f"{snapshot_at.date().isoformat()}.json"
    assert event["snapshot_date_utc"] == (
        snapshot_at.date().isoformat()
    )
    assert event["currency"] == "USD"
    assert event["ledger"] == {
        "reservations": [],
        "settlements": [],
    }
    assert event["mark_provenance"] == []

    valuation = event["valuation"]
    assert valuation["open_position_count"] == 0
    assert valuation["open_positions"] == []
    assert Decimal(valuation["open_market_value"]) == 0
    assert Decimal(valuation["gross_marked_nav"]) == Decimal(
        valuation["cash"]
    )

    assert audit_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
        snapshot_at.date(),
    ) == event


def test_cash_only_daily_snapshot_publishes_once(
    tmp_path,
    monkeypatch,
):
    _, portfolio, _ = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        append_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
        )

    assert path.read_bytes() == original


def test_cash_only_daily_snapshot_rejects_tampering(
    tmp_path,
    monkeypatch,
):
    _, portfolio, snapshot_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )
    event = json.loads(path.read_bytes())
    event["valuation"]["cash"] = "999"

    path.write_text(json.dumps(event))

    with pytest.raises(
        ValueError,
        match="snapshot hash mismatch",
    ):
        audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
        )


def test_later_reservation_does_not_rewrite_old_cash_snapshot(
    tmp_path,
    monkeypatch,
):
    args, portfolio, snapshot_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )
    original = path.read_bytes()

    later = snapshot_at + timedelta(days=1)

    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: later,
    )
    shadow_allocations.append_capital_reservation(
        tmp_path,
        *args,
        portfolio,
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: later,
    )

    audited = audit_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
        snapshot_at.date(),
    )

    assert path.read_bytes() == original
    assert audited == json.loads(original)


def test_cash_only_snapshot_rejects_active_reservation(
    tmp_path,
    monkeypatch,
):
    args, portfolio, now = setup_allocation(
        tmp_path,
        monkeypatch,
        us_capital_fraction=Decimal(".6"),
    )

    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: now,
    )
    shadow_allocations.append_capital_reservation(
        tmp_path,
        *args,
        portfolio,
    )

    snapshot_at = now + timedelta(minutes=1)
    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    with pytest.raises(
        ValueError,
        match="requires no active reservations",
    ):
        append_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
        )

    assert not (
        tmp_path / "daily-portfolio-snapshots"
    ).exists()


def setup_settled_cash_snapshot(
    tmp_path,
    monkeypatch,
):
    (
        args,
        portfolio,
        exit_policy,
        exit_packages,
        now,
    ) = setup_settlement(
        tmp_path,
        monkeypatch,
    )

    shadow_allocations.append_capital_settlement(
        tmp_path,
        *args,
        portfolio,
        exit_policy,
        exit_packages,
    )

    snapshot_at = now + timedelta(minutes=1)

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    return portfolio, snapshot_at, path


def _rehash_snapshot(event):
    basis = {
        key: value
        for key, value in event.items()
        if key not in {
            "snapshot_id",
            "recorded_at",
        }
    }

    event["snapshot_id"] = hashlib.sha256(
        shadow_daily_snapshots._canonical(basis)
    ).hexdigest()


def test_settled_cash_snapshot_rederives_all_accounting(
    tmp_path,
    monkeypatch,
):
    portfolio, snapshot_at, path = (
        setup_settled_cash_snapshot(
            tmp_path,
            monkeypatch,
        )
    )

    event = json.loads(path.read_bytes())
    valuation = event["valuation"]

    reservations = (
        shadow_allocations._read_reservations(
            tmp_path
        )
    )
    settlements = (
        shadow_allocations._read_settlements(
            tmp_path,
            reservations,
        )
    )

    assert len(reservations) == 1
    assert len(settlements) == 1

    assert valuation["reservation_count"] == 1
    assert valuation["active_reservation_count"] == 0
    assert valuation["settlement_count"] == 1

    assert Decimal(
        valuation["historical_capital_reserved"]
    ) == Decimal(
        reservations[0]["capital_reserved"]
    )

    assert Decimal(
        valuation["settled_net_exit_proceeds"]
    ) == Decimal(
        settlements[0]["net_exit_proceeds"]
    )

    assert Decimal(
        valuation["realized_pnl"]
    ) == (
        Decimal(
            settlements[0]["net_exit_proceeds"]
        )
        - Decimal(
            settlements[0]["capital_released"]
        )
    )

    assert (
        audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
        )
        == event
    )


def test_rehashed_snapshot_cannot_truncate_historical_ledger(
    tmp_path,
    monkeypatch,
):
    portfolio, snapshot_at, path = (
        setup_settled_cash_snapshot(
            tmp_path,
            monkeypatch,
        )
    )

    event = json.loads(path.read_bytes())

    event["ledger"] = {
        "reservations": [],
        "settlements": [],
    }

    _rehash_snapshot(event)

    path.write_text(
        json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match="does not match historical cutoff",
    ):
        audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
        )


def test_rehashed_snapshot_cannot_rewrite_accounting(
    tmp_path,
    monkeypatch,
):
    portfolio, snapshot_at, path = (
        setup_settled_cash_snapshot(
            tmp_path,
            monkeypatch,
        )
    )

    event = json.loads(path.read_bytes())

    event["valuation"][
        "historical_capital_reserved"
    ] = "0"

    _rehash_snapshot(event)

    path.write_text(
        json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match="valuation does not bind frozen ledger",
    ):
        audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
        )


def setup_two_position_marked_snapshot(
    tmp_path,
    monkeypatch,
):
    (
        args1,
        args2,
        portfolio,
        exit_policy,
        exit_packages,
    ) = _setup_two_authenticated_open_positions(
        tmp_path,
        monkeypatch,
    )

    requests = _single_session_requests(
        args1,
        args2,
        exit_policy,
        exit_packages,
    )

    exit_times = [
        datetime.fromisoformat(
            json.loads(path.read_bytes())["recorded_at"]
        )
        for path in sorted(
            (tmp_path / "exit-events").glob("*.json")
        )
    ]

    if len(exit_times) != 2:
        raise AssertionError(
            "expected exactly two authenticated exit events"
        )

    mark_times = [
        request.facts.bars[-1].available_at
        for request in requests
    ]

    snapshot_at = max(
        *exit_times,
        *mark_times,
    ) + timedelta(minutes=1)

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    path = append_marked_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
        requests,
    )

    return (
        portfolio,
        requests,
        snapshot_at,
        path,
    )


def test_two_position_marked_snapshot_is_durable_and_auditable(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        requests,
        snapshot_at,
        path,
    ) = setup_two_position_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    event = json.loads(path.read_bytes())

    assert event["valuation_status"] == (
        "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
    )

    assert event["snapshot_date_utc"] == (
        snapshot_at.date().isoformat()
    )

    assert len(event["mark_provenance"]) == 2

    valuation = event["valuation"]

    assert valuation["open_position_count"] == 2
    assert valuation["active_reservation_count"] == 2

    assert Decimal(
        valuation["gross_marked_nav"]
    ) == (
        Decimal(valuation["cash"])
        + Decimal(valuation["open_market_value"])
    )

    provenance_keys = [
        item["candidate_position_key"]
        for item in event["mark_provenance"]
    ]

    position_keys = [
        item["candidate_position_key"]
        for item in valuation["open_positions"]
    ]

    assert provenance_keys == sorted(provenance_keys)
    assert position_keys == sorted(position_keys)
    assert provenance_keys == position_keys

    assert (
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            requests,
        )
        == event
    )


def test_marked_daily_snapshot_publishes_once(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        requests,
        _,
        path,
    ) = setup_two_position_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        append_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            requests,
        )

    assert path.read_bytes() == original


def test_rehashed_mark_provenance_tamper_fails_closed(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        requests,
        snapshot_at,
        path,
    ) = setup_two_position_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    event = json.loads(path.read_bytes())

    event["mark_provenance"][0][
        "mark_price"
    ] = "999"

    _rehash_snapshot(event)

    path.write_text(
        json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "provenance does not bind "
            "authenticated mark inputs"
        ),
    ):
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            requests,
        )


def test_rehashed_marked_valuation_tamper_fails_closed(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        requests,
        snapshot_at,
        path,
    ) = setup_two_position_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    event = json.loads(path.read_bytes())

    event["valuation"][
        "gross_marked_nav"
    ] = "999"

    _rehash_snapshot(event)

    path.write_text(
        json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "valuation does not bind frozen "
            "authenticated marks"
        ),
    ):
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            requests,
        )


def test_marked_snapshot_requires_complete_historical_request_set(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        requests,
        snapshot_at,
        _,
    ) = setup_two_position_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        ValueError,
        match=(
            "every frozen active reservation requires "
            "exactly one authenticated mark"
        ),
    ):
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            (requests[0],),
        )


def setup_continuation_marked_snapshot(
    tmp_path,
    monkeypatch,
):
    (
        args,
        portfolio,
        chain,
        chain_packages,
        exit_policy,
        exit_packages,
        exit_path,
    ) = setup_continuation_settlement(
        tmp_path,
        monkeypatch,
        result="OPEN",
    )

    request = ContinuationMarkRequest(
        *args,
        chain,
        chain_packages,
        exit_policy,
        exit_packages,
    )

    exit_event = json.loads(
        exit_path.read_bytes()
    )

    snapshot_at = (
        datetime.fromisoformat(
            exit_event["recorded_at"]
        )
        + timedelta(minutes=1)
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    path = append_marked_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
        (request,),
    )

    return (
        portfolio,
        request,
        snapshot_at,
        path,
        exit_path,
        exit_event,
    )


def test_continuation_marked_snapshot_is_durable_and_auditable(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        request,
        snapshot_at,
        path,
        _,
        exit_event,
    ) = setup_continuation_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    event = json.loads(
        path.read_bytes()
    )

    assert event["valuation_status"] == (
        "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
    )

    assert len(
        event["mark_provenance"]
    ) == 1

    provenance = event[
        "mark_provenance"
    ][0]

    assert (
        provenance["evaluation_kind"]
        == "CONTINUATION"
    )

    assert (
        provenance["continuation_events"]
        == exit_event["continuation_events"]
    )

    assert (
        provenance["exit_event_id"]
        == exit_event["event_id"]
    )

    assert (
        provenance["position_event_id"]
        == exit_event["position_event_id"]
    )

    valuation = event["valuation"]

    assert valuation[
        "open_position_count"
    ] == 1

    assert valuation[
        "active_reservation_count"
    ] == 1

    assert Decimal(
        valuation["gross_marked_nav"]
    ) == (
        Decimal(
            valuation["cash"]
        )
        + Decimal(
            valuation["open_market_value"]
        )
    )

    assert (
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            (request,),
        )
        == event
    )


def test_rehashed_continuation_provenance_tamper_fails_closed(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        request,
        snapshot_at,
        path,
        _,
        _,
    ) = setup_continuation_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    event = json.loads(
        path.read_bytes()
    )

    event["mark_provenance"][0][
        "continuation_events"
    ][0]["event_sha256"] = "0" * 64

    _rehash_snapshot(event)

    path.write_text(
        json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "provenance does not bind "
            "authenticated mark inputs"
        ),
    ):
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            (request,),
        )


def test_continuation_snapshot_reaudits_upstream_exit_receipt(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        request,
        snapshot_at,
        _,
        exit_path,
        _,
    ) = setup_continuation_marked_snapshot(
        tmp_path,
        monkeypatch,
    )

    exit_event = json.loads(
        exit_path.read_bytes()
    )

    exit_event["result"][
        "evaluated_through_sequence"
    ] = 999999

    exit_path.write_text(
        json.dumps(
            exit_event,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    with pytest.raises(
        ValueError,
        match="does not bind authenticated inputs",
    ):
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            (request,),
        )


def test_unknown_continuation_cannot_publish_marked_snapshot(
    tmp_path,
    monkeypatch,
):
    (
        args,
        portfolio,
        chain,
        chain_packages,
        exit_policy,
        exit_packages,
        exit_path,
    ) = setup_continuation_settlement(
        tmp_path,
        monkeypatch,
        result="UNKNOWN",
    )

    request = ContinuationMarkRequest(
        *args,
        chain,
        chain_packages,
        exit_policy,
        exit_packages,
    )

    exit_event = json.loads(
        exit_path.read_bytes()
    )

    snapshot_at = (
        datetime.fromisoformat(
            exit_event["recorded_at"]
        )
        + timedelta(minutes=1)
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    with pytest.raises(
        ValueError,
        match="authenticated OPEN evaluation",
    ):
        append_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            (request,),
        )


def test_mixed_same_session_and_continuation_marks_share_one_snapshot(
    tmp_path,
    monkeypatch,
):
    from app.paper import (
        shadow_allocations,
        shadow_collection,
        shadow_continuations,
        shadow_exits,
        shadow_facts,
        shadow_fills,
        shadow_freeze,
        shadow_ledger,
        shadow_portfolio,
        shadow_positions,
        shadow_triggers,
    )
    from app.paper.shadow_daily_portfolio import (
        ContinuationMarkRequest,
        SingleSessionMarkRequest,
    )
    from app.paper.shadow_portfolio import (
        ShadowPortfolioPolicy,
    )
    from tests.test_shadow_continuations import (
        continuation_exit_policy,
        exit_evidence,
        prepared_continuation,
    )

    # --------------------------------------------------------
    # Position 1 starts from a genuine complete-close
    # predecessor session, so it can later continue.
    # --------------------------------------------------------
    (
        args1,
        continuation,
        continuation_packages,
        continuation_available,
    ) = prepared_continuation(
        tmp_path,
        monkeypatch,
    )

    (
        item1,
        watchlist_packages,
        facts1,
        fact_packages,
        fill_policy,
        fill_packages,
    ) = args1

    # --------------------------------------------------------
    # One shared native-currency portfolio policy.
    # --------------------------------------------------------
    portfolio = ShadowPortfolioPolicy(
        base_currency="USD",
        initial_capital=Decimal("1000"),
        effective_at=(
            item1.information_cutoff
            - timedelta(minutes=1)
        ),
        egx_capital_fraction=Decimal(".3"),
        us_capital_fraction=Decimal(".6"),
        minimum_cash_fraction=Decimal(".1"),
        max_position_fraction=Decimal(".2"),
        max_position_risk_fraction=Decimal(".1"),
        max_portfolio_risk_fraction=Decimal(".2"),
    )

    policy_frozen_at = (
        item1.information_cutoff
        - timedelta(minutes=2)
    )

    monkeypatch.setattr(
        shadow_portfolio,
        "_now",
        lambda: policy_frozen_at,
    )

    shadow_portfolio.freeze_portfolio_policy(
        tmp_path,
        portfolio,
    )

    # Reserve shared capital for position 1.
    position1 = (
        shadow_positions.audit_position_open_event(
            tmp_path,
            *args1,
        )
    )

    reservation1_at = (
        datetime.fromisoformat(
            position1["recorded_at"]
        )
        + timedelta(seconds=1)
    )

    monkeypatch.setattr(
        shadow_portfolio,
        "_now",
        lambda: reservation1_at,
    )
    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: reservation1_at,
    )

    shadow_allocations.append_capital_reservation(
        tmp_path,
        *args1,
        portfolio,
    )

    # --------------------------------------------------------
    # Position 2: genuine second candidate sharing the same
    # portfolio, but it remains a same-session OPEN mark.
    # --------------------------------------------------------
    candidate1 = item1.candidates[0]

    candidate2 = candidate1.model_copy(
        update={
            "candidate_id":
                "fixture-mixed-position-2",
        }
    )

    item2 = item1.model_copy(
        update={
            "record_id":
                "US-20260914-mixed-position-2",
            "candidates":
                (candidate2,),
        }
    )

    item2 = type(item1).model_validate(
        item2.model_dump(
            mode="python"
        )
    )

    facts2 = facts1.model_copy(
        update={
            "record_id":
                item2.record_id,
            "candidate_id":
                candidate2.candidate_id,
        }
    )

    facts2 = type(facts1).model_validate(
        facts2.model_dump(
            mode="python"
        )
    )

    completed_at = (
        item2.generated_at
        + timedelta(minutes=30)
    )

    monkeypatch.setattr(
        shadow_collection,
        "_now",
        lambda: completed_at,
    )
    monkeypatch.setattr(
        shadow_freeze,
        "_now",
        lambda: completed_at,
    )

    shadow_collection.complete_watchlist(
        tmp_path,
        item2,
        watchlist_packages,
    )

    monkeypatch.setattr(
        shadow_ledger,
        "_now",
        lambda: completed_at,
    )

    shadow_ledger.append_candidate_event(
        tmp_path,
        item2,
        watchlist_packages,
    )

    fact2_at = (
        facts2.bars[-1].available_at
        + timedelta(minutes=1)
    )

    monkeypatch.setattr(
        shadow_facts,
        "_now",
        lambda: fact2_at,
    )

    shadow_facts.append_forward_fact_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
    )

    selection2_at = (
        item2.generated_at
        + timedelta(minutes=31)
    )

    monkeypatch.setattr(
        shadow_fills,
        "_now",
        lambda: selection2_at,
    )

    shadow_fills.freeze_fill_policy_selection(
        tmp_path,
        item2,
        watchlist_packages,
        fill_policy,
        fill_packages,
    )

    monkeypatch.setattr(
        shadow_triggers,
        "_now",
        lambda: fact2_at,
    )

    shadow_triggers.append_trigger_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
    )

    monkeypatch.setattr(
        shadow_fills,
        "_now",
        lambda: fact2_at,
    )

    shadow_fills.append_fill_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
    )

    monkeypatch.setattr(
        shadow_positions,
        "_now",
        lambda: fact2_at,
    )

    shadow_positions.append_position_open_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
    )

    reservation2_at = max(
        fact2_at,
        reservation1_at + timedelta(seconds=1),
    )

    monkeypatch.setattr(
        shadow_portfolio,
        "_now",
        lambda: reservation2_at,
    )
    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: reservation2_at,
    )

    shadow_allocations.append_capital_reservation(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
        portfolio,
    )

    args2 = (
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
    )

    # --------------------------------------------------------
    # One exit policy/evidence set is authenticated for both
    # native-USD positions.
    # --------------------------------------------------------
    exit_packages = exit_evidence(
        item1.information_cutoff
    )

    exit_policy = (
        continuation_exit_policy().model_copy(
            update={
                "slippage_evidence_package_id":
                    exit_packages[0].identity,
                "cost_evidence_package_id":
                    exit_packages[1].identity,
                "participation_evidence_package_id":
                    exit_packages[2].identity,
            }
        )
    )

    # Same-session OPEN exit evaluation for position 2.
    monkeypatch.setattr(
        shadow_exits,
        "_now",
        lambda: fact2_at,
    )

    shadow_exits.append_exit_event(
        tmp_path,
        *args2,
        exit_policy,
        exit_packages,
    )

    # --------------------------------------------------------
    # Position 1 continues into the next authenticated session.
    # --------------------------------------------------------
    continuation_at = (
        continuation_available
        + timedelta(minutes=1)
    )

    monkeypatch.setattr(
        shadow_continuations,
        "_now",
        lambda: continuation_at,
    )

    shadow_continuations.append_continuation_event(
        tmp_path,
        *args1,
        continuation,
        continuation_packages,
    )

    continuation_exit_at = (
        continuation_available
        + timedelta(minutes=2)
    )

    monkeypatch.setattr(
        shadow_exits,
        "_now",
        lambda: continuation_exit_at,
    )

    shadow_exits.append_continuation_exit_event(
        tmp_path,
        *args1,
        (continuation,),
        (continuation_packages,),
        exit_policy,
        exit_packages,
    )

    same_session_request = (
        SingleSessionMarkRequest(
            *args2,
            exit_policy,
            exit_packages,
        )
    )

    continuation_request = (
        ContinuationMarkRequest(
            *args1,
            (continuation,),
            (continuation_packages,),
            exit_policy,
            exit_packages,
        )
    )

    requests = (
        same_session_request,
        continuation_request,
    )

    snapshot_at = (
        continuation_exit_at
        + timedelta(minutes=1)
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: snapshot_at,
    )

    path = append_marked_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
        requests,
    )

    event = json.loads(
        path.read_bytes()
    )

    assert (
        event["valuation"]["open_position_count"]
        == 2
    )

    assert (
        event["valuation"][
            "active_reservation_count"
        ]
        == 2
    )

    assert {
        item["evaluation_kind"]
        for item in event["mark_provenance"]
    } == {
        "SAME_SESSION",
        "CONTINUATION",
    }

    provenance_keys = [
        item["candidate_position_key"]
        for item in event["mark_provenance"]
    ]

    position_keys = [
        item["candidate_position_key"]
        for item in event[
            "valuation"
        ]["open_positions"]
    ]

    assert provenance_keys == sorted(
        provenance_keys
    )
    assert position_keys == sorted(
        position_keys
    )
    assert provenance_keys == position_keys

    assert Decimal(
        event["valuation"][
            "gross_marked_nav"
        ]
    ) == (
        Decimal(
            event["valuation"]["cash"]
        )
        + Decimal(
            event["valuation"][
                "open_market_value"
            ]
        )
    )

    assert (
        audit_marked_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            snapshot_at.date(),
            requests,
        )
        == event
    )
