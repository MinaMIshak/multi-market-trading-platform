"""Software-only portfolio valuation fixtures; no empirical claims."""
from decimal import Decimal

import pytest

from app.paper import shadow_allocations
from app.paper.shadow_daily_portfolio import (
    continuation_marked_portfolio_view,
    single_session_marked_portfolio_view,
)
from tests.test_shadow_allocations import (
    setup_continuation_settlement,
    setup_settlement,
)


def test_single_open_mark(tmp_path, monkeypatch):
    args, portfolio, policy, evidence, _ = setup_settlement(
        tmp_path,
        monkeypatch,
        closed=False,
    )

    before = {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    view = single_session_marked_portfolio_view(
        tmp_path,
        *args,
        portfolio,
        policy,
        evidence,
    )

    reservation = shadow_allocations._read_reservations(tmp_path)[0]

    cash = Decimal(view["cash"])
    market_value = Decimal(view["open_market_value"])
    marked_nav = Decimal(view["gross_marked_nav"])

    assert cash == (
        Decimal(view["initial_capital"])
        - Decimal(reservation["capital_reserved"])
    )
    assert marked_nav == cash + market_value
    assert (
        view["open_position"]["candidate_position_key"]
        == reservation["candidate_position_key"]
    )

    assert before == {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }





def test_continuation_open_mark(tmp_path, monkeypatch):
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

    before = {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    view = continuation_marked_portfolio_view(
        tmp_path,
        *args,
        portfolio,
        chain,
        chain_packages,
        exit_policy,
        exit_packages,
    )

    assert Decimal(view["gross_marked_nav"]) == (
        Decimal(view["cash"])
        + Decimal(view["open_market_value"])
    )

    assert (
        view["open_position"]["mark_price"]
        == str(chain[-1].bars[-1].close)
    )

    assert (
        view["open_position"]["marked_through_sequence"]
        == chain[-1].bars[-1].sequence
    )

    assert before == {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }





def test_closed_exit_cannot_be_marked(tmp_path, monkeypatch):
    args, portfolio, policy, evidence, _ = setup_settlement(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        ValueError,
        match="OPEN exit evaluation",
    ):
        single_session_marked_portfolio_view(
            tmp_path,
            *args,
            portfolio,
            policy,
            evidence,
        )





def test_unknown_continuation_cannot_be_marked(
    tmp_path, monkeypatch,
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

    with pytest.raises(
        ValueError,
        match="OPEN exit evaluation",
    ):
        continuation_marked_portfolio_view(
            tmp_path,
            *args,
            portfolio,
            chain,
            chain_packages,
            exit_policy,
            exit_packages,
        )
