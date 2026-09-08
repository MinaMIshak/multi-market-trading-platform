from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"


KNOWN_EGID_INDEX_CODES = {
    "EGX30",
    "EGX100 EWI",
    "EGX30CAPPED",
    "EGX35-LV",
    "SHARIAH",
    "TAMAYUZ",
    "EGX70 EWI",
}


class CanonicalInstrument(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    instrument_id: UUID

    instrument_type: InstrumentType

    canonical_ticker: str = Field(
        min_length=1,
        max_length=64,
    )

    name_en: str | None = None
    name_ar: str | None = None

    short_name_en: str | None = None
    short_name_ar: str | None = None

    source_provider: str
    source_symbol_code: str

    reuters_raw: str | None = None
    reuters_normalized: str | None = None

    source_sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    source_market_date: str | None = None

    normalization_notes: list[str] = Field(
        default_factory=list
    )


def _clean(
    value: Any,
) -> str | None:
    if value is None:
        return None

    result = str(value).strip()

    return result or None


def normalize_reuters(
    value: str | None,
) -> tuple[
    str | None,
    list[str],
]:
    if value is None:
        return None, []

    normalized = value.strip().upper()

    if not normalized:
        return None, []

    notes: list[str] = []

    if normalized.endswith("."):
        normalized = normalized.rstrip(".")

        notes.append(
            "REUTERS_TRAILING_DOT_REMOVED"
        )

    return normalized, notes


def ticker_from_reuters(
    value: str,
) -> str | None:
    value = value.strip().upper()

    if value.endswith(".CA"):
        ticker = value[:-3].strip()

        return ticker or None

    return None


def build_canonical_security_master(
    *,
    records: list[dict[str, Any]],
    source_sha256: str,
    source_market_date: str | None,
) -> list[CanonicalInstrument]:
    instruments: list[
        CanonicalInstrument
    ] = []

    seen_symbol_codes: set[str] = set()
    seen_tickers: dict[str, str] = {}

    for index, record in enumerate(
        records
    ):
        symbol_code = _clean(
            record.get(
                "SYMBOL_CODE"
            )
        )

        if symbol_code is None:
            raise ValueError(
                f"record {index} "
                "has no SYMBOL_CODE"
            )

        symbol_code = symbol_code.upper()

        if symbol_code in seen_symbol_codes:
            raise ValueError(
                "duplicate SYMBOL_CODE: "
                + symbol_code
            )

        seen_symbol_codes.add(
            symbol_code
        )

        reuters_raw = _clean(
            record.get(
                "Reuters"
            )
        )

        (
            reuters_normalized,
            normalization_notes,
        ) = normalize_reuters(
            reuters_raw
        )

        if (
            symbol_code
            in KNOWN_EGID_INDEX_CODES
        ):
            instrument_type = (
                InstrumentType.INDEX
            )

            canonical_ticker = (
                symbol_code
            )

        elif reuters_normalized:
            ticker = ticker_from_reuters(
                reuters_normalized
            )

            if ticker is None:
                instrument_type = (
                    InstrumentType.UNKNOWN
                )

                canonical_ticker = (
                    symbol_code
                )

                normalization_notes.append(
                    "UNSUPPORTED_REUTERS_FORMAT"
                )

            else:
                instrument_type = (
                    InstrumentType.EQUITY
                )

                canonical_ticker = ticker

        else:
            instrument_type = (
                InstrumentType.UNKNOWN
            )

            canonical_ticker = (
                symbol_code
            )

            normalization_notes.append(
                "MISSING_REUTERS_IDENTIFIER"
            )

        canonical_ticker = (
            canonical_ticker
            .strip()
            .upper()
        )

        existing_symbol = (
            seen_tickers.get(
                canonical_ticker
            )
        )

        if (
            existing_symbol is not None
            and existing_symbol
            != symbol_code
        ):
            raise ValueError(
                "canonical ticker collision: "
                f"{canonical_ticker} "
                f"maps to "
                f"{existing_symbol} "
                f"and {symbol_code}"
            )

        seen_tickers[
            canonical_ticker
        ] = symbol_code

        instrument_id = uuid5(
            NAMESPACE_URL,
            (
                "egx://instrument/egid/"
                + symbol_code
            ),
        )

        instruments.append(
            CanonicalInstrument(
                instrument_id=(
                    instrument_id
                ),
                instrument_type=(
                    instrument_type
                ),
                canonical_ticker=(
                    canonical_ticker
                ),
                name_en=_clean(
                    record.get(
                        "ENG_NAME"
                    )
                ),
                name_ar=_clean(
                    record.get(
                        "ARB_NAME"
                    )
                ),
                short_name_en=_clean(
                    record.get(
                        "eng_shortname"
                    )
                ),
                short_name_ar=_clean(
                    record.get(
                        "arb_shortname"
                    )
                ),
                source_provider="egid",
                source_symbol_code=(
                    symbol_code
                ),
                reuters_raw=(
                    reuters_raw
                ),
                reuters_normalized=(
                    reuters_normalized
                ),
                source_sha256=(
                    source_sha256
                ),
                source_market_date=(
                    source_market_date
                ),
                normalization_notes=(
                    normalization_notes
                ),
            )
        )

    return instruments
