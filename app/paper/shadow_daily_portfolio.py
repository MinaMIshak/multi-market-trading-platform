"""Read-only native-currency portfolio accounting and authenticated marking."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path

from app.paper.shadow_allocations import (
    _read_reservations,
    _read_settlements,
)
from app.paper.shadow_collection import LABEL
from app.paper.shadow_exits import ShadowExitPolicy
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_fills import ShadowFillPolicy
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage
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

@dataclass(frozen=True, slots=True)
class SingleSessionMarkRequest:
    """Authenticated inputs for one same-session OPEN mark."""

    watchlist: ShadowWatchlist
    watchlist_packages: tuple[HistoricalEvidencePackage, ...]
    facts: ForwardFactBundle
    fact_packages: tuple[HistoricalEvidencePackage, ...]
    fill_policy: ShadowFillPolicy
    fill_packages: tuple[HistoricalEvidencePackage, ...]
    exit_policy: ShadowExitPolicy
    exit_packages: tuple[HistoricalEvidencePackage, ...]
    evaluation_facts: ForwardFactBundle | None = None
    evaluation_fact_packages: (
        tuple[HistoricalEvidencePackage, ...] | None
    ) = None


@dataclass(frozen=True, slots=True)
class ContinuationMarkRequest:
    """Authenticated inputs for one continuation OPEN mark."""

    watchlist: ShadowWatchlist
    watchlist_packages: tuple[HistoricalEvidencePackage, ...]
    facts: ForwardFactBundle
    fact_packages: tuple[HistoricalEvidencePackage, ...]
    fill_policy: ShadowFillPolicy
    fill_packages: tuple[HistoricalEvidencePackage, ...]
    continuations: tuple
    continuation_packages: tuple
    exit_policy: ShadowExitPolicy
    exit_packages: tuple[HistoricalEvidencePackage, ...]


def _report_for_mark_request(
    directory: Path,
    request,
) -> dict:
    """Re-audit one exact request through the existing report boundary."""
    if type(request) is SingleSessionMarkRequest:
        return exit_evaluation_view(
            directory,
            request.watchlist,
            request.watchlist_packages,
            request.facts,
            request.fact_packages,
            request.fill_policy,
            request.fill_packages,
            request.exit_policy,
            request.exit_packages,
            evaluation_facts=request.evaluation_facts,
            evaluation_fact_packages=(
                request.evaluation_fact_packages
            ),
        )

    if type(request) is ContinuationMarkRequest:
        return continuation_exit_evaluation_view(
            directory,
            request.watchlist,
            request.watchlist_packages,
            request.facts,
            request.fact_packages,
            request.fill_policy,
            request.fill_packages,
            request.continuations,
            request.continuation_packages,
            request.exit_policy,
            request.exit_packages,
        )

    raise ValueError(
        "exact authenticated portfolio mark request required"
    )


def _authenticated_open_mark_from_report(
    report_view: dict,
    reservation: dict,
    base_currency: str,
) -> tuple[dict, Decimal]:
    """Bind one re-audited OPEN mark to one active reservation."""
    if (
        type(report_view) is not dict
        or report_view.get("position_status") != "OPEN"
    ):
        raise ValueError(
            "portfolio mark requires authenticated OPEN evaluation"
        )

    opened = report_view.get("open_paper_positions")

    if (
        type(opened) is not dict
        or opened.get("status")
        != "ONE AUTHENTICATED OPEN POSITION AS OF OBSERVED BAR"
        or type(opened.get("position")) is not dict
    ):
        raise ValueError(
            "authenticated OPEN evaluation has no admissible mark"
        )

    position = opened["position"]
    key = position.get("candidate_position_key")

    if key != reservation["candidate_position_key"]:
        raise ValueError(
            "portfolio mark does not bind active reservation"
        )

    references = report_view.get("audit_references")

    if (
        type(references) is not dict
        or references.get("position_event_id")
        != reservation["position_event_id"]
        or references.get("position_event_sha256")
        != reservation["position_event_sha256"]
    ):
        raise ValueError(
            "portfolio mark does not bind reserved position event"
        )

    if (
        report_view.get("market") != reservation["market"]
        or position.get("currency") != reservation["currency"]
        or reservation["currency"] != base_currency
    ):
        raise ValueError(
            "portfolio mark market or currency mismatch"
        )

    try:
        market_value = Decimal(
            position["gross_market_value"]
        )
    except (
        InvalidOperation,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "invalid authenticated gross market value"
        ) from None

    if (
        not market_value.is_finite()
        or market_value < 0
    ):
        raise ValueError(
            "invalid authenticated gross market value"
        )

    return (
        dict(position)
        | {"reservation_id": reservation["reservation_id"]},
        market_value,
    )


def multi_position_marked_portfolio_view(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    requests: tuple[
        SingleSessionMarkRequest | ContinuationMarkRequest,
        ...,
    ],
) -> dict:
    """Value all active native-currency reservations from authenticated marks."""
    if type(requests) is not tuple:
        raise ValueError(
            "portfolio mark requests must be an exact tuple"
        )

    cash_view = native_cash_portfolio_view(
        directory,
        portfolio,
    )
    _, _, _, active = _ledger_state(
        Path(directory),
        portfolio,
    )

    active_by_key = {
        row["candidate_position_key"]: row
        for row in active
    }

    if len(active_by_key) != len(active):
        raise ValueError(
            "duplicate active candidate position key"
        )

    marks: dict[str, dict] = {}
    values: dict[str, Decimal] = {}

    for request in requests:
        report_view = _report_for_mark_request(
            Path(directory),
            request,
        )

        opened = report_view.get(
            "open_paper_positions"
        )
        position = (
            opened.get("position")
            if type(opened) is dict
            else None
        )

        if type(position) is not dict:
            raise ValueError(
                "portfolio mark requires authenticated OPEN evaluation"
            )

        key = position.get("candidate_position_key")

        if key in marks:
            raise ValueError(
                "duplicate authenticated portfolio mark"
            )

        reservation = active_by_key.get(key)

        if reservation is None:
            raise ValueError(
                "authenticated mark has no active reservation"
            )

        mark, market_value = (
            _authenticated_open_mark_from_report(
                report_view,
                reservation,
                cash_view["currency"],
            )
        )

        marks[key] = mark
        values[key] = market_value

    if set(marks) != set(active_by_key):
        raise ValueError(
            "every active reservation requires exactly one "
            "authenticated OPEN mark"
        )

    with localcontext(Context(prec=34)):
        cash = Decimal(cash_view["cash"])
        open_market_value = sum(
            values.values(),
            Decimal(0),
        )
        gross_marked_nav = cash + open_market_value

    ordered_keys = sorted(marks)

    if ordered_keys:
        status = (
            "AUTHENTICATED NATIVE CASH + "
            "OBSERVED GROSS OPEN MARKS"
        )
        marked_status = (
            "ACCOUNTING CASH PLUS AUTHENTICATED OBSERVED "
            "GROSS MARKET VALUE / ENTRY COST INCLUDED / "
            "BEFORE LIQUIDATION COST AND SLIPPAGE"
        )
    else:
        status = (
            "AUTHENTICATED NATIVE CASH / "
            "NO ACTIVE RESERVATIONS"
        )
        marked_status = (
            "ACCOUNTING CASH / NO ACTIVE RESERVATIONS / "
            "NO LIQUIDATION INFERENCE"
        )

    return cash_view | {
        "schema_version":
            "shadow-gross-marked-portfolio-view-v2",
        "scope": (
            "ONE SHARED NATIVE-CURRENCY PORTFOLIO / "
            "ALL ACTIVE RESERVATIONS AUTHENTICATED"
        ),
        "status": status,
        "open_position_count": len(ordered_keys),
        "open_positions": [
            marks[key]
            for key in ordered_keys
        ],
        "open_market_value": str(open_market_value),
        "gross_marked_nav": str(gross_marked_nav),
        "gross_marked_nav_status": marked_status,
        "performance": {
            "status":
                "MARKED VALUATION ONLY / PERFORMANCE NOT EVALUATED",
            "nav": None,
            "cumulative_return": None,
            "realized_return": None,
            "max_drawdown": None,
            "hit_rate": None,
            "expectancy": None,
            "egx_attribution": None,
            "us_attribution": None,
            "combined_attribution": None,
        },
    }
