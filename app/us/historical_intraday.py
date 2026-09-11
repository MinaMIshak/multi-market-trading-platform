"""US7B retrospective raw intraday execution-truth admission.

This module admits historical raw, unadjusted US intraday observations.
It does not create shared IntradayBar objects, TradePlan/RiskDecision
objects, paper simulations, fills, positions, or performance results.

`evidence_cutoff_at` is deliberately distinct from a strategy decision
timestamp. Execution observations may occur after the strategy decision.
Their historical per-bar availability remains explicit in
`available_at_utc`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.data.models import BarGranularity
from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.contracts import (
    USListingIdentity,
    USSessionState,
    US_MARKET_TIMEZONE,
)
from app.us.historical_identity import (
    AdmittedUSListingHistory,
    admit_us_listing_history,
)
from app.us.historical_session import (
    AdmittedUSSessionHistory,
    admit_us_session_history,
    resolve_us_session_on_date,
)


_SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9._-]{0,63}$"
_MIC_RE = re.compile(r"^[A-Z0-9]{4}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

_GRANULARITY_WIDTH = {
    BarGranularity.M1: timedelta(minutes=1),
    BarGranularity.M5: timedelta(minutes=5),
}


class USIntradayCoverageMode(StrEnum):
    FULL_SESSION = "FULL_SESSION"
    BOUNDED_WINDOW = "BOUNDED_WINDOW"


US_INTRADAY_EVIDENCE_FIELDS = frozenset(
    {
        "instrument_id",
        "market_date",
        "calendar_mic",
        "canonical_symbol",
        "provider_symbol",
        "source_instrument_key",
        "currency",
        "price_basis",
        "granularity",
        "session_sequence",
        "interval_start_utc",
        "interval_end_utc",
        "available_at_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "traded_value",
        "source_provider",
        "source_row_number",
        "source_sha256",
        "provenance_id",
        "coverage_start_at_utc",
        "coverage_end_at_utc",
        "first_session_sequence",
        "last_session_sequence",
        "complete",
    }
)


def _require_exact_utc(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is not timezone.utc
    ):
        raise ValueError(
            f"{field_name} must use datetime.timezone.utc"
        )
    return value


def _require_exact_date(
    value: date,
    *,
    field_name: str,
) -> date:
    if type(value) is not date:
        raise ValueError(
            f"{field_name} must be exact date"
        )
    return value


def _require_exact_uuid(
    value: UUID,
    *,
    field_name: str,
) -> UUID:
    if type(value) is not UUID:
        raise ValueError(
            f"{field_name} must be exact UUID"
        )
    return value


def _require_calendar_mic(
    value: str,
) -> str:
    if (
        type(value) is not str
        or _MIC_RE.fullmatch(value) is None
    ):
        raise ValueError(
            "calendar_mic must be canonical four-character MIC"
        )
    return value


def _require_granularity(
    value: BarGranularity,
) -> BarGranularity:
    if (
        type(value) is not BarGranularity
        or value not in _GRANULARITY_WIDTH
    ):
        raise ValueError(
            "exact M1 or M5 BarGranularity required"
        )
    return value


def _require_coverage_mode(
    value: USIntradayCoverageMode,
) -> USIntradayCoverageMode:
    if type(value) is not USIntradayCoverageMode:
        raise ValueError(
            "exact USIntradayCoverageMode required"
        )
    return value


def _finite_decimal(
    value: Decimal,
    *,
    field_name: str,
) -> Decimal:
    if (
        type(value) is not Decimal
        or not value.is_finite()
    ):
        raise ValueError(
            f"{field_name} must be finite exact Decimal"
        )
    return value


def _require_package_exact_utc(
    package: HistoricalEvidencePackage,
) -> None:
    _require_exact_utc(
        package.raw_receipt.local_received_at,
        field_name="raw_receipt.local_received_at",
    )

    for index, attachment in enumerate(
        package.attachments
    ):
        _require_exact_utc(
            attachment.local_received_at,
            field_name=(
                f"attachments[{index}].local_received_at"
            ),
        )

    _require_exact_utc(
        package.review.reviewed_at,
        field_name="review.reviewed_at",
    )

    availability = package.evidence.availability

    if availability.kind == "EXACT":
        assert availability.exact_at is not None
        _require_exact_utc(
            availability.exact_at,
            field_name="availability.exact_at",
        )
        return

    assert availability.start is not None
    assert availability.end is not None

    _require_exact_utc(
        availability.start,
        field_name="availability.start",
    )
    _require_exact_utc(
        availability.end,
        field_name="availability.end",
    )


class USHistoricalIntradayBar(BaseModel):
    """One raw unadjusted historical US intraday OHLCV interval."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    contract: Literal[
        "us-historical-intraday-bar-v1"
    ] = "us-historical-intraday-bar-v1"

    market: Literal["US"] = "US"

    instrument_id: UUID
    market_date: date

    calendar_mic: str = Field(
        pattern=r"^[A-Z0-9]{4}$",
    )
    canonical_symbol: str = Field(
        pattern=_SYMBOL_PATTERN,
    )

    provider_symbol: str = Field(
        min_length=1,
        max_length=128,
    )
    source_instrument_key: str = Field(
        min_length=1,
        max_length=256,
    )

    currency: Literal["USD"] = "USD"
    price_basis: Literal[
        "RAW_UNADJUSTED"
    ] = "RAW_UNADJUSTED"

    granularity: BarGranularity
    session_sequence: int = Field(ge=1)

    interval_start_utc: datetime
    interval_end_utc: datetime
    available_at_utc: datetime

    open: Decimal = Field(
        gt=0,
        allow_inf_nan=False,
    )
    high: Decimal = Field(
        gt=0,
        allow_inf_nan=False,
    )
    low: Decimal = Field(
        gt=0,
        allow_inf_nan=False,
    )
    close: Decimal = Field(
        gt=0,
        allow_inf_nan=False,
    )

    volume: Decimal = Field(
        ge=0,
        allow_inf_nan=False,
    )
    traded_value: Decimal | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )

    source_provider: str = Field(
        min_length=1,
        max_length=128,
    )
    source_row_number: int = Field(ge=1)
    source_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    provenance_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    @field_validator(
        "instrument_id",
        mode="before",
    )
    @classmethod
    def exact_uuid(cls, value):
        return _require_exact_uuid(
            value,
            field_name="instrument_id",
        )

    @field_validator(
        "market_date",
        mode="before",
    )
    @classmethod
    def exact_date(cls, value):
        return _require_exact_date(
            value,
            field_name="market_date",
        )

    @field_validator(
        "granularity",
        mode="before",
    )
    @classmethod
    def exact_granularity(cls, value):
        return _require_granularity(value)

    @field_validator(
        "session_sequence",
        "source_row_number",
        mode="before",
    )
    @classmethod
    def exact_positive_int(cls, value, info):
        if (
            type(value) is not int
            or value < 1
        ):
            raise ValueError(
                f"{info.field_name} must be exact positive int"
            )
        return value

    @field_validator(
        "interval_start_utc",
        "interval_end_utc",
        "available_at_utc",
        mode="before",
    )
    @classmethod
    def exact_utc(cls, value, info):
        return _require_exact_utc(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "open",
        "high",
        "low",
        "close",
        "volume",
        "traded_value",
        mode="before",
    )
    @classmethod
    def exact_decimal(cls, value, info):
        if value is None:
            return None

        return _finite_decimal(
            value,
            field_name=info.field_name,
        )

    @model_validator(mode="after")
    def consistency(
        self,
    ) -> "USHistoricalIntradayBar":
        width = _GRANULARITY_WIDTH[
            self.granularity
        ]

        if (
            self.interval_end_utc
            - self.interval_start_utc
            != width
        ):
            raise ValueError(
                "intraday interval width does not match granularity"
            )

        if (
            self.interval_end_utc
            > self.available_at_utc
        ):
            raise ValueError(
                "intraday bar available before interval end"
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

        if self.traded_value is not None:
            if (
                (self.volume == 0)
                != (self.traded_value == 0)
            ):
                raise ValueError(
                    "inconsistent volume/traded_value"
                )

        market_tz = ZoneInfo(
            US_MARKET_TIMEZONE
        )

        if (
            self.interval_start_utc
            .astimezone(market_tz)
            .date()
            != self.market_date
        ):
            raise ValueError(
                "intraday interval start does not belong to market_date"
            )

        if (
            self.interval_end_utc
            .astimezone(market_tz)
            .date()
            != self.market_date
        ):
            raise ValueError(
                "intraday interval end does not belong to market_date"
            )

        return self


def _canonical_bar(
    value: USHistoricalIntradayBar,
) -> USHistoricalIntradayBar:
    if type(value) is not USHistoricalIntradayBar:
        raise ValueError(
            "exact USHistoricalIntradayBar required"
        )

    return USHistoricalIntradayBar.model_validate(
        value.model_dump(mode="python")
    )


class USHistoricalIntradaySegment(BaseModel):
    """Explicit complete contiguous raw-artifact coverage segment."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    contract: Literal[
        "us-historical-intraday-segment-v1"
    ] = "us-historical-intraday-segment-v1"

    instrument_id: UUID
    market_date: date

    calendar_mic: str = Field(
        pattern=r"^[A-Z0-9]{4}$",
    )
    canonical_symbol: str = Field(
        pattern=_SYMBOL_PATTERN,
    )

    granularity: BarGranularity

    coverage_start_at_utc: datetime
    coverage_end_at_utc: datetime

    first_session_sequence: int = Field(
        ge=1,
    )
    last_session_sequence: int = Field(
        ge=1,
    )

    complete: Literal[True] = True

    source_provider: str = Field(
        min_length=1,
        max_length=128,
    )
    source_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    provenance_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    @field_validator(
        "instrument_id",
        mode="before",
    )
    @classmethod
    def exact_uuid(cls, value):
        return _require_exact_uuid(
            value,
            field_name="instrument_id",
        )

    @field_validator(
        "market_date",
        mode="before",
    )
    @classmethod
    def exact_date(cls, value):
        return _require_exact_date(
            value,
            field_name="market_date",
        )

    @field_validator(
        "granularity",
        mode="before",
    )
    @classmethod
    def exact_granularity(cls, value):
        return _require_granularity(value)

    @field_validator(
        "coverage_start_at_utc",
        "coverage_end_at_utc",
        mode="before",
    )
    @classmethod
    def exact_utc(cls, value, info):
        return _require_exact_utc(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "first_session_sequence",
        "last_session_sequence",
        mode="before",
    )
    @classmethod
    def exact_sequence(cls, value, info):
        if (
            type(value) is not int
            or value < 1
        ):
            raise ValueError(
                f"{info.field_name} must be exact positive int"
            )
        return value

    @field_validator(
        "complete",
        mode="before",
    )
    @classmethod
    def exact_complete(cls, value):
        if (
            type(value) is not bool
            or value is not True
        ):
            raise ValueError(
                "segment complete must be exact True"
            )
        return value

    @model_validator(mode="after")
    def coverage_geometry(
        self,
    ) -> "USHistoricalIntradaySegment":
        if (
            self.coverage_end_at_utc
            <= self.coverage_start_at_utc
        ):
            raise ValueError(
                "intraday segment end must follow start"
            )

        width = _GRANULARITY_WIDTH[
            self.granularity
        ]

        duration = (
            self.coverage_end_at_utc
            - self.coverage_start_at_utc
        )

        if duration % width != timedelta(0):
            raise ValueError(
                "segment duration not divisible by granularity"
            )

        count = duration // width

        if (
            self.last_session_sequence
            - self.first_session_sequence
            + 1
            != count
        ):
            raise ValueError(
                "segment sequence span does not match coverage"
            )

        return self


def _canonical_segment(
    value: USHistoricalIntradaySegment,
) -> USHistoricalIntradaySegment:
    if type(value) is not USHistoricalIntradaySegment:
        raise ValueError(
            "exact USHistoricalIntradaySegment required"
        )

    return USHistoricalIntradaySegment.model_validate(
        value.model_dump(mode="python")
    )


class HistoricalUSIntradaySegmentFact(BaseModel):
    """One reviewed raw artifact segment plus its exact intraday rows."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    segment: USHistoricalIntradaySegment
    bars: tuple[
        USHistoricalIntradayBar,
        ...
    ]
    evidence_package: HistoricalEvidencePackage

    @field_validator(
        "segment",
        mode="before",
    )
    @classmethod
    def exact_segment(cls, value):
        return _canonical_segment(value)

    @field_validator(
        "bars",
        mode="before",
    )
    @classmethod
    def exact_bars(cls, value):
        if (
            type(value) is not tuple
            or any(
                type(item)
                is not USHistoricalIntradayBar
                for item in value
            )
        ):
            raise ValueError(
                "canonical intraday-bar tuple required"
            )

        return tuple(
            _canonical_bar(item)
            for item in value
        )

    @field_validator(
        "evidence_package",
        mode="before",
    )
    @classmethod
    def exact_package(cls, value):
        if type(value) is not HistoricalEvidencePackage:
            raise ValueError(
                "exact HistoricalEvidencePackage required"
            )
        return value

    @model_validator(mode="after")
    def evidence_and_rows(
        self,
    ) -> "HistoricalUSIntradaySegmentFact":
        segment = self.segment
        package = self.evidence_package

        if not self.bars:
            raise ValueError(
                "intraday segment requires bars"
            )

        if (
            segment.source_provider
            != package.raw_receipt.provider
        ):
            raise ValueError(
                "segment source_provider does not match "
                "historical raw receipt provider"
            )

        if (
            segment.source_sha256
            != package.raw_receipt.sha256
        ):
            raise ValueError(
                "segment source_sha256 does not match "
                "historical raw receipt sha256"
            )

        covered = set(
            package.evidence.covered_fields
        )
        missing = (
            US_INTRADAY_EVIDENCE_FIELDS
            - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US intraday fields: "
                + ",".join(sorted(missing))
            )

        first = self.bars[0]
        last = self.bars[-1]

        if (
            first.interval_start_utc
            != segment.coverage_start_at_utc
            or last.interval_end_utc
            != segment.coverage_end_at_utc
        ):
            raise ValueError(
                "segment bars do not cover declared boundaries"
            )

        if (
            first.session_sequence
            != segment.first_session_sequence
            or last.session_sequence
            != segment.last_session_sequence
        ):
            raise ValueError(
                "segment bar sequence does not match declared sequence"
            )

        if (
            len(self.bars)
            != (
                segment.last_session_sequence
                - segment.first_session_sequence
                + 1
            )
        ):
            raise ValueError(
                "segment bar count does not match declared coverage"
            )

        provider_symbol = first.provider_symbol
        source_key = first.source_instrument_key

        previous = None

        for bar in self.bars:
            if (
                bar.instrument_id
                != segment.instrument_id
                or bar.market_date
                != segment.market_date
                or bar.calendar_mic
                != segment.calendar_mic
                or bar.canonical_symbol
                != segment.canonical_symbol
                or bar.granularity
                != segment.granularity
            ):
                raise ValueError(
                    "intraday bar/segment identity mismatch"
                )

            if (
                bar.source_provider
                != segment.source_provider
                or bar.source_sha256
                != segment.source_sha256
                or bar.provenance_id
                != segment.provenance_id
            ):
                raise ValueError(
                    "intraday bar/segment provenance mismatch"
                )

            if (
                bar.source_provider
                != package.raw_receipt.provider
                or bar.source_sha256
                != package.raw_receipt.sha256
            ):
                raise ValueError(
                    "intraday bar does not match historical raw receipt"
                )

            if (
                bar.provider_symbol
                != provider_symbol
                or bar.source_instrument_key
                != source_key
            ):
                raise ValueError(
                    "provider identity changes inside raw segment"
                )

            if previous is not None:
                if (
                    bar.session_sequence
                    != previous.session_sequence + 1
                    or bar.interval_start_utc
                    != previous.interval_end_utc
                ):
                    raise ValueError(
                        "gap/overlap/unordered intraday segment"
                    )

                if (
                    bar.source_row_number
                    <= previous.source_row_number
                ):
                    raise ValueError(
                        "source rows must be strictly increasing"
                    )

            previous = bar

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-intraday-segment-fact-v1"
            ),
            "segment": self.segment.model_dump(
                mode="json"
            ),
            "bars": [
                bar.model_dump(mode="json")
                for bar in self.bars
            ],
            "evidence_package_id": (
                self.evidence_package.identity
            ),
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")

        return hashlib.sha256(
            encoded
        ).hexdigest()


