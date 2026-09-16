"""Authenticated historical native-currency portfolio valuation series.

This is a read-only analytical boundary over immutable audited daily
portfolio snapshots.  It does not replace M7 realized-trade economics and
does not infer equal-period risk-adjusted performance.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from app.paper.shadow_daily_snapshots import (
    _canonical,
    audit_cash_only_daily_portfolio_snapshot,
    audit_marked_daily_portfolio_snapshot,
)
from app.paper.shadow_portfolio import (
    ShadowPortfolioPolicy,
    audit_portfolio_policy,
)


D = Decimal

SCHEMA = "shadow-daily-portfolio-series-v1"
LABEL = "EXPERIMENTAL / PAPER ONLY"
PERFORMANCE_STATUS = (
    "VALUATION SERIES ONLY / M7 PERFORMANCE NOT EVALUATED"
)


@dataclass(frozen=True, slots=True)
class CashSnapshotRequest:
    """Request one historical authenticated cash-only snapshot."""

    snapshot_date_utc: date

    def __post_init__(self) -> None:
        if type(self.snapshot_date_utc) is not date:
            raise ValueError("exact UTC snapshot date required")


@dataclass(frozen=True, slots=True)
class MarkedSnapshotRequest:
    """Request one historical authenticated gross-marked snapshot."""

    snapshot_date_utc: date
    mark_requests: tuple[Any, ...]

    def __post_init__(self) -> None:
        if type(self.snapshot_date_utc) is not date:
            raise ValueError("exact UTC snapshot date required")
        if type(self.mark_requests) is not tuple:
            raise ValueError(
                "marked snapshot requests must be an exact tuple"
            )


def _validate_request_series(
    requests: tuple[
        CashSnapshotRequest | MarkedSnapshotRequest,
        ...,
    ],
) -> None:
    if type(requests) is not tuple:
        raise ValueError(
            "daily portfolio series requires an exact tuple"
        )

    if len(requests) < 2:
        raise ValueError(
            "daily portfolio series requires at least two observations"
        )

    previous: date | None = None

    for request in requests:
        if type(request) not in {
            CashSnapshotRequest,
            MarkedSnapshotRequest,
        }:
            raise ValueError(
                "daily portfolio series requires exact snapshot requests"
            )

        current = request.snapshot_date_utc

        if previous is not None and current <= previous:
            raise ValueError(
                "snapshot dates must be strictly increasing"
            )

        previous = current


def _audit_request(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    request: CashSnapshotRequest | MarkedSnapshotRequest,
) -> dict:
    if type(request) is CashSnapshotRequest:
        return audit_cash_only_daily_portfolio_snapshot(
            directory,
            portfolio,
            request.snapshot_date_utc,
        )

    if type(request) is MarkedSnapshotRequest:
        return audit_marked_daily_portfolio_snapshot(
            directory,
            portfolio,
            request.snapshot_date_utc,
            request.mark_requests,
        )

    raise ValueError(
        "daily portfolio series requires exact snapshot requests"
    )


def _nav(value: object) -> Decimal:
    if type(value) is not str:
        raise ValueError(
            "gross marked NAV requires canonical decimal string"
        )

    if value != value.strip():
        raise ValueError(
            "gross marked NAV requires canonical decimal string"
        )

    try:
        result = D(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(
            "invalid gross marked NAV"
        ) from None

    if not result.is_finite() or result < 0:
        raise ValueError(
            "invalid gross marked NAV"
        )

    return result


def _observation(
    event: dict,
    request: CashSnapshotRequest | MarkedSnapshotRequest,
    *,
    policy_id: str,
    currency: str,
) -> tuple[dict, Decimal]:
    if type(event) is not dict:
        raise ValueError(
            "snapshot audit did not return an authenticated event"
        )

    expected_status = (
        "AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION"
        if type(request) is CashSnapshotRequest
        else "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
    )

    if event.get("snapshot_date_utc") != (
        request.snapshot_date_utc.isoformat()
    ):
        raise ValueError("snapshot date mismatch")

    if event.get("policy_id") != policy_id:
        raise ValueError(
            "snapshot series policy mismatch"
        )

    if event.get("currency") != currency:
        raise ValueError(
            "snapshot series currency mismatch"
        )

    if event.get("valuation_status") != expected_status:
        raise ValueError(
            "snapshot valuation status does not match request"
        )

    if event.get("performance_status") != "PERFORMANCE NOT EVALUATED":
        raise ValueError(
            "snapshot performance semantics must remain not evaluated"
        )

    recorded_at_raw = event.get("recorded_at")

    if type(recorded_at_raw) is not str:
        raise ValueError(
            "snapshot recorded_at must be canonical UTC datetime"
        )

    try:
        recorded_at = datetime.fromisoformat(recorded_at_raw)
    except ValueError:
        raise ValueError(
            "snapshot recorded_at must be canonical UTC datetime"
        ) from None

    if (
        recorded_at.tzinfo is None
        or recorded_at.utcoffset() != timezone.utc.utcoffset(None)
        or recorded_at.date() != request.snapshot_date_utc
    ):
        raise ValueError(
            "snapshot recorded_at must match UTC snapshot date"
        )

    snapshot_id = event.get("snapshot_id")

    if (
        type(snapshot_id) is not str
        or len(snapshot_id) != 64
        or any(
            char not in "0123456789abcdef"
            for char in snapshot_id
        )
    ):
        raise ValueError("invalid snapshot id")

    valuation = event.get("valuation")

    if type(valuation) is not dict:
        raise ValueError("invalid snapshot valuation")

    nav = _nav(
        valuation.get("gross_marked_nav")
    )

    observation = {
        "snapshot_date_utc":
            request.snapshot_date_utc.isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_receipt_sha256": hashlib.sha256(
            _canonical(event)
        ).hexdigest(),
        "recorded_at": recorded_at_raw,
        "valuation_status": expected_status,
        "gross_marked_nav": str(nav),
    }

    return observation, nav


def _intervals(
    observations: list[dict],
    navs: list[Decimal],
) -> list[dict]:
    result = []

    for index in range(1, len(observations)):
        previous = observations[index - 1]
        current = observations[index]

        previous_nav = navs[index - 1]
        current_nav = navs[index]

        if previous_nav <= 0:
            raise ValueError(
                "portfolio return requires positive previous "
                "gross marked NAV"
            )

        start_date = date.fromisoformat(
            previous["snapshot_date_utc"]
        )
        end_date = date.fromisoformat(
            current["snapshot_date_utc"]
        )

        gross_return = (
            current_nav - previous_nav
        ) / previous_nav

        result.append(
            {
                "start_date_utc":
                    start_date.isoformat(),
                "end_date_utc":
                    end_date.isoformat(),
                "elapsed_days":
                    (end_date - start_date).days,
                "start_snapshot_id":
                    previous["snapshot_id"],
                "end_snapshot_id":
                    current["snapshot_id"],
                "gross_marked_return":
                    str(gross_return),
            }
        )

    return result


def _summary(
    observations: list[dict],
    navs: list[Decimal],
) -> dict:
    starting = navs[0]
    ending = navs[-1]

    if starting <= 0:
        raise ValueError(
            "portfolio return requires positive previous "
            "gross marked NAV"
        )

    peak = starting
    peak_date = observations[0]["snapshot_date_utc"]
    maximum_peak = starting

    max_drawdown_amount = D(0)
    max_drawdown_fraction = D(0)

    max_amount_peak_date = None
    max_amount_trough_date = None

    max_fraction_peak_date = None
    max_fraction_trough_date = None

    for observation, nav in zip(
        observations,
        navs,
        strict=True,
    ):
        if nav > maximum_peak:
            maximum_peak = nav

        if nav > peak:
            peak = nav
            peak_date = observation["snapshot_date_utc"]

        drawdown_amount = peak - nav

        if peak <= 0:
            raise ValueError(
                "marked drawdown requires positive peak NAV"
            )

        drawdown_fraction = (
            drawdown_amount / peak
        )

        if drawdown_amount > max_drawdown_amount:
            max_drawdown_amount = drawdown_amount
            max_amount_peak_date = peak_date
            max_amount_trough_date = (
                observation["snapshot_date_utc"]
            )

        if drawdown_fraction > max_drawdown_fraction:
            max_drawdown_fraction = drawdown_fraction
            max_fraction_peak_date = peak_date
            max_fraction_trough_date = (
                observation["snapshot_date_utc"]
            )

    cumulative = (
        ending - starting
    ) / starting

    return {
        "starting_gross_marked_nav":
            str(starting),
        "ending_gross_marked_nav":
            str(ending),
        "peak_gross_marked_nav":
            str(maximum_peak),
        "cumulative_gross_marked_return":
            str(cumulative),
        "max_drawdown_amount":
            str(max_drawdown_amount),
        "max_drawdown_fraction":
            str(max_drawdown_fraction),
        "max_drawdown_amount_peak_date_utc":
            max_amount_peak_date,
        "max_drawdown_amount_trough_date_utc":
            max_amount_trough_date,
        "max_drawdown_fraction_peak_date_utc":
            max_fraction_peak_date,
        "max_drawdown_fraction_trough_date_utc":
            max_fraction_trough_date,
    }


def authenticated_daily_portfolio_series(
    directory: Path,
    portfolio: ShadowPortfolioPolicy,
    requests: tuple[
        CashSnapshotRequest | MarkedSnapshotRequest,
        ...,
    ],
) -> dict:
    """Build a historical series only from authenticated daily snapshots."""
    directory = Path(directory)

    if type(portfolio) is not ShadowPortfolioPolicy:
        raise TypeError("ShadowPortfolioPolicy required")

    _validate_request_series(requests)

    policy_receipt = audit_portfolio_policy(
        directory,
        portfolio,
    )

    policy_id = policy_receipt["policy_id"]
    currency = portfolio.base_currency

    observations: list[dict] = []
    navs: list[Decimal] = []
    seen_snapshot_ids: set[str] = set()

    with localcontext(Context(prec=34)):
        for request in requests:
            event = _audit_request(
                directory,
                portfolio,
                request,
            )

            observation, nav = _observation(
                event,
                request,
                policy_id=policy_id,
                currency=currency,
            )

            snapshot_id = observation["snapshot_id"]

            if snapshot_id in seen_snapshot_ids:
                raise ValueError(
                    "duplicate snapshot identity in portfolio series"
                )

            seen_snapshot_ids.add(snapshot_id)
            observations.append(observation)
            navs.append(nav)

        intervals = _intervals(
            observations,
            navs,
        )

        summary = _summary(
            observations,
            navs,
        )

        basis = {
            "schema_version": SCHEMA,
            "label": LABEL,
            "scoring": "NOT SCORED",
            "performance_status": PERFORMANCE_STATUS,
            "policy_id": policy_id,
            "policy_receipt_sha256": hashlib.sha256(
                _canonical(policy_receipt)
            ).hexdigest(),
            "currency": currency,
            "observations": observations,
            "intervals": intervals,
            "summary": summary,
        }

        return basis | {
            "series_id": hashlib.sha256(
                _canonical(basis)
            ).hexdigest(),
        }
