from datetime import timedelta
from decimal import Decimal

import pytest

from app.paper import (
    shadow_allocations,
    shadow_collection,
    shadow_exits,
    shadow_facts,
    shadow_fills,
    shadow_freeze,
    shadow_ledger,
    shadow_portfolio,
    shadow_positions,
    shadow_triggers,
)
from app.paper.shadow_report import exit_evaluation_view
from app.paper.shadow_daily_portfolio import (
    ContinuationMarkRequest,
    SingleSessionMarkRequest,
    multi_position_marked_portfolio_view,
)
from tests.test_shadow_allocations import (
    setup_allocation,
    setup_continuation_settlement,
    setup_settlement,
)


def test_two_real_authenticated_open_positions(
    tmp_path,
    monkeypatch,
):
    (
        args1,
        portfolio,
        exit_policy,
        exit_packages,
        now,
    ) = setup_settlement(
        tmp_path,
        monkeypatch,
        closed=False,
        us_capital_fraction=Decimal(".6"),
    )

    (
        item1,
        watchlist_packages,
        facts1,
        fact_packages,
        fill_policy,
        fill_packages,
    ) = args1

    # Build a second genuine watchlist identity.
    candidate1 = item1.candidates[0]
    candidate2 = candidate1.model_copy(
        update={
            "candidate_id": "fixture-ibm-2",
        }
    )

    item2 = item1.model_copy(
        update={
            "record_id": "US-20260914-fixture-2",
            "candidates": (candidate2,),
        }
    )
    item2 = type(item1).model_validate(
        item2.model_dump(mode="python")
    )

    # Bind forward facts to that second authenticated candidate.
    facts2 = facts1.model_copy(
        update={
            "record_id": item2.record_id,
            "candidate_id": candidate2.candidate_id,
        }
    )
    facts2 = type(facts1).model_validate(
        facts2.model_dump(mode="python")
    )

    # Complete the second watchlist and candidate ledger event.
    completed_at = item2.generated_at + timedelta(minutes=30)

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

    # Admit the second candidate's forward facts.
    fact_at = facts2.bars[-1].available_at + timedelta(
        minutes=1
    )

    monkeypatch.setattr(
        shadow_facts,
        "_now",
        lambda: fact_at,
    )
    shadow_facts.append_forward_fact_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
    )

    # Freeze the already-authenticated fill policy for candidate 2.
    selection_at = item2.generated_at + timedelta(minutes=31)

    monkeypatch.setattr(
        shadow_fills,
        "_now",
        lambda: selection_at,
    )
    shadow_fills.freeze_fill_policy_selection(
        tmp_path,
        item2,
        watchlist_packages,
        fill_policy,
        fill_packages,
    )

    # Trigger -> fill -> open position.
    monkeypatch.setattr(
        shadow_triggers,
        "_now",
        lambda: fact_at,
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
        lambda: fact_at,
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
        lambda: fact_at,
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

    # Reserve the same shared portfolio capital for candidate 2.
    monkeypatch.setattr(
        shadow_portfolio,
        "_now",
        lambda: fact_at,
    )
    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: fact_at,
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

    # Authenticate an OPEN exit evaluation for candidate 2.
    monkeypatch.setattr(
        shadow_exits,
        "_now",
        lambda: fact_at,
    )
    shadow_exits.append_exit_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
        exit_policy,
        exit_packages,
    )

    args2 = (
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
    )

    before = {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    view1 = exit_evaluation_view(
        tmp_path,
        *args1,
        exit_policy,
        exit_packages,
    )
    view2 = exit_evaluation_view(
        tmp_path,
        *args2,
        exit_policy,
        exit_packages,
    )

    assert view1["position_status"] == "OPEN"
    assert view2["position_status"] == "OPEN"

    mark1 = view1["open_paper_positions"]["position"]
    mark2 = view2["open_paper_positions"]["position"]

    assert (
        mark1["candidate_position_key"]
        != mark2["candidate_position_key"]
    )

    reservations = shadow_allocations._read_reservations(
        tmp_path
    )

    assert len(reservations) == 2

    active_keys = {
        row["candidate_position_key"]
        for row in reservations
    }
    marked_keys = {
        mark1["candidate_position_key"],
        mark2["candidate_position_key"],
    }

    assert active_keys == marked_keys

    assert Decimal(mark1["gross_market_value"]) > 0
    assert Decimal(mark2["gross_market_value"]) > 0

    request1 = SingleSessionMarkRequest(
        *args1,
        exit_policy,
        exit_packages,
    )
    request2 = SingleSessionMarkRequest(
        *args2,
        exit_policy,
        exit_packages,
    )

    aggregate = multi_position_marked_portfolio_view(
        tmp_path,
        portfolio,
        (request2, request1),
    )

    expected_market_value = (
        Decimal(mark1["gross_market_value"])
        + Decimal(mark2["gross_market_value"])
    )

    assert aggregate["open_position_count"] == 2
    assert Decimal(
        aggregate["open_market_value"]
    ) == expected_market_value

    assert Decimal(
        aggregate["gross_marked_nav"]
    ) == (
        Decimal(aggregate["cash"])
        + expected_market_value
    )

    keys = [
        row["candidate_position_key"]
        for row in aggregate["open_positions"]
    ]

    assert keys == sorted(keys)
    assert set(keys) == active_keys

    assert before == {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

def _setup_two_authenticated_open_positions(
    tmp_path,
    monkeypatch,
):
    (
        args1,
        portfolio,
        exit_policy,
        exit_packages,
        now,
    ) = setup_settlement(
        tmp_path,
        monkeypatch,
        closed=False,
        us_capital_fraction=Decimal(".6"),
    )

    (
        item1,
        watchlist_packages,
        facts1,
        fact_packages,
        fill_policy,
        fill_packages,
    ) = args1

    # Build a second genuine watchlist identity.
    candidate1 = item1.candidates[0]
    candidate2 = candidate1.model_copy(
        update={
            "candidate_id": "fixture-ibm-2",
        }
    )

    item2 = item1.model_copy(
        update={
            "record_id": "US-20260914-fixture-2",
            "candidates": (candidate2,),
        }
    )
    item2 = type(item1).model_validate(
        item2.model_dump(mode="python")
    )

    # Bind forward facts to that second authenticated candidate.
    facts2 = facts1.model_copy(
        update={
            "record_id": item2.record_id,
            "candidate_id": candidate2.candidate_id,
        }
    )
    facts2 = type(facts1).model_validate(
        facts2.model_dump(mode="python")
    )

    # Complete the second watchlist and candidate ledger event.
    completed_at = item2.generated_at + timedelta(minutes=30)

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

    # Admit the second candidate's forward facts.
    fact_at = facts2.bars[-1].available_at + timedelta(
        minutes=1
    )

    monkeypatch.setattr(
        shadow_facts,
        "_now",
        lambda: fact_at,
    )
    shadow_facts.append_forward_fact_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
    )

    # Freeze the already-authenticated fill policy for candidate 2.
    selection_at = item2.generated_at + timedelta(minutes=31)

    monkeypatch.setattr(
        shadow_fills,
        "_now",
        lambda: selection_at,
    )
    shadow_fills.freeze_fill_policy_selection(
        tmp_path,
        item2,
        watchlist_packages,
        fill_policy,
        fill_packages,
    )

    # Trigger -> fill -> open position.
    monkeypatch.setattr(
        shadow_triggers,
        "_now",
        lambda: fact_at,
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
        lambda: fact_at,
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
        lambda: fact_at,
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

    # Reserve the same shared portfolio capital for candidate 2.
    monkeypatch.setattr(
        shadow_portfolio,
        "_now",
        lambda: fact_at,
    )
    monkeypatch.setattr(
        shadow_allocations,
        "_now",
        lambda: fact_at,
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

    # Authenticate an OPEN exit evaluation for candidate 2.
    monkeypatch.setattr(
        shadow_exits,
        "_now",
        lambda: fact_at,
    )
    shadow_exits.append_exit_event(
        tmp_path,
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
        exit_policy,
        exit_packages,
    )

    args2 = (
        item2,
        watchlist_packages,
        facts2,
        fact_packages,
        fill_policy,
        fill_packages,
    )

    return (
        args1,
        args2,
        portfolio,
        exit_policy,
        exit_packages,
    )


def _single_session_requests(
    args1,
    args2,
    exit_policy,
    exit_packages,
):
    return (
        SingleSessionMarkRequest(
            *args1,
            exit_policy,
            exit_packages,
        ),
        SingleSessionMarkRequest(
            *args2,
            exit_policy,
            exit_packages,
        ),
    )


def test_missing_active_mark_fails_closed(
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

    request1, _ = _single_session_requests(
        args1,
        args2,
        exit_policy,
        exit_packages,
    )

    with pytest.raises(
        ValueError,
        match="every active reservation requires exactly one",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            (request1,),
        )


def test_duplicate_authenticated_mark_fails_closed(
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

    request1, _ = _single_session_requests(
        args1,
        args2,
        exit_policy,
        exit_packages,
    )

    with pytest.raises(
        ValueError,
        match="duplicate authenticated portfolio mark",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            (request1, request1),
        )


def test_authenticated_mark_without_active_reservation_fails_closed(
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

    request1, request2 = _single_session_requests(
        args1,
        args2,
        exit_policy,
        exit_packages,
    )

    position2 = shadow_positions.audit_position_open_event(
        tmp_path,
        *args2,
    )

    reservation2 = (
        tmp_path
        / "capital-reservations"
        / f'{position2["candidate_position_key"]}.json'
    )
    reservation2.unlink()

    with pytest.raises(
        ValueError,
        match="authenticated mark has no active reservation",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            (request1, request2),
        )


def test_closed_evaluation_cannot_mark_portfolio(
    tmp_path,
    monkeypatch,
):
    (
        args,
        portfolio,
        exit_policy,
        exit_packages,
        _,
    ) = setup_settlement(
        tmp_path,
        monkeypatch,
        closed=True,
        us_capital_fraction=Decimal(".6"),
    )

    request = SingleSessionMarkRequest(
        *args,
        exit_policy,
        exit_packages,
    )

    with pytest.raises(
        ValueError,
        match="authenticated OPEN evaluation",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            (request,),
        )


def test_request_container_must_be_exact_tuple(
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

    request1, request2 = _single_session_requests(
        args1,
        args2,
        exit_policy,
        exit_packages,
    )

    with pytest.raises(
        ValueError,
        match="exact tuple",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            [request1, request2],
        )

def test_continuation_open_marks_multi_portfolio(
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
        _,
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

    before = {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    view = multi_position_marked_portfolio_view(
        tmp_path,
        portfolio,
        (request,),
    )

    assert view["open_position_count"] == 1

    mark = view["open_positions"][0]
    observed = chain[-1].bars[-1]

    assert mark["mark_price"] == str(observed.close)
    assert (
        mark["marked_through_sequence"]
        == observed.sequence
    )

    assert Decimal(
        view["open_market_value"]
    ) == Decimal(
        mark["gross_market_value"]
    )

    assert Decimal(
        view["gross_marked_nav"]
    ) == (
        Decimal(view["cash"])
        + Decimal(view["open_market_value"])
    )

    assert before == {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


def test_unknown_continuation_cannot_mark_multi_portfolio(
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
        _,
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

    with pytest.raises(
        ValueError,
        match="authenticated OPEN evaluation",
    ):
        multi_position_marked_portfolio_view(
            tmp_path,
            portfolio,
            (request,),
        )

def test_zero_active_portfolio_is_authenticated_cash_only(
    tmp_path,
    monkeypatch,
):
    _, portfolio, _ = setup_allocation(
        tmp_path,
        monkeypatch,
        us_capital_fraction=Decimal(".6"),
    )

    assert shadow_allocations._read_reservations(
        tmp_path
    ) == []

    before = {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    view = multi_position_marked_portfolio_view(
        tmp_path,
        portfolio,
        (),
    )

    assert view["open_position_count"] == 0
    assert view["open_positions"] == []
    assert Decimal(view["open_market_value"]) == 0

    assert Decimal(
        view["gross_marked_nav"]
    ) == Decimal(view["cash"])

    assert (
        view["status"]
        == "AUTHENTICATED NATIVE CASH / "
        "NO ACTIVE RESERVATIONS"
    )

    assert (
        view["performance"]["status"]
        == "MARKED VALUATION ONLY / "
        "PERFORMANCE NOT EVALUATED"
    )

    assert all(
        value is None
        for key, value in view["performance"].items()
        if key != "status"
    )

    assert before == {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
