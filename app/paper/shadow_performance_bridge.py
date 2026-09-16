"""Authenticated C1-to-M7 fixed-period return bridge.

This module does not replace M7 trade economics or realized-trade drawdown.
It only admits an authenticated C1 valuation series into the existing
PeriodicReturnSeries contract when receipt spacing is exactly fixed-period.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path

from app.paper.shadow_daily_series import (
    authenticated_daily_portfolio_series,
)
from app.paper.shadow_daily_snapshots import _canonical
from app.paper.shadow_portfolio import (
    ShadowPortfolioPolicy,
    audit_portfolio_policy,
)
from app.performance.extensions import PeriodicReturnSeries
from app.performance.models import PerformanceAnalysisInput


D = Decimal

BRIDGE_SCHEMA = "shadow-periodic-return-bridge-v1"


@dataclass(frozen=True, slots=True)
class PeriodicReturnBridgeConfig:
    """Explicit assumptions required to admit C1 returns into M7."""

    period_seconds: int
    risk_free_return: Decimal
    sortino_target: Decimal
    minimum_samples: int
    annualization_factor: Decimal | None = None

    def __post_init__(self) -> None:
        if (
            type(self.period_seconds) is not int
            or self.period_seconds <= 0
        ):
            raise ValueError(
                "period_seconds must be an exact positive integer"
            )

        if (
            type(self.risk_free_return) is not Decimal
            or not self.risk_free_return.is_finite()
        ):
            raise ValueError(
                "risk_free_return must be an exact finite Decimal"
            )

        if (
            type(self.sortino_target) is not Decimal
            or not self.sortino_target.is_finite()
        ):
            raise ValueError(
                "sortino_target must be an exact finite Decimal"
            )

        if (
            type(self.minimum_samples) is not int
            or self.minimum_samples < 2
        ):
            raise ValueError(
                "minimum_samples must be an exact integer >= 2"
            )

        if self.annualization_factor is not None:
            if (
                type(self.annualization_factor) is not Decimal
                or not self.annualization_factor.is_finite()
                or self.annualization_factor <= 0
            ):
                raise ValueError(
                    "annualization_factor must be an exact "
                    "positive finite Decimal or None"
                )


def _recorded_at(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError(
            "authenticated series requires canonical UTC recorded_at"
        )

    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            "authenticated series requires canonical UTC recorded_at"
        ) from None

    if result.tzinfo != timezone.utc:
        raise ValueError(
            "authenticated series requires canonical UTC recorded_at"
        )

    return result


def _return(value: object) -> Decimal:
    if type(value) is not str:
        raise ValueError(
            "authenticated series requires canonical Decimal return"
        )

    try:
        result = D(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(
            "authenticated series requires canonical Decimal return"
        ) from None

    if not result.is_finite():
        raise ValueError(
            "authenticated series requires finite return"
        )

    return result


def _hex64(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(
            char in "0123456789abcdef"
            for char in value
        )
    )


def _nav(value: object) -> Decimal:
    if type(value) is not str:
        raise ValueError(
            "authenticated C1 NAV must be canonical Decimal string"
        )

    try:
        result = D(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(
            "authenticated C1 NAV must be canonical Decimal string"
        ) from None

    if not result.is_finite() or result < 0:
        raise ValueError(
            "authenticated C1 NAV must be finite and non-negative"
        )

    return result


def _validate_source_material(
    directory: Path,
    portfolio,
    source: dict,
) -> tuple[
    tuple[datetime, ...],
    tuple[Decimal, ...],
]:
    if type(source) is not dict:
        raise ValueError(
            "authenticated C1 series required"
        )

    expected_fields = {
        "schema_version",
        "label",
        "scoring",
        "performance_status",
        "policy_id",
        "policy_receipt_sha256",
        "currency",
        "observations",
        "intervals",
        "summary",
        "series_id",
    }

    if set(source) != expected_fields:
        raise ValueError(
            "invalid authenticated C1 series shape"
        )

    if (
        source["schema_version"]
        != "shadow-daily-portfolio-series-v1"
        or source["label"]
        != "EXPERIMENTAL / PAPER ONLY"
        or source["scoring"] != "NOT SCORED"
        or source["performance_status"]
        != "VALUATION SERIES ONLY / M7 PERFORMANCE NOT EVALUATED"
    ):
        raise ValueError(
            "invalid C1 series semantics"
        )

    series_id = source["series_id"]

    if not _hex64(series_id):
        raise ValueError(
            "invalid source series identity"
        )

    identity_basis = {
        key: value
        for key, value in source.items()
        if key != "series_id"
    }

    expected_series_id = hashlib.sha256(
        _canonical(identity_basis)
    ).hexdigest()

    if series_id != expected_series_id:
        raise ValueError(
            "source series identity mismatch"
        )

    if type(portfolio) is not ShadowPortfolioPolicy:
        raise TypeError(
            "ShadowPortfolioPolicy required"
        )

    policy_receipt = audit_portfolio_policy(
        Path(directory),
        portfolio,
    )

    if source["policy_id"] != policy_receipt["policy_id"]:
        raise ValueError(
            "source policy does not match authenticated portfolio"
        )

    expected_policy_receipt_hash = hashlib.sha256(
        _canonical(policy_receipt)
    ).hexdigest()

    if (
        source["policy_receipt_sha256"]
        != expected_policy_receipt_hash
    ):
        raise ValueError(
            "source policy receipt hash mismatch"
        )

    if source["currency"] != portfolio.base_currency:
        raise ValueError(
            "source currency does not match portfolio"
        )

    observations = source["observations"]
    intervals = source["intervals"]

    if (
        type(observations) is not list
        or type(intervals) is not list
        or len(observations) < 2
        or len(intervals) != len(observations) - 1
    ):
        raise ValueError(
            "invalid authenticated C1 series shape"
        )

    expected_observation_fields = {
        "snapshot_date_utc",
        "snapshot_id",
        "snapshot_receipt_sha256",
        "recorded_at",
        "valuation_status",
        "gross_marked_nav",
    }

    recorded_ats = []
    navs = []
    snapshot_ids = set()

    allowed_valuation_statuses = {
        "AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION",
        "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION",
    }

    for observation in observations:
        if (
            type(observation) is not dict
            or set(observation)
            != expected_observation_fields
        ):
            raise ValueError(
                "invalid authenticated C1 observation shape"
            )

        recorded_at = _recorded_at(
            observation["recorded_at"]
        )

        snapshot_date = observation[
            "snapshot_date_utc"
        ]

        if (
            type(snapshot_date) is not str
            or snapshot_date
            != recorded_at.date().isoformat()
        ):
            raise ValueError(
                "invalid observation chronology"
            )

        snapshot_id = observation["snapshot_id"]

        if (
            not _hex64(snapshot_id)
            or snapshot_id in snapshot_ids
        ):
            raise ValueError(
                "invalid authenticated snapshot identity"
            )

        if not _hex64(
            observation["snapshot_receipt_sha256"]
        ):
            raise ValueError(
                "invalid authenticated snapshot receipt hash"
            )

        if (
            observation["valuation_status"]
            not in allowed_valuation_statuses
        ):
            raise ValueError(
                "invalid authenticated observation semantics"
            )

        snapshot_ids.add(snapshot_id)
        recorded_ats.append(recorded_at)
        navs.append(
            _nav(observation["gross_marked_nav"])
        )

    recorded_ats = tuple(recorded_ats)
    navs = tuple(navs)

    if any(
        current <= previous
        for previous, current in zip(
            recorded_ats,
            recorded_ats[1:],
        )
    ):
        raise ValueError(
            "invalid observation chronology"
        )

    expected_interval_fields = {
        "start_date_utc",
        "end_date_utc",
        "elapsed_days",
        "start_snapshot_id",
        "end_snapshot_id",
        "gross_marked_return",
    }

    returns = []

    with localcontext(Context(prec=34)):
        for index, interval in enumerate(intervals):
            if (
                type(interval) is not dict
                or set(interval)
                != expected_interval_fields
            ):
                raise ValueError(
                    "invalid authenticated C1 interval shape"
                )

            start = observations[index]
            end = observations[index + 1]

            if (
                interval["start_snapshot_id"]
                != start["snapshot_id"]
                or interval["end_snapshot_id"]
                != end["snapshot_id"]
            ):
                raise ValueError(
                    "interval provenance does not match observations"
                )

            elapsed_days = (
                recorded_ats[index + 1].date()
                - recorded_ats[index].date()
            ).days

            if (
                interval["start_date_utc"]
                != start["snapshot_date_utc"]
                or interval["end_date_utc"]
                != end["snapshot_date_utc"]
                or type(interval["elapsed_days"]) is not int
                or interval["elapsed_days"]
                != elapsed_days
            ):
                raise ValueError(
                    "interval chronology does not match observations"
                )

            reported_return = _return(
                interval["gross_marked_return"]
            )

            previous_nav = navs[index]
            current_nav = navs[index + 1]

            if previous_nav <= 0:
                raise ValueError(
                    "periodic bridge requires positive previous NAV"
                )

            expected_return = (
                current_nav - previous_nav
            ) / previous_nav

            if reported_return != expected_return:
                raise ValueError(
                    "interval return does not match authenticated NAVs"
                )

            returns.append(reported_return)

    return recorded_ats, tuple(returns)


def _bridge_basis(
    source_series: dict,
    config: PeriodicReturnBridgeConfig,
    period_ends: tuple[datetime, ...],
    returns: tuple[Decimal, ...],
) -> dict:
    return {
        "schema_version": BRIDGE_SCHEMA,
        "source_series_id": source_series["series_id"],
        "source_policy_id": source_series["policy_id"],
        "source_currency": source_series["currency"],
        "period_seconds": config.period_seconds,
        "period_ends": [
            value.isoformat()
            for value in period_ends
        ],
        "returns": [
            str(value)
            for value in returns
        ],
        "risk_free_return":
            str(config.risk_free_return),
        "sortino_target":
            str(config.sortino_target),
        "minimum_samples":
            config.minimum_samples,
        "annualization_factor": (
            None
            if config.annualization_factor is None
            else str(config.annualization_factor)
        ),
    }


def authenticated_periodic_return_series(
    directory: Path,
    portfolio,
    requests,
    config: PeriodicReturnBridgeConfig,
) -> PeriodicReturnSeries:
    """Admit an authenticated C1 series only at exact fixed cadence."""
    if type(config) is not PeriodicReturnBridgeConfig:
        raise TypeError(
            "PeriodicReturnBridgeConfig required"
        )

    source = authenticated_daily_portfolio_series(
        Path(directory),
        portfolio,
        requests,
    )

    recorded_ats, returns = _validate_source_material(
        Path(directory),
        portfolio,
        source,
    )

    expected_delta = timedelta(
        seconds=config.period_seconds,
    )

    for previous, current in zip(
        recorded_ats,
        recorded_ats[1:],
    ):
        if current - previous != expected_delta:
            raise ValueError(
                "authenticated snapshots do not satisfy "
                "configured fixed-period cadence"
            )

    period_ends = recorded_ats[1:]

    with localcontext(Context(prec=34)):
        basis = _bridge_basis(
            source,
            config,
            period_ends,
            returns,
        )

        series_id = hashlib.sha256(
            _canonical(basis)
        ).hexdigest()

        return PeriodicReturnSeries(
            schema_version="periodic-return-v1",
            series_id=series_id,
            period_seconds=config.period_seconds,
            period_ends=period_ends,
            returns=returns,
            risk_free_return=config.risk_free_return,
            sortino_target=config.sortino_target,
            minimum_samples=config.minimum_samples,
            annualization_factor=(
                config.annualization_factor
            ),
        )


def attach_authenticated_periodic_returns(
    request: PerformanceAnalysisInput,
    directory: Path,
    portfolio,
    snapshot_requests,
    config: PeriodicReturnBridgeConfig,
) -> PerformanceAnalysisInput:
    """Return a new M7 request with authenticated periodic returns attached."""
    if not isinstance(
        request,
        PerformanceAnalysisInput,
    ):
        raise TypeError(
            "PerformanceAnalysisInput required"
        )

    if request.periodic_returns is not None:
        raise ValueError(
            "performance request already contains periodic returns"
        )

    periodic = authenticated_periodic_return_series(
        Path(directory),
        portfolio,
        snapshot_requests,
        config,
    )

    values = {
        name: getattr(request, name)
        for name in PerformanceAnalysisInput.model_fields
    }

    values["periodic_returns"] = periodic

    return PerformanceAnalysisInput(**values)
