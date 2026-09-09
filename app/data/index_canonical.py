from __future__ import annotations

from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)
from enum import StrEnum
from typing import (
    Any,
    Mapping,
)

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from app.data.models import (
    DataModel,
)


class IndexCanonicalizationError(
    ValueError
):
    pass


class IndexBarSemanticClass(
    StrEnum
):
    FULL_OHLC_VALID = (
        "FULL_OHLC_VALID"
    )

    LEGACY_CLOSE_REFERENCE = (
        "LEGACY_CLOSE_REFERENCE"
    )

    QUARANTINED_ANOMALY = (
        "QUARANTINED_ANOMALY"
    )


class CanonicalIndexDailyBar(
    DataModel
):
    """
    Canonical daily index observation.

    Important:

    FULL_OHLC_VALID
        open/high/low/close may be used
        as validated OHLC values.

    LEGACY_CLOSE_REFERENCE
        close is retained.
        source indexOpen is stored only as
        reference_level and MUST NOT be
        treated as executable open.
        high/low remain unavailable.

    QUARANTINED_ANOMALY
        close is preserved for audit only.
        canonical OHLC fields are withheld.
        Consumers must not use the row for
        model features until separately
        resolved.
    """

    index_name: str = Field(
        min_length=1,
        max_length=64,
    )

    market_date: date

    semantic_class: (
        IndexBarSemanticClass
    )

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

    close: Decimal = Field(
        gt=0,
    )

    reference_level: (
        Decimal | None
    ) = Field(
        default=None,
        gt=0,
    )

    change: Decimal | None = None

    change_pct: (
        Decimal | None
    ) = None

    quality_flags: tuple[
        str,
        ...,
    ] = ()

    source_provider: str = Field(
        min_length=1,
        max_length=64,
    )

    source_snapshot_date: date

    source_page_number: int = Field(
        ge=1,
    )

    source_row_number: int = Field(
        ge=1,
    )

    source_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "index_name"
    )
    @classmethod
    def normalize_index_name(
        cls,
        value: str,
    ) -> str:
        return (
            value.strip().upper()
        )

    @field_validator(
        "source_provider"
    )
    @classmethod
    def normalize_provider(
        cls,
        value: str,
    ) -> str:
        return (
            value.strip().lower()
        )

    @model_validator(
        mode="after"
    )
    def validate_semantics(
        self,
    ) -> "CanonicalIndexDailyBar":
        if (
            self.semantic_class
            == IndexBarSemanticClass
            .FULL_OHLC_VALID
        ):
            if any(
                value is None
                for value in (
                    self.open,
                    self.high,
                    self.low,
                )
            ):
                raise ValueError(
                    "FULL_OHLC_VALID requires "
                    "open/high/low"
                )

            assert self.open is not None
            assert self.high is not None
            assert self.low is not None

            if self.reference_level is not None:
                raise ValueError(
                    "FULL_OHLC_VALID cannot "
                    "have reference_level"
                )

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
                    "open must be inside "
                    "low/high range"
                )

            if not (
                self.low
                <= self.close
                <= self.high
            ):
                raise ValueError(
                    "close must be inside "
                    "low/high range"
                )

        elif (
            self.semantic_class
            == IndexBarSemanticClass
            .LEGACY_CLOSE_REFERENCE
        ):
            if any(
                value is not None
                for value in (
                    self.open,
                    self.high,
                    self.low,
                )
            ):
                raise ValueError(
                    "legacy reference rows "
                    "cannot expose canonical "
                    "OHLC fields"
                )

            if self.reference_level is None:
                raise ValueError(
                    "legacy reference rows "
                    "require reference_level"
                )

        elif (
            self.semantic_class
            == IndexBarSemanticClass
            .QUARANTINED_ANOMALY
        ):
            if any(
                value is not None
                for value in (
                    self.open,
                    self.high,
                    self.low,
                    self.reference_level,
                )
            ):
                raise ValueError(
                    "quarantined rows cannot "
                    "expose canonical OHLC "
                    "or reference_level"
                )

            if not self.quality_flags:
                raise ValueError(
                    "quarantined rows require "
                    "quality flags"
                )

        return self

    @property
    def usable_for_full_ohlc(
        self,
    ) -> bool:
        return (
            self.semantic_class
            == IndexBarSemanticClass
            .FULL_OHLC_VALID
        )

    @property
    def usable_for_close_history(
        self,
    ) -> bool:
        return self.semantic_class in {
            IndexBarSemanticClass
            .FULL_OHLC_VALID,
            IndexBarSemanticClass
            .LEGACY_CLOSE_REFERENCE,
        }


def _decimal(
    row: Mapping[
        str,
        Any,
    ],
    field: str,
) -> Decimal:
    if field not in row:
        raise (
            IndexCanonicalizationError(
                f"missing required field: "
                f"{field}"
            )
        )

    value = row[field]

    if value is None:
        raise (
            IndexCanonicalizationError(
                f"null required field: "
                f"{field}"
            )
        )

    try:
        result = Decimal(
            str(value)
        )

    except (
        InvalidOperation,
        ValueError,
    ) as exc:
        raise (
            IndexCanonicalizationError(
                f"invalid decimal field: "
                f"{field}"
            )
        ) from exc

    if not result.is_finite():
        raise (
            IndexCanonicalizationError(
                f"non-finite decimal field: "
                f"{field}"
            )
        )

    return result


