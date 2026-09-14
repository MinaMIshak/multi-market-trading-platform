"""Read-only native-currency portfolio accounting and authenticated marking."""
from __future__ import annotations

from decimal import Context, Decimal, localcontext
from pathlib import Path

from app.paper.shadow_allocations import (
    _read_reservations,
    _read_settlements,
)
from app.paper.shadow_collection import LABEL
from app.paper.shadow_portfolio import (
    ShadowPortfolioPolicy,
    audit_portfolio_policy,
)
from app.paper.shadow_report import (
    continuation_exit_evaluation_view,
    exit_evaluation_view,
)


def _ledger_state(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    directory = Path(directory)

    policy_receipt = audit_portfolio_policy(
        directory,
        portfolio,
    )
    reservations = _read_reservations(directory)
    settlements = _read_settlements(
        directory,
        reservations,
    )

    policy_id = policy_receipt["policy_id"]
    currency = portfolio.base_currency

    if any(
        item["policy_id"] != policy_id
        or item["currency"] != currency
        for item in reservations
    ):
        raise ValueError(
            "portfolio view requires one authenticated "
            "native-currency policy"
        )

    if any(
        item["policy_id"] != policy_id
        or item["currency"] != currency
        for item in settlements
    ):
        raise ValueError(
            "portfolio settlement does not bind "
            "native-currency policy"
        )

    settled_ids = {
        item["reservation_id"]
        for item in settlements
    }

    active = [
        item
        for item in reservations
        if item["reservation_id"] not in settled_ids
    ]

    return (
        policy_receipt,
        reservations,
        settlements,
        active,
    )


def native_cash_portfolio_view(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
) -> dict:
    """Derive authenticated native-currency accounting cash."""
    directory = Path(directory)

    (
        policy_receipt,
        reservations,
        settlements,
        active,
    ) = _ledger_state(directory, portfolio)

    with localcontext(Context(prec=34)):
        initial = Decimal(portfolio.initial_capital)

        historical_reserved = sum(
            (
                Decimal(item["capital_reserved"])
                for item in reservations
            ),
            Decimal(0),
        )

        active_reserved = sum(
            (
                Decimal(item["capital_reserved"])
                for item in active
            ),
            Decimal(0),
        )

        settled_proceeds = sum(
            (
                Decimal(item["net_exit_proceeds"])
                for item in settlements
            ),
            Decimal(0),
        )

        released_capital = sum(
            (
                Decimal(item["capital_released"])
                for item in settlements
            ),
            Decimal(0),
        )

        realized_pnl = (
            settled_proceeds
            - released_capital
        )

        cash_from_flows = (
            initial
            - historical_reserved
            + settled_proceeds
        )

        cash_from_state = (
            initial
            - active_reserved
            + realized_pnl
        )

    if cash_from_flows != cash_from_state:
        raise ValueError(
            "portfolio cash accounting identity failed"
        )

    return {
        "schema_version": (
            "shadow-native-cash-portfolio-view-v1"
        ),
        "label": LABEL,
        "scope": (
            "ONE SHARED NATIVE-CURRENCY PORTFOLIO "
            "/ CASH ONLY"
        ),
        "status": (
            "AUTHENTICATED NATIVE ACCOUNTING CASH "
            "/ NO MARKED NAV OR PERFORMANCE"
        ),
        "policy_id": policy_receipt["policy_id"],
        "currency": portfolio.base_currency,
        "initial_capital": str(initial),
        "cash": str(cash_from_flows),
        "active_capital_reserved": str(
            active_reserved
        ),
        "historical_capital_reserved": str(
            historical_reserved
        ),
        "settled_net_exit_proceeds": str(
            settled_proceeds
        ),
        "realized_pnl": str(realized_pnl),
        "reservation_count": len(reservations),
        "active_reservation_count": len(active),
        "settlement_count": len(settlements),
        "open_market_value": None,
        "gross_marked_nav": None,
        "performance_status": "NOT EVALUATED",
    }


def _attach_one_authenticated_open_mark(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    report_view: dict,
) -> dict:
    """Combine native cash with exactly one authenticated OPEN mark."""
    directory = Path(directory)

    cash_view = native_cash_portfolio_view(
        directory,
        portfolio,
    )

    (
        _,
        _,
        _,
        active,
    ) = _ledger_state(directory, portfolio)

    if len(active) != 1:
        raise ValueError(
            "marked portfolio currently requires "
            "exactly one active reservation"
        )

    if (
        type(report_view) is not dict
        or report_view.get("position_status") != "OPEN"
    ):
        raise ValueError(
            "marked portfolio requires authenticated "
            "OPEN exit evaluation"
        )

    opened = report_view.get("open_paper_positions")

    if (
        type(opened) is not dict
        or opened.get("status")
        != "ONE AUTHENTICATED OPEN POSITION AS OF OBSERVED BAR"
        or type(opened.get("position")) is not dict
    ):
        raise ValueError(
            "authenticated OPEN mark unavailable"
        )

    mark = opened["position"]
    reservation = active[0]

    if (
        mark.get("candidate_position_key")
        != reservation["candidate_position_key"]
    ):
        raise ValueError(
            "OPEN mark does not bind active reservation"
        )

    if (
        mark.get("currency")
        != cash_view["currency"]
        or reservation["currency"]
        != cash_view["currency"]
    ):
        raise ValueError(
            "OPEN mark currency does not bind portfolio"
        )

    if (
        report_view.get("market")
        != reservation["market"]
    ):
        raise ValueError(
            "OPEN mark market does not bind reservation"
        )

    market_value = Decimal(
        mark["gross_market_value"]
    )

    if (
        not market_value.is_finite()
        or market_value < 0
    ):
        raise ValueError(
            "invalid authenticated open market value"
        )

    with localcontext(Context(prec=34)):
        cash = Decimal(cash_view["cash"])
        gross_marked_nav = cash + market_value

    return cash_view | {
        "schema_version": (
            "shadow-gross-marked-portfolio-view-v1"
        ),
        "scope": (
            "ONE SHARED NATIVE-CURRENCY PORTFOLIO "
            "/ EXACTLY ONE AUTHENTICATED OPEN POSITION"
        ),
        "status": (
            "AUTHENTICATED NATIVE CASH + "
            "OBSERVED GROSS OPEN MARK"
        ),
        "open_market_value": str(market_value),
        "gross_marked_nav": str(gross_marked_nav),
        "gross_marked_nav_status": (
            "ACCOUNTING CASH + OBSERVED MARKET VALUE "
            "/ ENTRY COST INCLUDED "
            "/ BEFORE LIQUIDATION COST AND SLIPPAGE"
        ),
        "open_position": mark,
        "performance_status": (
            "MARKED VALUATION ONLY "
            "/ PERFORMANCE NOT EVALUATED"
        ),
    }


def single_session_marked_portfolio_view(
    directory: Path,
    watchlist,
    watchlist_packages,
    facts,
    fact_packages,
    fill_policy,
    fill_packages,
    portfolio: ShadowPortfolioPolicy,
    exit_policy,
    exit_packages,
    *,
    evaluation_facts=None,
    evaluation_fact_packages=None,
) -> dict:
    """Re-audit one single-session OPEN mark and attach it to native cash."""
    report_view = exit_evaluation_view(
        directory,
        watchlist,
        watchlist_packages,
        facts,
        fact_packages,
        fill_policy,
        fill_packages,
        exit_policy,
        exit_packages,
        evaluation_facts=evaluation_facts,
        evaluation_fact_packages=evaluation_fact_packages,
    )

    return _attach_one_authenticated_open_mark(
        directory,
        portfolio,
        report_view,
    )


def continuation_marked_portfolio_view(
    directory: Path,
    watchlist,
    watchlist_packages,
    facts,
    fact_packages,
    fill_policy,
    fill_packages,
    portfolio: ShadowPortfolioPolicy,
    continuations,
    continuation_packages,
    exit_policy,
    exit_packages,
) -> dict:
    """Re-audit one continuation OPEN mark and attach it to native cash."""
    report_view = continuation_exit_evaluation_view(
        directory,
        watchlist,
        watchlist_packages,
        facts,
        fact_packages,
        fill_policy,
        fill_packages,
        continuations,
        continuation_packages,
        exit_policy,
        exit_packages,
    )

    return _attach_one_authenticated_open_mark(
        directory,
        portfolio,
        report_view,
    )
