"""Immutable daily native-currency portfolio valuation snapshots."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path

from app.paper.shadow_allocations import (
    _allocation_lock,
    _read_reservations,
    _read_settlements,
)
from app.paper.shadow_collection import LABEL, _publish_once
from app.paper.shadow_daily_portfolio import (
    ContinuationMarkRequest,
    SingleSessionMarkRequest,
    _authenticated_open_mark_from_report,
    _report_for_mark_request,
    multi_position_marked_portfolio_view,
)
from app.paper.shadow_exits import (
    audit_continuation_exit_event,
    audit_exit_event,
)
from app.paper.shadow_fills import _canonical, _utc
from app.paper.shadow_portfolio import (
    ShadowPortfolioPolicy,
    audit_portfolio_policy,
)


SCHEMA = "shadow-daily-portfolio-snapshot-v1"
EVENT_TYPE = "DAILY_PORTFOLIO_SNAPSHOT"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(
                "duplicate daily portfolio snapshot field"
            )
        result[key] = value
    return result


def _hex64(value, message: str) -> str:
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise ValueError(message)
    return value


def _amount(value, message: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(message) from None
    if not result.is_finite() or result < 0:
        raise ValueError(message)
    return result


def daily_portfolio_snapshot_path(
    directory: Path,
    snapshot_date_utc: date,
) -> Path:
    """Address one immutable UTC reporting-day snapshot."""
    if type(snapshot_date_utc) is not date:
        raise ValueError("exact UTC snapshot date required")
    return (
        Path(directory)
        / "daily-portfolio-snapshots"
        / f"{snapshot_date_utc.isoformat()}.json"
    )


def _reservation_ref(event: dict) -> dict:
    return {
        "reservation_id": event["reservation_id"],
        "receipt_sha256": hashlib.sha256(
            _canonical(event)
        ).hexdigest(),
    }


def _settlement_ref(event: dict) -> dict:
    return {
        "settlement_id": event["settlement_id"],
        "reservation_id": event["reservation_id"],
        "receipt_sha256": hashlib.sha256(
            _canonical(event)
        ).hexdigest(),
    }


def _cash_from_frozen_ledger(
    portfolio: ShadowPortfolioPolicy,
    reservations: list[dict],
    settlements: list[dict],
) -> Decimal:
    with localcontext(Context(prec=34)):
        historical_reserved = sum(
            (
                Decimal(item["capital_reserved"])
                for item in reservations
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
        return (
            portfolio.initial_capital
            - historical_reserved
            + settled_proceeds
        )


def _validate_cash_only_valuation(
    valuation: dict,
    portfolio: ShadowPortfolioPolicy,
    expected_cash: Decimal,
) -> None:
    if type(valuation) is not dict:
        raise ValueError("daily snapshot valuation must be an object")

    if (
        valuation.get("schema_version")
        != "shadow-gross-marked-portfolio-view-v2"
        or valuation.get("currency") != portfolio.base_currency
        or valuation.get("open_position_count") != 0
        or valuation.get("open_positions") != []
    ):
        raise ValueError(
            "daily snapshot is not authenticated cash-only valuation"
        )

    if _amount(
        valuation.get("open_market_value"),
        "invalid daily snapshot open market value",
    ) != 0:
        raise ValueError(
            "cash-only daily snapshot has open market value"
        )

    cash = _amount(
        valuation.get("cash"),
        "invalid daily snapshot cash",
    )
    marked = _amount(
        valuation.get("gross_marked_nav"),
        "invalid daily snapshot marked NAV",
    )
    initial = _amount(
        valuation.get("initial_capital"),
        "invalid daily snapshot initial capital",
    )

    if (
        cash != expected_cash
        or marked != expected_cash
        or initial != portfolio.initial_capital
    ):
        raise ValueError(
            "daily snapshot cash does not bind frozen ledger"
        )

    performance = valuation.get("performance")
    expected_performance = {
        "status",
        "nav",
        "cumulative_return",
        "realized_return",
        "max_drawdown",
        "hit_rate",
        "expectancy",
        "egx_attribution",
        "us_attribution",
        "combined_attribution",
    }

    if (
        type(performance) is not dict
        or set(performance) != expected_performance
        or performance.get("status")
        != "MARKED VALUATION ONLY / PERFORMANCE NOT EVALUATED"
        or any(
            value is not None
            for key, value in performance.items()
            if key != "status"
        )
    ):
        raise ValueError(
            "daily snapshot must not claim performance"
        )


def _cash_accounting_from_frozen_ledger(
    portfolio: ShadowPortfolioPolicy,
    reservations: list[dict],
    settlements: list[dict],
) -> dict:
    settled_ids = {
        item["reservation_id"]
        for item in settlements
    }
    active = [
        item
        for item in reservations
        if item["reservation_id"] not in settled_ids
    ]

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

        realized_pnl = settled_proceeds - released_capital
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
            "daily snapshot cash accounting identity failed"
        )

    return {
        "initial_capital": initial,
        "cash": cash_from_flows,
        "active_capital_reserved": active_reserved,
        "historical_capital_reserved": historical_reserved,
        "settled_net_exit_proceeds": settled_proceeds,
        "realized_pnl": realized_pnl,
        "reservation_count": len(reservations),
        "active_reservation_count": len(active),
        "settlement_count": len(settlements),
    }


def _validate_exact_cash_only_valuation(
    valuation: dict,
    portfolio: ShadowPortfolioPolicy,
    policy_id: str,
    reservations: list[dict],
    settlements: list[dict],
) -> None:
    accounting = _cash_accounting_from_frozen_ledger(
        portfolio,
        reservations,
        settlements,
    )

    if accounting["active_reservation_count"] != 0:
        raise ValueError(
            "cash-only daily snapshot contains active reservation"
        )

    _validate_cash_only_valuation(
        valuation,
        portfolio,
        accounting["cash"],
    )

    expected_keys = {
        "schema_version",
        "label",
        "scope",
        "status",
        "policy_id",
        "currency",
        "initial_capital",
        "cash",
        "active_capital_reserved",
        "historical_capital_reserved",
        "settled_net_exit_proceeds",
        "realized_pnl",
        "reservation_count",
        "active_reservation_count",
        "settlement_count",
        "open_market_value",
        "gross_marked_nav",
        "performance_status",
        "open_position_count",
        "open_positions",
        "gross_marked_nav_status",
        "performance",
    }

    if type(valuation) is not dict or set(valuation) != expected_keys:
        raise ValueError(
            "unexpected daily snapshot valuation fields"
        )

    expected = {
        "schema_version":
            "shadow-gross-marked-portfolio-view-v2",
        "label": LABEL,
        "scope": (
            "ONE SHARED NATIVE-CURRENCY PORTFOLIO / "
            "ALL ACTIVE RESERVATIONS AUTHENTICATED"
        ),
        "status": (
            "AUTHENTICATED NATIVE CASH / "
            "NO ACTIVE RESERVATIONS"
        ),
        "policy_id": policy_id,
        "currency": portfolio.base_currency,
        "initial_capital": str(
            accounting["initial_capital"]
        ),
        "cash": str(accounting["cash"]),
        "active_capital_reserved": str(
            accounting["active_capital_reserved"]
        ),
        "historical_capital_reserved": str(
            accounting["historical_capital_reserved"]
        ),
        "settled_net_exit_proceeds": str(
            accounting["settled_net_exit_proceeds"]
        ),
        "realized_pnl": str(
            accounting["realized_pnl"]
        ),
        "reservation_count":
            accounting["reservation_count"],
        "active_reservation_count":
            accounting["active_reservation_count"],
        "settlement_count":
            accounting["settlement_count"],
        "performance_status": "NOT EVALUATED",
        "gross_marked_nav_status": (
            "ACCOUNTING CASH / NO ACTIVE RESERVATIONS / "
            "NO LIQUIDATION INFERENCE"
        ),
    }

    for key, value in expected.items():
        if valuation[key] != value:
            raise ValueError(
                "daily snapshot valuation does not bind frozen ledger"
            )


def _active_reservations(
    reservations: list[dict],
    settlements: list[dict],
) -> list[dict]:
    settled_ids = {
        item["reservation_id"]
        for item in settlements
    }

    return [
        item
        for item in reservations
        if item["reservation_id"] not in settled_ids
    ]


def _same_session_mark_material(
    directory: Path,
    request: SingleSessionMarkRequest,
    reservation: dict,
    base_currency: str,
) -> tuple[dict, dict, datetime]:
    """Re-audit one same-session mark and derive durable provenance."""
    if type(request) is not SingleSessionMarkRequest:
        raise ValueError(
            "same-session marked snapshot requires exact "
            "SingleSessionMarkRequest"
        )

    report = _report_for_mark_request(
        Path(directory),
        request,
    )

    mark, _ = _authenticated_open_mark_from_report(
        report,
        reservation,
        base_currency,
    )

    event = audit_exit_event(
        Path(directory),
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

    references = report.get("audit_references")

    if (
        type(references) is not dict
        or references.get("exit_event_id")
        != event["event_id"]
        or references.get("position_event_id")
        != event["position_event_id"]
        or references.get("position_event_sha256")
        != event["position_event_sha256"]
        or references.get("fact_event_id")
        != event["fact_event_id"]
        or references.get("fact_event_sha256")
        != event["fact_event_sha256"]
    ):
        raise ValueError(
            "same-session mark report does not bind audited exit event"
        )

    reservation_ref = _reservation_ref(
        reservation
    )

    provenance = {
        "evaluation_kind": "SAME_SESSION",
        "candidate_position_key":
            reservation["candidate_position_key"],
        "reservation_id":
            reservation["reservation_id"],
        "reservation_receipt_sha256":
            reservation_ref["receipt_sha256"],
        "position_event_id":
            reservation["position_event_id"],
        "position_event_sha256":
            reservation["position_event_sha256"],
        "exit_event_id":
            event["event_id"],
        "exit_event_receipt_sha256":
            hashlib.sha256(
                _canonical(event)
            ).hexdigest(),
        "fact_record_id":
            event["fact_record_id"],
        "fact_event_id":
            event["fact_event_id"],
        "fact_event_sha256":
            event["fact_event_sha256"],
        "market":
            reservation["market"],
        "currency":
            reservation["currency"],
        "marked_through_sequence":
            mark["marked_through_sequence"],
        "mark_interval_end":
            mark["mark_interval_end"],
        "mark_known_at":
            mark["mark_known_at"],
        "mark_price":
            mark["mark_price"],
        "gross_market_value":
            mark["gross_market_value"],
    }

    return (
        mark,
        provenance,
        _utc(datetime.fromisoformat(
            event["recorded_at"]
        )),
    )


def _continuation_mark_material(
    directory: Path,
    request: ContinuationMarkRequest,
    reservation: dict,
    base_currency: str,
) -> tuple[dict, dict, datetime]:
    """Re-audit one continuation OPEN mark and derive durable provenance."""
    if type(request) is not ContinuationMarkRequest:
        raise ValueError(
            "continuation marked snapshot requires exact "
            "ContinuationMarkRequest"
        )

    report = _report_for_mark_request(
        Path(directory),
        request,
    )

    mark, _ = _authenticated_open_mark_from_report(
        report,
        reservation,
        base_currency,
    )

    event = audit_continuation_exit_event(
        Path(directory),
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

    references = report.get("audit_references")

    if (
        type(references) is not dict
        or references.get("exit_event_id")
        != event["event_id"]
        or references.get("position_event_id")
        != event["position_event_id"]
        or references.get("position_event_sha256")
        != event["position_event_sha256"]
        or references.get("continuation_events")
        != event["continuation_events"]
    ):
        raise ValueError(
            "continuation mark report does not bind "
            "audited continuation exit event"
        )

    reservation_ref = _reservation_ref(
        reservation
    )

    provenance = {
        "evaluation_kind": "CONTINUATION",
        "candidate_position_key":
            reservation["candidate_position_key"],
        "reservation_id":
            reservation["reservation_id"],
        "reservation_receipt_sha256":
            reservation_ref["receipt_sha256"],
        "position_event_id":
            reservation["position_event_id"],
        "position_event_sha256":
            reservation["position_event_sha256"],
        "exit_event_id":
            event["event_id"],
        "exit_event_receipt_sha256":
            hashlib.sha256(
                _canonical(event)
            ).hexdigest(),
        "continuation_events":
            event["continuation_events"],
        "market":
            reservation["market"],
        "currency":
            reservation["currency"],
        "marked_through_sequence":
            mark["marked_through_sequence"],
        "mark_interval_end":
            mark["mark_interval_end"],
        "mark_known_at":
            mark["mark_known_at"],
        "mark_price":
            mark["mark_price"],
        "gross_market_value":
            mark["gross_market_value"],
    }

    return (
        mark,
        provenance,
        _utc(
            datetime.fromisoformat(
                event["recorded_at"]
            )
        ),
    )


def _mark_materials(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    reservations: list[dict],
    settlements: list[dict],
    requests: tuple[
        SingleSessionMarkRequest | ContinuationMarkRequest,
        ...,
    ],
) -> tuple[list[dict], list[dict], list[datetime]]:
    """Bind one exact authenticated request to every frozen active reservation."""
    if type(requests) is not tuple:
        raise ValueError(
            "daily snapshot mark requests must be an exact tuple"
        )

    active = _active_reservations(
        reservations,
        settlements,
    )

    active_by_key = {
        item["candidate_position_key"]: item
        for item in active
    }

    if len(active_by_key) != len(active):
        raise ValueError(
            "duplicate active candidate position key"
        )

    marks = {}
    provenance = {}
    exit_recorded_at = []

    for request in requests:
        if type(request) not in (
            SingleSessionMarkRequest,
            ContinuationMarkRequest,
        ):
            raise ValueError(
                "daily snapshot requires exact authenticated "
                "portfolio mark requests"
            )

        report = _report_for_mark_request(
            Path(directory),
            request,
        )

        opened = report.get(
            "open_paper_positions"
        )

        position = (
            opened.get("position")
            if type(opened) is dict
            else None
        )

        if type(position) is not dict:
            raise ValueError(
                "daily snapshot requires authenticated OPEN mark"
            )

        key = position.get(
            "candidate_position_key"
        )

        if key in marks:
            raise ValueError(
                "duplicate daily snapshot mark request"
            )

        reservation = active_by_key.get(key)

        if reservation is None:
            raise ValueError(
                "daily snapshot mark has no frozen active reservation"
            )

        if type(request) is SingleSessionMarkRequest:
            material = _same_session_mark_material(
                Path(directory),
                request,
                reservation,
                portfolio.base_currency,
            )
        else:
            material = _continuation_mark_material(
                Path(directory),
                request,
                reservation,
                portfolio.base_currency,
            )

        mark, item, event_recorded_at = material

        marks[key] = mark
        provenance[key] = item
        exit_recorded_at.append(
            event_recorded_at
        )

    if set(marks) != set(active_by_key):
        raise ValueError(
            "every frozen active reservation requires exactly "
            "one authenticated mark"
        )

    ordered = sorted(marks)

    return (
        [
            marks[key]
            for key in ordered
        ],
        [
            provenance[key]
            for key in ordered
        ],
        exit_recorded_at,
    )


def _expected_marked_valuation(
    portfolio: ShadowPortfolioPolicy,
    policy_id: str,
    reservations: list[dict],
    settlements: list[dict],
    marks: list[dict],
) -> dict:
    """Re-derive exact gross marked valuation from a frozen ledger cut."""
    accounting = _cash_accounting_from_frozen_ledger(
        portfolio,
        reservations,
        settlements,
    )

    active = _active_reservations(
        reservations,
        settlements,
    )

    active_by_key = {
        item["candidate_position_key"]: item
        for item in active
    }

    mark_by_key = {}

    for mark in marks:
        if type(mark) is not dict:
            raise ValueError(
                "invalid authenticated daily snapshot mark"
            )

        key = mark.get(
            "candidate_position_key"
        )

        if key in mark_by_key:
            raise ValueError(
                "duplicate authenticated daily snapshot mark"
            )

        reservation = active_by_key.get(key)

        if (
            reservation is None
            or mark.get("reservation_id")
            != reservation["reservation_id"]
            or mark.get("currency")
            != portfolio.base_currency
        ):
            raise ValueError(
                "daily snapshot mark does not bind frozen reservation"
            )

        try:
            value = Decimal(
                mark["gross_market_value"]
            )
        except (
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise ValueError(
                "invalid daily snapshot gross market value"
            ) from None

        if (
            not value.is_finite()
            or value < 0
        ):
            raise ValueError(
                "invalid daily snapshot gross market value"
            )

        mark_by_key[key] = (
            mark,
            value,
        )

    if set(mark_by_key) != set(active_by_key):
        raise ValueError(
            "daily snapshot marks do not cover frozen active ledger"
        )

    ordered_keys = sorted(mark_by_key)

    with localcontext(Context(prec=34)):
        open_market_value = sum(
            (
                mark_by_key[key][1]
                for key in ordered_keys
            ),
            Decimal(0),
        )
        gross_marked_nav = (
            accounting["cash"]
            + open_market_value
        )

    return {
        "schema_version":
            "shadow-gross-marked-portfolio-view-v2",
        "label": LABEL,
        "scope": (
            "ONE SHARED NATIVE-CURRENCY PORTFOLIO / "
            "ALL ACTIVE RESERVATIONS AUTHENTICATED"
        ),
        "status": (
            "AUTHENTICATED NATIVE CASH + "
            "OBSERVED GROSS OPEN MARKS"
        ),
        "policy_id": policy_id,
        "currency": portfolio.base_currency,
        "initial_capital": str(
            accounting["initial_capital"]
        ),
        "cash": str(
            accounting["cash"]
        ),
        "active_capital_reserved": str(
            accounting["active_capital_reserved"]
        ),
        "historical_capital_reserved": str(
            accounting["historical_capital_reserved"]
        ),
        "settled_net_exit_proceeds": str(
            accounting["settled_net_exit_proceeds"]
        ),
        "realized_pnl": str(
            accounting["realized_pnl"]
        ),
        "reservation_count":
            accounting["reservation_count"],
        "active_reservation_count":
            accounting["active_reservation_count"],
        "settlement_count":
            accounting["settlement_count"],
        "open_market_value":
            str(open_market_value),
        "gross_marked_nav":
            str(gross_marked_nav),
        "performance_status":
            "NOT EVALUATED",
        "open_position_count":
            len(ordered_keys),
        "open_positions": [
            mark_by_key[key][0]
            for key in ordered_keys
        ],
        "gross_marked_nav_status": (
            "ACCOUNTING CASH PLUS AUTHENTICATED OBSERVED "
            "GROSS MARKET VALUE / ENTRY COST INCLUDED / "
            "BEFORE LIQUIDATION COST AND SLIPPAGE"
        ),
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


def _basis(
    snapshot_date_utc: date,
    policy_receipt: dict,
    portfolio: ShadowPortfolioPolicy,
    reservations: list[dict],
    settlements: list[dict],
    valuation: dict,
) -> dict:
    return {
        "schema_version": SCHEMA,
        "label": LABEL,
        "event_type": EVENT_TYPE,
        "scoring": "NOT SCORED",
        "valuation_status": (
            "AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION"
        ),
        "performance_status": "PERFORMANCE NOT EVALUATED",
        "snapshot_date_utc": snapshot_date_utc.isoformat(),
        "policy_id": policy_receipt["policy_id"],
        "policy_receipt_sha256": hashlib.sha256(
            _canonical(policy_receipt)
        ).hexdigest(),
        "currency": portfolio.base_currency,
        "ledger": {
            "reservations": [
                _reservation_ref(item)
                for item in reservations
            ],
            "settlements": sorted(
                (
                    _settlement_ref(item)
                    for item in settlements
                ),
                key=lambda item: item["settlement_id"],
            ),
        },
        "mark_provenance": [],
        "valuation": valuation,
    }


def _marked_basis(
    snapshot_date_utc: date,
    policy_receipt: dict,
    portfolio: ShadowPortfolioPolicy,
    reservations: list[dict],
    settlements: list[dict],
    valuation: dict,
    mark_provenance: list[dict],
) -> dict:
    """Build the same v1 receipt envelope with authenticated OPEN marks."""
    basis = _basis(
        snapshot_date_utc,
        policy_receipt,
        portfolio,
        reservations,
        settlements,
        valuation,
    )

    return basis | {
        "valuation_status": (
            "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
        ),
        "mark_provenance": mark_provenance,
    }


def append_marked_daily_portfolio_snapshot(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    requests: tuple[
        SingleSessionMarkRequest | ContinuationMarkRequest,
        ...,
    ],
) -> Path:
    """Freeze today's authenticated gross marked portfolio valuation."""
    if type(requests) is not tuple:
        raise ValueError(
            "daily snapshot mark requests must be an exact tuple"
        )

    if any(
        type(request) not in (
            SingleSessionMarkRequest,
            ContinuationMarkRequest,
        )
        for request in requests
    ):
        raise ValueError(
            "marked daily snapshot requires exact "
            "authenticated portfolio mark requests"
        )

    directory = Path(directory)

    with _allocation_lock(directory):
        policy_receipt = audit_portfolio_policy(
            directory,
            portfolio,
        )

        reservations = _read_reservations(
            directory
        )
        settlements = _read_settlements(
            directory,
            reservations,
        )

        active = _active_reservations(
            reservations,
            settlements,
        )

        if not active:
            raise ValueError(
                "marked daily snapshot requires active reservations"
            )

        valuation = multi_position_marked_portfolio_view(
            directory,
            portfolio,
            requests,
        )

        (
            marks,
            provenance,
            exit_recorded_at,
        ) = _mark_materials(
            directory,
            portfolio,
            reservations,
            settlements,
            requests,
        )

        expected_valuation = _expected_marked_valuation(
            portfolio,
            policy_receipt["policy_id"],
            reservations,
            settlements,
            marks,
        )

        if valuation != expected_valuation:
            raise ValueError(
                "marked daily snapshot valuation does not "
                "bind re-audited marks"
            )

        recorded_at = _utc(_now())

        clocks = [
            _utc(
                datetime.fromisoformat(
                    policy_receipt["frozen_at"]
                )
            ),
            *(
                _utc(
                    datetime.fromisoformat(
                        item["recorded_at"]
                    )
                )
                for item in reservations
            ),
            *(
                _utc(
                    datetime.fromisoformat(
                        item["recorded_at"]
                    )
                )
                for item in settlements
            ),
            *exit_recorded_at,
            *(
                _utc(
                    datetime.fromisoformat(
                        item["mark_known_at"]
                    )
                )
                for item in provenance
            ),
        ]

        if recorded_at <= max(clocks):
            raise ValueError(
                "marked daily snapshot must follow authenticated inputs"
            )

        snapshot_date_utc = recorded_at.date()

        basis = _marked_basis(
            snapshot_date_utc,
            policy_receipt,
            portfolio,
            reservations,
            settlements,
            valuation,
            provenance,
        )

        payload = basis | {
            "snapshot_id": hashlib.sha256(
                _canonical(basis)
            ).hexdigest(),
            "recorded_at":
                recorded_at.isoformat(),
        }

        path = _publish_once(
            daily_portfolio_snapshot_path(
                directory,
                snapshot_date_utc,
            ),
            payload,
        )

        if _utc(_now()) < recorded_at:
            path.unlink()
            raise ValueError(
                "clock rollback during marked daily snapshot publication"
            )

        return path