def _optional_decimal(
    row: Mapping[
        str,
        Any,
    ],
    field: str,
) -> Decimal | None:
    value = row.get(field)

    if value is None:
        return None

    try:
        result = Decimal(
            str(value)
        )

    except (
        InvalidOperation,
        ValueError,
    ) as exc:
        raise (
            IndexCanonicalizationError(
                f"invalid decimal field: "
                f"{field}"
            )
        ) from exc

    if not result.is_finite():
        raise (
            IndexCanonicalizationError(
                f"non-finite decimal field: "
                f"{field}"
            )
        )

    return result


def _market_date(
    row: Mapping[
        str,
        Any,
    ],
) -> date:
    value = row.get(
        "indexDay"
    )

    if value is None:
        raise (
            IndexCanonicalizationError(
                "missing required field: "
                "indexDay"
            )
        )

    text = str(value)

    try:
        return date.fromisoformat(
            text[:10]
        )

    except ValueError as exc:
        raise (
            IndexCanonicalizationError(
                "invalid indexDay"
            )
        ) from exc


def classify_index_row(
    row: Mapping[
        str,
        Any,
    ],
) -> IndexBarSemanticClass:
    opening = _decimal(
        row,
        "indexOpen",
    )

    high = _decimal(
        row,
        "high",
    )

    low = _decimal(
        row,
        "low",
    )

    close = _decimal(
        row,
        "indexClose",
    )

    if close <= 0:
        raise (
            IndexCanonicalizationError(
                "indexClose must be "
                "positive"
            )
        )

    if (
        high == 0
        and low == 0
        and opening > 0
    ):
        return (
            IndexBarSemanticClass
            .LEGACY_CLOSE_REFERENCE
        )

    if (
        opening > 0
        and high > 0
        and low > 0
        and high >= low
        and low
        <= opening
        <= high
        and low
        <= close
        <= high
    ):
        return (
            IndexBarSemanticClass
            .FULL_OHLC_VALID
        )

    return (
        IndexBarSemanticClass
        .QUARANTINED_ANOMALY
    )


def _quality_flags(
    row: Mapping[
        str,
        Any,
    ],
    semantic_class: (
        IndexBarSemanticClass
    ),
) -> tuple[
    str,
    ...,
]:
    opening = _decimal(
        row,
        "indexOpen",
    )

    high = _decimal(
        row,
        "high",
    )

    low = _decimal(
        row,
        "low",
    )

    close = _decimal(
        row,
        "indexClose",
    )

    if (
        semantic_class
        == IndexBarSemanticClass
        .FULL_OHLC_VALID
    ):
        return ()

    if (
        semantic_class
        == IndexBarSemanticClass
        .LEGACY_CLOSE_REFERENCE
    ):
        return (
            "HIGH_LOW_UNAVAILABLE",
            "SOURCE_OPEN_NOT_APPROVED_AS_EXECUTABLE",
        )

    flags: list[str] = []

    if opening <= 0:
        flags.append(
            "NONPOSITIVE_OPEN"
        )

    if high <= 0:
        flags.append(
            "NONPOSITIVE_HIGH"
        )

    if low <= 0:
        flags.append(
            "NONPOSITIVE_LOW"
        )

    if high < low:
        flags.append(
            "HIGH_BELOW_LOW"
        )

    if (
        high >= low
        and high > 0
        and low > 0
    ):
        if not (
            low
            <= opening
            <= high
        ):
            flags.append(
                "OPEN_OUTSIDE_RANGE"
            )

        if not (
            low
            <= close
            <= high
        ):
            flags.append(
                "CLOSE_OUTSIDE_RANGE"
            )

    if not flags:
        flags.append(
            "UNCLASSIFIED_OHLC_ANOMALY"
        )

    return tuple(flags)


def canonicalize_index_row(
    row: Mapping[
        str,
        Any,
    ],
    *,
    index_name: str,
    source_provider: str,
    source_snapshot_date: date,
    source_page_number: int,
    source_row_number: int,
    source_sha256: str,
) -> CanonicalIndexDailyBar:
    semantic_class = (
        classify_index_row(
            row
        )
    )

    opening = _decimal(
        row,
        "indexOpen",
    )

    high = _decimal(
        row,
        "high",
    )

    low = _decimal(
        row,
        "low",
    )

    close = _decimal(
        row,
        "indexClose",
    )

    flags = _quality_flags(
        row,
        semantic_class,
    )

    canonical_open = None
    canonical_high = None
    canonical_low = None
    reference_level = None

    if (
        semantic_class
        == IndexBarSemanticClass
        .FULL_OHLC_VALID
    ):
        canonical_open = opening
        canonical_high = high
        canonical_low = low

    elif (
        semantic_class
        == IndexBarSemanticClass
        .LEGACY_CLOSE_REFERENCE
    ):
        reference_level = opening

    return CanonicalIndexDailyBar(
        index_name=index_name,
        market_date=_market_date(
            row
        ),
        semantic_class=(
            semantic_class
        ),
        open=canonical_open,
        high=canonical_high,
        low=canonical_low,
        close=close,
        reference_level=(
            reference_level
        ),
        change=_optional_decimal(
            row,
            "change",
        ),
        change_pct=(
            _optional_decimal(
                row,
                "changePer",
            )
        ),
        quality_flags=flags,
        source_provider=(
            source_provider
        ),
        source_snapshot_date=(
            source_snapshot_date
        ),
        source_page_number=(
            source_page_number
        ),
        source_row_number=(
            source_row_number
        ),
        source_sha256=(
            source_sha256
        ),
    )