@dataclass(frozen=True)
class AdmittedUSIntradaySession:
    """Canonical contiguous intraday truth for one explicit US session."""

    instrument_id: UUID
    calendar_mic: str
    market_date: date
    canonical_symbol: str

    granularity: BarGranularity
    coverage_mode: USIntradayCoverageMode

    coverage_start_at_utc: datetime
    coverage_end_at_utc: datetime
    first_session_sequence: int
    last_session_sequence: int

    facts: tuple[
        HistoricalUSIntradaySegmentFact,
        ...
    ]
    bars: tuple[
        USHistoricalIntradayBar,
        ...
    ]

    evidence_cutoff_at: datetime
    research_built_at: datetime

    listing_history_id: str
    session_history_id: str

    @property
    def identity(self) -> str:
        # research_built_at is deliberately excluded.
        payload = {
            "schema_version": (
                "admitted-us-intraday-session-v1"
            ),
            "instrument_id": str(
                self.instrument_id
            ),
            "calendar_mic": self.calendar_mic,
            "market_date": (
                self.market_date.isoformat()
            ),
            "canonical_symbol": (
                self.canonical_symbol
            ),
            "granularity": (
                self.granularity.value
            ),
            "coverage_mode": (
                self.coverage_mode.value
            ),
            "coverage_start_at_utc": (
                self.coverage_start_at_utc.isoformat()
            ),
            "coverage_end_at_utc": (
                self.coverage_end_at_utc.isoformat()
            ),
            "first_session_sequence": (
                self.first_session_sequence
            ),
            "last_session_sequence": (
                self.last_session_sequence
            ),
            "evidence_cutoff_at": (
                self.evidence_cutoff_at.isoformat()
            ),
            "listing_history_id": (
                self.listing_history_id
            ),
            "session_history_id": (
                self.session_history_id
            ),
            "fact_ids": [
                item.identity
                for item in self.facts
            ],
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")

        return hashlib.sha256(
            encoded
        ).hexdigest()


def _canonical_listing_history(
    value: AdmittedUSListingHistory,
) -> AdmittedUSListingHistory:
    if type(value) is not AdmittedUSListingHistory:
        raise ValueError(
            "exact AdmittedUSListingHistory required"
        )

    canonical = admit_us_listing_history(
        value.facts,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US listing history"
        )

    return canonical


def _canonical_session_history(
    value: AdmittedUSSessionHistory,
) -> AdmittedUSSessionHistory:
    if type(value) is not AdmittedUSSessionHistory:
        raise ValueError(
            "exact AdmittedUSSessionHistory required"
        )

    canonical = admit_us_session_history(
        value.facts,
        calendar_mic=value.calendar_mic,
        coverage_start=value.coverage_start,
        coverage_end=value.coverage_end,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US session history"
        )

    return canonical


def _listing_on_exact_date(
    history: AdmittedUSListingHistory,
    *,
    market_date: date,
) -> USListingIdentity:
    matches = tuple(
        item.listing
        for item in history.facts
        if (
            item.listing.effective_date
            == market_date
        )
    )

    if len(matches) != 1:
        raise ValueError(
            "exact-dated US listing identity unavailable"
        )

    return USListingIdentity.model_validate(
        matches[0].model_dump(
            mode="python"
        )
    )


def _canonical_fact(
    value: HistoricalUSIntradaySegmentFact,
) -> HistoricalUSIntradaySegmentFact:
    if type(value) is not HistoricalUSIntradaySegmentFact:
        raise ValueError(
            "exact HistoricalUSIntradaySegmentFact required"
        )

    segment = _canonical_segment(
        value.segment
    )

    if type(value.bars) is not tuple:
        raise ValueError(
            "canonical intraday-bar tuple required"
        )

    bars = tuple(
        _canonical_bar(item)
        for item in value.bars
    )

    if type(value.evidence_package) is not HistoricalEvidencePackage:
        raise ValueError(
            "exact HistoricalEvidencePackage required"
        )

    # Keep nested canonical objects intact.  Dumping the whole fact to
    # dictionaries would violate this contract's exact-object validators.
    # The evidence package is canonically re-admitted below through
    # require_historical_evidence().
    return HistoricalUSIntradaySegmentFact(
        segment=segment,
        bars=bars,
        evidence_package=value.evidence_package,
    )


def admit_us_intraday_session(
    facts: tuple[
        HistoricalUSIntradaySegmentFact,
        ...
    ],
    *,
    instrument_id: UUID,
    calendar_mic: str,
    market_date: date,
    granularity: BarGranularity,
    coverage_mode: USIntradayCoverageMode,
    listing_history: AdmittedUSListingHistory,
    session_history: AdmittedUSSessionHistory,
    evidence_cutoff_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSIntradaySession:
    """Admit one contiguous historical intraday execution-truth window."""

    if type(facts) is not tuple:
        raise ValueError(
            "canonical intraday segment fact tuple required"
        )

    if not facts:
        raise ValueError(
            "at least one intraday segment fact required"
        )

    instrument_id = _require_exact_uuid(
        instrument_id,
        field_name="instrument_id",
    )
    calendar_mic = _require_calendar_mic(
        calendar_mic
    )
    market_date = _require_exact_date(
        market_date,
        field_name="market_date",
    )
    granularity = _require_granularity(
        granularity
    )
    coverage_mode = _require_coverage_mode(
        coverage_mode
    )

    evidence_cutoff_at = _require_exact_utc(
        evidence_cutoff_at,
        field_name="evidence_cutoff_at",
    )
    research_built_at = _require_exact_utc(
        research_built_at,
        field_name="research_built_at",
    )

    if research_built_at < evidence_cutoff_at:
        raise ValueError(
            "research_built_at cannot precede evidence_cutoff_at"
        )

    cutoff_market_date = (
        evidence_cutoff_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    if market_date > cutoff_market_date:
        raise ValueError(
            "intraday market_date cannot exceed "
            "US-local evidence cutoff date"
        )

    listing_history = _canonical_listing_history(
        listing_history
    )
    session_history = _canonical_session_history(
        session_history
    )

    if (
        listing_history.decision_at
        != evidence_cutoff_at
    ):
        raise ValueError(
            "listing history decision_at must match "
            "intraday evidence_cutoff_at"
        )

    if (
        session_history.decision_at
        != evidence_cutoff_at
    ):
        raise ValueError(
            "session history decision_at must match "
            "intraday evidence_cutoff_at"
        )

    if (
        listing_history.research_built_at
        > research_built_at
    ):
        raise ValueError(
            "listing history was built after "
            "intraday research build"
        )

    if (
        session_history.research_built_at
        > research_built_at
    ):
        raise ValueError(
            "session history was built after "
            "intraday research build"
        )

    if (
        session_history.calendar_mic
        != calendar_mic
    ):
        raise ValueError(
            "session history calendar_mic mismatch"
        )

    if not (
        session_history.coverage_start
        <= market_date
        <= session_history.coverage_end
    ):
        raise ValueError(
            "session history does not cover intraday market_date"
        )

    listing = _listing_on_exact_date(
        listing_history,
        market_date=market_date,
    )

    if listing.instrument_id != instrument_id:
        raise ValueError(
            "exact-dated listing instrument_id mismatch"
        )

    if listing.listing_mic != calendar_mic:
        raise ValueError(
            "exact-dated listing MIC mismatch"
        )

    session = resolve_us_session_on_date(
        session_history,
        market_date=market_date,
    )

    if session.calendar_mic != calendar_mic:
        raise ValueError(
            "exact session calendar_mic mismatch"
        )

    if session.state == USSessionState.CLOSED:
        raise ValueError(
            "intraday truth cannot exist on explicitly closed session"
        )

    if (
        session.opens_at_utc is None
        or session.closes_at_utc is None
    ):
        raise ValueError(
            "open session requires explicit boundaries"
        )

    admitted: list[
        HistoricalUSIntradaySegmentFact
    ] = []

    for fact in facts:
        canonical = _canonical_fact(
            fact
        )

        _require_package_exact_utc(
            canonical.evidence_package
        )

        package = require_historical_evidence(
            canonical.evidence_package,
            decision_at=evidence_cutoff_at,
            research_built_at=research_built_at,
        )

        canonical = HistoricalUSIntradaySegmentFact(
            segment=canonical.segment,
            bars=canonical.bars,
            evidence_package=package,
        )

        admitted.append(
            canonical
        )

    # Event chronology is execution-sensitive. Do not sort or repair.
    starts = tuple(
        item.segment.coverage_start_at_utc
        for item in admitted
    )

    if starts != tuple(sorted(starts)):
        raise ValueError(
            "intraday segments must already be chronologically ordered"
        )

    seen_fact_ids: set[str] = set()
    seen_source_rows: set[
        tuple[str, int]
    ] = set()

    previous_fact = None
    flattened: list[
        USHistoricalIntradayBar
    ] = []

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US intraday segment fact"
            )

        seen_fact_ids.add(
            item.identity
        )

        segment = item.segment

        if (
            segment.instrument_id
            != instrument_id
            or segment.market_date
            != market_date
            or segment.calendar_mic
            != calendar_mic
            or segment.canonical_symbol
            != listing.canonical_symbol
            or segment.granularity
            != granularity
        ):
            raise ValueError(
                "intraday segment does not match "
                "stable dated listing/session identity"
            )

        if (
            segment.coverage_start_at_utc
            < session.opens_at_utc
            or segment.coverage_end_at_utc
            > session.closes_at_utc
        ):
            raise ValueError(
                "intraday segment outside explicit session boundaries"
            )

        if previous_fact is not None:
            previous_segment = (
                previous_fact.segment
            )

            if (
                previous_segment.coverage_end_at_utc
                != segment.coverage_start_at_utc
            ):
                raise ValueError(
                    "gap/overlap between intraday segments"
                )

            if (
                segment.first_session_sequence
                != (
                    previous_segment.last_session_sequence
                    + 1
                )
            ):
                raise ValueError(
                    "nonconsecutive session sequence across segments"
                )

        for bar in item.bars:
            if bar.available_at_utc > evidence_cutoff_at:
                raise ValueError(
                    "intraday bar unavailable by evidence cutoff"
                )

            source_key = (
                bar.source_sha256,
                bar.source_row_number,
            )

            if source_key in seen_source_rows:
                raise ValueError(
                    "duplicate intraday raw source row"
                )

            seen_source_rows.add(
                source_key
            )

            flattened.append(
                bar
            )

        previous_fact = item

    coverage_start = (
        admitted[0]
        .segment
        .coverage_start_at_utc
    )
    coverage_end = (
        admitted[-1]
        .segment
        .coverage_end_at_utc
    )
    first_sequence = (
        admitted[0]
        .segment
        .first_session_sequence
    )
    last_sequence = (
        admitted[-1]
        .segment
        .last_session_sequence
    )

    width = _GRANULARITY_WIDTH[
        granularity
    ]

    start_offset = (
        coverage_start
        - session.opens_at_utc
    )
    end_offset = (
        coverage_end
        - session.opens_at_utc
    )

    if (
        start_offset < timedelta(0)
        or end_offset <= start_offset
    ):
        raise ValueError(
            "invalid intraday session coverage offsets"
        )

    if (
        start_offset % width
        != timedelta(0)
        or end_offset % width
        != timedelta(0)
    ):
        raise ValueError(
            "intraday coverage not aligned to explicit session grid"
        )

    expected_first_sequence = (
        start_offset // width
        + 1
    )
    expected_last_sequence = (
        end_offset // width
    )

    if (
        first_sequence
        != expected_first_sequence
        or last_sequence
        != expected_last_sequence
    ):
        raise ValueError(
            "intraday sequence does not match explicit session origin"
        )

    if coverage_mode == USIntradayCoverageMode.FULL_SESSION:
        if (
            coverage_start
            != session.opens_at_utc
            or coverage_end
            != session.closes_at_utc
        ):
            raise ValueError(
                "FULL_SESSION requires exact explicit session coverage"
            )

        session_duration = (
            session.closes_at_utc
            - session.opens_at_utc
        )

        if (
            session_duration % width
            != timedelta(0)
        ):
            raise ValueError(
                "explicit session duration incompatible with granularity"
            )

        expected_count = (
            session_duration // width
        )

        if (
            first_sequence != 1
            or last_sequence
            != expected_count
            or len(flattened)
            != expected_count
        ):
            raise ValueError(
                "FULL_SESSION sequence/count incomplete"
            )

    else:
        expected_count = (
            coverage_end - coverage_start
        ) // width

        if len(flattened) != expected_count:
            raise ValueError(
                "BOUNDED_WINDOW sequence/count incomplete"
            )

    return AdmittedUSIntradaySession(
        instrument_id=instrument_id,
        calendar_mic=calendar_mic,
        market_date=market_date,
        canonical_symbol=(
            listing.canonical_symbol
        ),
        granularity=granularity,
        coverage_mode=coverage_mode,
        coverage_start_at_utc=coverage_start,
        coverage_end_at_utc=coverage_end,
        first_session_sequence=(
            first_sequence
        ),
        last_session_sequence=(
            last_sequence
        ),
        facts=tuple(admitted),
        bars=tuple(flattened),
        evidence_cutoff_at=(
            evidence_cutoff_at
        ),
        research_built_at=(
            research_built_at
        ),
        listing_history_id=(
            listing_history.identity
        ),
        session_history_id=(
            session_history.identity
        ),
    )


__all__ = [
    "US_INTRADAY_EVIDENCE_FIELDS",
    "AdmittedUSIntradaySession",
    "HistoricalUSIntradaySegmentFact",
    "USHistoricalIntradayBar",
    "USHistoricalIntradaySegment",
    "USIntradayCoverageMode",
    "admit_us_intraday_session",
]