def append_cash_only_daily_portfolio_snapshot(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
) -> Path:
    """Freeze today's UTC cash-only valuation exactly once."""
    directory = Path(directory)

    with _allocation_lock(directory):
        policy_receipt = audit_portfolio_policy(
            directory,
            portfolio,
        )
        reservations = _read_reservations(directory)
        settlements = _read_settlements(
            directory,
            reservations,
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

        if active:
            raise ValueError(
                "cash-only daily snapshot requires no active reservations"
            )

        valuation = multi_position_marked_portfolio_view(
            directory,
            portfolio,
            (),
        )

        _validate_exact_cash_only_valuation(
            valuation,
            portfolio,
            policy_receipt["policy_id"],
            reservations,
            settlements,
        )

        recorded_at = _utc(_now())

        clocks = [
            _utc(datetime.fromisoformat(
                policy_receipt["frozen_at"]
            )),
            *(
                _utc(datetime.fromisoformat(
                    item["recorded_at"]
                ))
                for item in reservations
            ),
            *(
                _utc(datetime.fromisoformat(
                    item["recorded_at"]
                ))
                for item in settlements
            ),
        ]

        if recorded_at <= max(clocks):
            raise ValueError(
                "daily snapshot must follow authenticated inputs"
            )

        snapshot_date_utc = recorded_at.date()

        basis = _basis(
            snapshot_date_utc,
            policy_receipt,
            portfolio,
            reservations,
            settlements,
            valuation,
        )

        payload = basis | {
            "snapshot_id": hashlib.sha256(
                _canonical(basis)
            ).hexdigest(),
            "recorded_at": recorded_at.isoformat(),
        }

        path = _publish_once(
            daily_portfolio_snapshot_path(
                directory,
                snapshot_date_utc,
            ),
            payload,
        )

        if _utc(_now()) < recorded_at:
            path.unlink()
            raise ValueError(
                "clock rollback during daily snapshot publication"
            )

        return path


def _validate_refs(
    event: dict,
    current_reservations: list[dict],
    current_settlements: list[dict],
    recorded_at: datetime,
) -> tuple[list[dict], list[dict]]:
    """Reconstruct the exact authenticated ledger cut before this snapshot."""
    ledger = event.get("ledger")

    if (
        type(ledger) is not dict
        or set(ledger) != {
            "reservations",
            "settlements",
        }
        or type(ledger["reservations"]) is not list
        or type(ledger["settlements"]) is not list
    ):
        raise ValueError(
            "invalid daily snapshot ledger provenance"
        )

    frozen_reservations = [
        item
        for item in current_reservations
        if _utc(
            datetime.fromisoformat(
                item["recorded_at"]
            )
        ) < recorded_at
    ]

    frozen_settlements = [
        item
        for item in current_settlements
        if _utc(
            datetime.fromisoformat(
                item["recorded_at"]
            )
        ) < recorded_at
    ]

    expected_reservations = [
        _reservation_ref(item)
        for item in frozen_reservations
    ]

    expected_settlements = sorted(
        (
            _settlement_ref(item)
            for item in frozen_settlements
        ),
        key=lambda item: item["settlement_id"],
    )

    if (
        ledger["reservations"] != expected_reservations
        or ledger["settlements"] != expected_settlements
    ):
        raise ValueError(
            "daily snapshot ledger provenance does not "
            "match historical cutoff"
        )

    return frozen_reservations, frozen_settlements


def audit_cash_only_daily_portfolio_snapshot(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    snapshot_date_utc: date,
) -> dict:
    """Audit one historical cash-only snapshot without using later ledger state."""
    directory = Path(directory)
    path = daily_portfolio_snapshot_path(
        directory,
        snapshot_date_utc,
    )

    event = json.loads(
        path.read_bytes(),
        object_pairs_hook=_unique,
    )

    expected_fields = {
        "schema_version",
        "label",
        "event_type",
        "scoring",
        "valuation_status",
        "performance_status",
        "snapshot_date_utc",
        "policy_id",
        "policy_receipt_sha256",
        "currency",
        "ledger",
        "mark_provenance",
        "valuation",
        "snapshot_id",
        "recorded_at",
    }

    if type(event) is not dict or set(event) != expected_fields:
        raise ValueError(
            "unexpected daily portfolio snapshot fields"
        )

    if (
        event["schema_version"] != SCHEMA
        or event["label"] != LABEL
        or event["event_type"] != EVENT_TYPE
        or event["scoring"] != "NOT SCORED"
        or event["valuation_status"]
        != "AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION"
        or event["performance_status"]
        != "PERFORMANCE NOT EVALUATED"
        or event["currency"] != portfolio.base_currency
        or event["mark_provenance"] != []
    ):
        raise ValueError(
            "invalid daily portfolio snapshot semantics"
        )

    if event["snapshot_date_utc"] != snapshot_date_utc.isoformat():
        raise ValueError(
            "daily portfolio snapshot date mismatch"
        )

    _hex64(
        event["policy_id"],
        "invalid daily snapshot policy id",
    )
    _hex64(
        event["policy_receipt_sha256"],
        "invalid daily snapshot policy hash",
    )
    _hex64(
        event["snapshot_id"],
        "invalid daily snapshot id",
    )

    recorded_at = _utc(
        datetime.fromisoformat(event["recorded_at"])
    )

    if (
        recorded_at.date() != snapshot_date_utc
        or recorded_at > _utc(_now())
    ):
        raise ValueError(
            "invalid daily portfolio snapshot clock"
        )

    basis = {
        key: value
        for key, value in event.items()
        if key not in {"snapshot_id", "recorded_at"}
    }

    if event["snapshot_id"] != hashlib.sha256(
        _canonical(basis)
    ).hexdigest():
        raise ValueError(
            "daily portfolio snapshot hash mismatch"
        )

    policy_receipt = audit_portfolio_policy(
        directory,
        portfolio,
    )

    if (
        event["policy_id"] != policy_receipt["policy_id"]
        or event["policy_receipt_sha256"]
        != hashlib.sha256(
            _canonical(policy_receipt)
        ).hexdigest()
    ):
        raise ValueError(
            "daily snapshot does not bind portfolio policy"
        )

    policy_frozen_at = _utc(
        datetime.fromisoformat(
            policy_receipt["frozen_at"]
        )
    )

    if recorded_at <= policy_frozen_at:
        raise ValueError(
            "daily snapshot does not follow portfolio policy"
        )

    current_reservations = _read_reservations(directory)
    current_settlements = _read_settlements(
        directory,
        current_reservations,
    )

    (
        frozen_reservations,
        frozen_settlements,
    ) = _validate_refs(
        event,
        current_reservations,
        current_settlements,
        recorded_at,
    )

    _validate_exact_cash_only_valuation(
        event["valuation"],
        portfolio,
        policy_receipt["policy_id"],
        frozen_reservations,
        frozen_settlements,
    )

    return event

def audit_marked_daily_portfolio_snapshot(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    snapshot_date_utc: date,
    requests: tuple[
        SingleSessionMarkRequest | ContinuationMarkRequest,
        ...,
    ],
) -> dict:
    """Audit one historical authenticated gross marked daily snapshot."""
    if type(requests) is not tuple:
        raise ValueError(
            "daily snapshot mark requests must be an exact tuple"
        )

    if any(
        type(request) not in (
            SingleSessionMarkRequest,
            ContinuationMarkRequest,
        )
        for request in requests
    ):
        raise ValueError(
            "marked daily snapshot requires exact "
            "authenticated portfolio mark requests"
        )

    directory = Path(directory)

    path = daily_portfolio_snapshot_path(
        directory,
        snapshot_date_utc,
    )

    event = json.loads(
        path.read_bytes(),
        object_pairs_hook=_unique,
    )

    expected_fields = {
        "schema_version",
        "label",
        "event_type",
        "scoring",
        "valuation_status",
        "performance_status",
        "snapshot_date_utc",
        "policy_id",
        "policy_receipt_sha256",
        "currency",
        "ledger",
        "mark_provenance",
        "valuation",
        "snapshot_id",
        "recorded_at",
    }

    if (
        type(event) is not dict
        or set(event) != expected_fields
    ):
        raise ValueError(
            "unexpected daily portfolio snapshot fields"
        )

    if (
        event["schema_version"] != SCHEMA
        or event["label"] != LABEL
        or event["event_type"] != EVENT_TYPE
        or event["scoring"] != "NOT SCORED"
        or event["valuation_status"]
        != "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
        or event["performance_status"]
        != "PERFORMANCE NOT EVALUATED"
        or event["currency"]
        != portfolio.base_currency
        or type(event["mark_provenance"]) is not list
        or not event["mark_provenance"]
    ):
        raise ValueError(
            "invalid marked daily portfolio snapshot semantics"
        )

    if (
        event["snapshot_date_utc"]
        != snapshot_date_utc.isoformat()
    ):
        raise ValueError(
            "daily portfolio snapshot date mismatch"
        )

    _hex64(
        event["policy_id"],
        "invalid daily snapshot policy id",
    )
    _hex64(
        event["policy_receipt_sha256"],
        "invalid daily snapshot policy hash",
    )
    _hex64(
        event["snapshot_id"],
        "invalid daily snapshot id",
    )

    recorded_at = _utc(
        datetime.fromisoformat(
            event["recorded_at"]
        )
    )

    if (
        recorded_at.date() != snapshot_date_utc
        or recorded_at > _utc(_now())
    ):
        raise ValueError(
            "invalid daily portfolio snapshot clock"
        )

    basis = {
        key: value
        for key, value in event.items()
        if key not in {
            "snapshot_id",
            "recorded_at",
        }
    }

    if (
        event["snapshot_id"]
        != hashlib.sha256(
            _canonical(basis)
        ).hexdigest()
    ):
        raise ValueError(
            "daily portfolio snapshot hash mismatch"
        )

    policy_receipt = audit_portfolio_policy(
        directory,
        portfolio,
    )

    if (
        event["policy_id"]
        != policy_receipt["policy_id"]
        or event["policy_receipt_sha256"]
        != hashlib.sha256(
            _canonical(policy_receipt)
        ).hexdigest()
    ):
        raise ValueError(
            "daily snapshot does not bind portfolio policy"
        )

    policy_frozen_at = _utc(
        datetime.fromisoformat(
            policy_receipt["frozen_at"]
        )
    )

    if recorded_at <= policy_frozen_at:
        raise ValueError(
            "daily snapshot does not follow portfolio policy"
        )

    current_reservations = _read_reservations(
        directory
    )
    current_settlements = _read_settlements(
        directory,
        current_reservations,
    )

    (
        frozen_reservations,
        frozen_settlements,
    ) = _validate_refs(
        event,
        current_reservations,
        current_settlements,
        recorded_at,
    )

    active = _active_reservations(
        frozen_reservations,
        frozen_settlements,
    )

    if not active:
        raise ValueError(
            "marked daily snapshot has no frozen active reservations"
        )

    (
        marks,
        expected_provenance,
        exit_recorded_at,
    ) = _mark_materials(
        directory,
        portfolio,
        frozen_reservations,
        frozen_settlements,
        requests,
    )

    if event["mark_provenance"] != expected_provenance:
        raise ValueError(
            "marked daily snapshot provenance does not "
            "bind authenticated mark inputs"
        )

    mark_clocks = [
        _utc(
            datetime.fromisoformat(
                item["mark_known_at"]
            )
        )
        for item in expected_provenance
    ]

    if any(
        recorded_at <= clock
        for clock in (
            *exit_recorded_at,
            *mark_clocks,
        )
    ):
        raise ValueError(
            "marked daily snapshot does not follow observed marks"
        )

    expected_valuation = _expected_marked_valuation(
        portfolio,
        policy_receipt["policy_id"],
        frozen_reservations,
        frozen_settlements,
        marks,
    )

    if event["valuation"] != expected_valuation:
        raise ValueError(
            "marked daily snapshot valuation does not "
            "bind frozen authenticated marks"
        )

    return event
