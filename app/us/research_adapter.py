"""US6 research-only adapter from US5B PIT truth to shared Swing math.

No execution, broker, provider, network, database, or M6 simulation occurs
here. Stable US instrument identity is deliberately preserved instead of
collapsing the result to ticker identity.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from app.strategies.contracts import Contract
from app.strategies.eod import (
    SwingConfig,
    evaluate_swing_series,
)
from app.us.contracts import US_MARKET_TIMEZONE
from app.us.historical_daily import USHistoricalDailyBar
from app.us.retrospective_pit import (
    US_PIT_TRANSFORMATION,
    USRetrospectivePITDailyDataset,
    USSplitAdjustedObservation,
)


class USSwingResearchResult(Contract):
    """Non-executable US daily research signal with stable identity binding."""

    schema_version: Literal[
        "us-swing-research-result-v1"
    ] = "us-swing-research-result-v1"

    strategy_id: Literal["SWING"] = "SWING"
    strategy_version: Literal["1"] = "1"

    instrument_id: UUID

    canonical_symbol: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    listing_mic: str = Field(
        pattern=r"^[A-Z0-9]{4}$",
    )

    signal_date: date
    decision_at: datetime

    source_dataset_id: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    config_id: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    state: Literal[
        "WATCH",
        "NO_CONFIRMATION",
    ]

    planning_close_reference: Decimal | None = Field(
        default=None,
        gt=0,
    )

    fast_ema: float
    slow_ema: float

    breakout_reference: float | None = Field(
        default=None,
        gt=0,
    )

    price_basis: Literal[
        "RAW_CLOSE_REFERENCE_SPLIT_ADJUSTED_INDICATORS"
    ] = "RAW_CLOSE_REFERENCE_SPLIT_ADJUSTED_INDICATORS"

    validation_status: Literal[
        "UNVALIDATED"
    ] = "UNVALIDATED"

    execution_allowed: Literal[False] = False

    @field_validator(
        "signal_date",
        mode="before",
    )
    @classmethod
    def exact_date(cls, value):
        if type(value) is not date:
            raise ValueError(
                "signal_date must be exact date"
            )
        return value

    @field_validator(
        "decision_at",
        mode="before",
    )
    @classmethod
    def exact_utc(cls, value):
        if (
            type(value) is not datetime
            or value.tzinfo is not timezone.utc
        ):
            raise ValueError(
                "decision_at must use datetime.timezone.utc"
            )

        return value

    @model_validator(mode="after")
    def state_reference_consistency(self):
        if (
            self.state == "WATCH"
            and self.planning_close_reference is None
        ):
            raise ValueError(
                "WATCH requires raw planning close reference"
            )

        if (
            self.state != "WATCH"
            and self.planning_close_reference is not None
        ):
            raise ValueError(
                "non-WATCH result cannot carry "
                "planning close reference"
            )

        return self


def _canonical_config(
    value: SwingConfig,
) -> SwingConfig:
    # US boundary is deliberately stricter than the legacy EGX wrapper.
    if type(value) is not SwingConfig:
        raise ValueError(
            "exact SwingConfig required"
        )

    return SwingConfig.model_validate(
        value.model_dump(mode="python"),
        strict=True,
    )


def _validate_dataset(
    dataset: USRetrospectivePITDailyDataset,
    *,
    config: SwingConfig,
) -> None:
    if type(dataset) is not USRetrospectivePITDailyDataset:
        raise ValueError(
            "exact USRetrospectivePITDailyDataset required"
        )

    if dataset.dq_status != "VALIDATED":
        raise ValueError(
            "validated US5B dataset required"
        )

    if (
        dataset.transformation
        != US_PIT_TRANSFORMATION
    ):
        raise ValueError(
            "canonical US5B split transformation required"
        )

    if type(dataset.instrument_id) is not UUID:
        raise ValueError(
            "exact dataset instrument_id required"
        )

    if (
        type(dataset.coverage_start) is not date
        or type(dataset.coverage_end) is not date
    ):
        raise ValueError(
            "exact dataset coverage dates required"
        )

    if (
        type(dataset.decision_at) is not datetime
        or dataset.decision_at.tzinfo
        is not timezone.utc
    ):
        raise ValueError(
            "dataset decision_at must use datetime.timezone.utc"
        )

    decision_market_date = (
        dataset.decision_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    # US6 baseline evaluates one same-market-date daily decision horizon.
    # Stale/later decisions need an explicit session-aware contract.
    if (
        decision_market_date
        != dataset.coverage_end
    ):
        raise ValueError(
            "US Swing decision horizon must equal "
            "coverage_end market date"
        )

    if (
        len(dataset.rows)
        != len(dataset.split_adjusted)
    ):
        raise ValueError(
            "raw and indicator series length mismatch"
        )

    dates = tuple(
        row.market_date
        for row in dataset.rows
    )

    if (
        dates != tuple(sorted(dates))
        or len(set(dates)) != len(dates)
    ):
        raise ValueError(
            "strictly ordered unique raw market dates required"
        )

    if (
        not dates
        or dates[-1] != dataset.coverage_end
    ):
        raise ValueError(
            "coverage_end lacks eligible executable observation"
        )

    if (
        len(dataset.rows)
        < config.minimum_history
    ):
        raise ValueError(
            "validated minimum history required"
        )

    for raw, adjusted in zip(
        dataset.rows,
        dataset.split_adjusted,
    ):
        if type(raw) is not USHistoricalDailyBar:
            raise ValueError(
                "canonical USHistoricalDailyBar required"
            )

        if type(adjusted) is not USSplitAdjustedObservation:
            raise ValueError(
                "canonical USSplitAdjustedObservation required"
            )

        if (
            raw.market_date
            != adjusted.market_date
        ):
            raise ValueError(
                "raw/indicator market_date mismatch"
            )

        if (
            raw.instrument_id
            != dataset.instrument_id
            or adjusted.instrument_id
            != dataset.instrument_id
        ):
            raise ValueError(
                "raw/indicator instrument_id mismatch"
            )

        if (
            raw.canonical_symbol
            != adjusted.canonical_symbol
        ):
            raise ValueError(
                "raw/indicator symbol mismatch"
            )

        if (
            raw.calendar_mic
            != dataset.calendar_mic
            or adjusted.listing_mic
            != dataset.calendar_mic
        ):
            raise ValueError(
                "raw/indicator MIC mismatch"
            )

        prices = (
            adjusted.open,
            adjusted.high,
            adjusted.low,
            adjusted.close,
        )

        if any(
            type(value) is not Decimal
            or not value.is_finite()
            or value <= 0
            for value in prices
        ):
            raise ValueError(
                "positive finite adjusted prices required"
            )

        if (
            type(adjusted.volume) is not Decimal
            or not adjusted.volume.is_finite()
            or adjusted.volume < 0
        ):
            raise ValueError(
                "finite nonnegative adjusted volume required"
            )

        if (
            adjusted.high < adjusted.low
            or not (
                adjusted.low
                <= adjusted.open
                <= adjusted.high
            )
            or not (
                adjusted.low
                <= adjusted.close
                <= adjusted.high
            )
        ):
            raise ValueError(
                "invalid adjusted OHLC geometry"
            )


def evaluate_us_swing(
    dataset: USRetrospectivePITDailyDataset,
    config: SwingConfig,
) -> USSwingResearchResult:
    """Evaluate shared Swing math over one canonical US5B PIT dataset."""

    config = _canonical_config(config)

    _validate_dataset(
        dataset,
        config=config,
    )

    evaluation = evaluate_swing_series(
        closes=tuple(
            float(row.close)
            for row in dataset.split_adjusted
        ),
        highs=tuple(
            float(row.high)
            for row in dataset.split_adjusted
        ),
        config=config,
    )

    raw = dataset.rows[-1]

    return USSwingResearchResult(
        instrument_id=dataset.instrument_id,
        canonical_symbol=raw.canonical_symbol,
        listing_mic=dataset.calendar_mic,
        signal_date=raw.market_date,
        decision_at=dataset.decision_at,
        source_dataset_id=dataset.identity,
        config_id=config.identity,
        state=evaluation.state,
        planning_close_reference=(
            raw.close
            if evaluation.state == "WATCH"
            else None
        ),
        fast_ema=evaluation.fast_ema,
        slow_ema=evaluation.slow_ema,
        breakout_reference=(
            evaluation.breakout_reference
        ),
    )


__all__ = [
    "USSwingResearchResult",
    "evaluate_us_swing",
]
