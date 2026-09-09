from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from app.data.models import DataModel


DAILY_SEMANTIC_CONTRACT_VERSION = (
    "egx-daily-semantic-v1"
)

DAILY_SERIALIZATION_FORMAT = (
    "canonical-json-v1"
)


class DailyCanonicalizationError(
    ValueError
):
    pass


class DailyBarSemanticClass(
    StrEnum
):
    VALID_EXECUTABLE = (
        "VALID_EXECUTABLE"
    )

    QUARANTINED_ANOMALY = (
        "QUARANTINED_ANOMALY"
    )


class CanonicalDailyBar(
    DataModel
):
    """
    Canonical equity daily observation.

    VALID_EXECUTABLE:
        OHLCV may be consumed by execution,
        indicators, and backtests.

    QUARANTINED_ANOMALY:
        executable OHLCV is withheld.
        Original provider bytes remain in
        immutable raw storage.

    provider_adjusted_close_reference is
    audit/reference data only and MUST NOT
    be used as an execution price.
    """

    instrument_id: UUID

    canonical_symbol: str = Field(
        min_length=1,
        max_length=64,
    )

    provider_symbol: str = Field(
        min_length=1,
        max_length=64,
    )

    market_date: date

    semantic_class: DailyBarSemanticClass

    open: Decimal | None = Field(
        default=None,
        gt=0,
    )

    high: Decimal | None = Field(
        default=None,
        gt=0,
    )

    low: Decimal | None = Field(
        default=None,
        gt=0,
    )

    close: Decimal | None = Field(
        default=None,
        gt=0,
    )

    volume: Decimal | None = Field(
        default=None,
        ge=0,
    )

    provider_adjusted_close_reference: (
        Decimal | None
    ) = Field(
        default=None,
        gt=0,
    )

    quality_flags: tuple[
        str,
        ...,
    ] = ()

    source_provider: str = Field(
        min_length=1,
        max_length=64,
    )

    source_snapshot_date: date

    source_row_number: int = Field(
        ge=1,
    )

    source_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "canonical_symbol",
        "provider_symbol",
    )
    @classmethod
    def normalize_symbol(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()

    @field_validator(
        "source_provider"
    )
    @classmethod
    def normalize_provider(
        cls,
        value: str,
    ) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_semantics(
        self,
    ) -> "CanonicalDailyBar":
        executable = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )

        if (
            self.semantic_class
            == DailyBarSemanticClass
            .VALID_EXECUTABLE
        ):
            if any(
                value is None
                for value in executable
            ):
                raise ValueError(
                    "VALID_EXECUTABLE requires "
                    "complete OHLCV"
                )

            assert self.open is not None
            assert self.high is not None
            assert self.low is not None
            assert self.close is not None

            if self.high < self.low:
                raise ValueError(
                    "high cannot be below low"
                )

            if not (
                self.low
                <= self.open
                <= self.high
            ):
                raise ValueError(
                    "open outside high-low range"
                )

            if not (
                self.low
                <= self.close
                <= self.high
            ):
                raise ValueError(
                    "close outside high-low range"
                )

        else:
            if any(
                value is not None
                for value in executable
            ):
                raise ValueError(
                    "QUARANTINED_ANOMALY must "
                    "withhold executable OHLCV"
                )

        return self


from typing import Any, Mapping


def _required_decimal(
    row: Mapping[str, Any],
    field: str,
) -> Decimal:
    if field not in row:
        raise DailyCanonicalizationError(
            f"missing daily field: {field}"
        )

    value = row[field]

    if isinstance(value, bool):
        raise DailyCanonicalizationError(
            f"invalid daily field: {field}"
        )

    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise DailyCanonicalizationError(
            f"invalid daily field: {field}"
        ) from exc

    if not result.is_finite():
        raise DailyCanonicalizationError(
            f"non-finite daily field: {field}"
        )

    return result


def canonicalize_daily_row(
    row: Mapping[str, Any],
    *,
    instrument_id: UUID,
    canonical_symbol: str,
    provider_symbol: str,
    source_provider: str,
    source_snapshot_date: date,
    source_row_number: int,
    source_sha256: str,
) -> CanonicalDailyBar:
    try:
        market_date = date.fromisoformat(
            str(row["date"])
        )
    except (KeyError, ValueError) as exc:
        raise DailyCanonicalizationError(
            "invalid daily market date"
        ) from exc

    opening = _required_decimal(row, "open")
    high = _required_decimal(row, "high")
    low = _required_decimal(row, "low")
    close = _required_decimal(row, "close")
    volume = _required_decimal(row, "volume")
    adjusted = _required_decimal(
        row,
        "adjusted_close",
    )

    for name, value in (
        ("open", opening),
        ("high", high),
        ("low", low),
        ("close", close),
        ("adjusted_close", adjusted),
    ):
        if value <= 0:
            raise DailyCanonicalizationError(
                f"non-positive daily field: {name}"
            )

    if volume < 0:
        raise DailyCanonicalizationError(
            "negative daily field: volume"
        )

    flags: list[str] = []

    if high < low:
        flags.append("HIGH_BELOW_LOW")

    if not low <= opening <= high:
        flags.append("OPEN_OUTSIDE_RANGE")

    if not low <= close <= high:
        flags.append("CLOSE_OUTSIDE_RANGE")

    semantic_class = (
        DailyBarSemanticClass.VALID_EXECUTABLE
        if not flags
        else DailyBarSemanticClass
        .QUARANTINED_ANOMALY
    )

    executable = (
        {
            "open": opening,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        if not flags
        else {}
    )

    return CanonicalDailyBar(
        instrument_id=instrument_id,
        canonical_symbol=canonical_symbol,
        provider_symbol=provider_symbol,
        market_date=market_date,
        semantic_class=semantic_class,
        provider_adjusted_close_reference=adjusted,
        quality_flags=tuple(flags),
        source_provider=source_provider,
        source_snapshot_date=source_snapshot_date,
        source_row_number=source_row_number,
        source_sha256=source_sha256,
        **executable,
    )


def serialize_daily_rows(
    rows,
) -> bytes:
    import json

    materialized = list(rows)

    if not materialized:
        raise ValueError(
            "canonical daily rows cannot be empty"
        )

    ordered = sorted(
        materialized,
        key=lambda row: row.market_date,
    )

    dates = [
        row.market_date
        for row in ordered
    ]

    if len(dates) != len(set(dates)):
        raise ValueError(
            "canonical daily rows contain "
            "duplicate market_date values"
        )

    payload = [
        row.model_dump(mode="json")
        for row in ordered
    ]

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
