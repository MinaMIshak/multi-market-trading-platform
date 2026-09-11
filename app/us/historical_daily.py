"""US3 retrospective raw US daily-bar admission.

US3 admits raw, unadjusted historical OHLCV observations for research.

It deliberately does not:
- infer market sessions from bar presence or absence;
- treat ticker as security identity;
- apply split/dividend/corporate-action adjustments;
- use provider adjusted-close references as executable prices;
- forward/back fill missing observations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
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

from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.contracts import (
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
)


_SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9._-]{0,63}$"
_MIC_RE = re.compile(r"^[A-Z0-9]{4}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


US_DAILY_BAR_EVIDENCE_FIELDS = frozenset(
    {
        "instrument_id",
        "market_date",
        "calendar_mic",
        "canonical_symbol",
        "provider_symbol",
        "source_instrument_key",
        "currency",
        "price_basis",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider_adjusted_close_reference",
        "source_provider",
        "source_snapshot_date",
        "source_row_number",
        "source_sha256",
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


class USHistoricalDailyBar(BaseModel):
    """One raw unadjusted US daily OHLCV observation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    contract: Literal["us-historical-daily-bar-v1"] = (
        "us-historical-daily-bar-v1"
    )
    market: Literal["US"] = "US"

    instrument_id: UUID
    market_date: date
    calendar_mic: str = Field(
        pattern=_SYMBOL_PATTERN[:0] + r"^[A-Z0-9]{4}$"
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
    price_basis: Literal["RAW_UNADJUSTED"] = (
        "RAW_UNADJUSTED"
    )

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

    # Audit/reference only. Never an execution price and never used by US3
    # to transform raw OHLCV.
    provider_adjusted_close_reference: (
        Decimal | None
    ) = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )

    source_provider: str = Field(
        min_length=1,
        max_length=128,
    )
    source_snapshot_date: date
    source_row_number: int = Field(
        strict=True,
        ge=1,
    )
    source_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )

    @field_validator(
        "instrument_id",
        mode="before",
    )
    @classmethod
    def exact_instrument_id(
        cls,
        value,
    ):
        return _require_exact_uuid(
            value,
            field_name="instrument_id",
        )

    @field_validator(
        "market_date",
        "source_snapshot_date",
        mode="before",
    )
    @classmethod
    def exact_dates(
        cls,
        value,
        info,
    ):
        return _require_exact_date(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "open",
        "high",
        "low",
        "close",
        "volume",
        mode="before",
    )
    @classmethod
    def exact_decimal(
        cls,
        value,
        info,
    ):
        if type(value) is not Decimal:
            raise ValueError(
                f"{info.field_name} must be exact Decimal"
            )
        return value

    @field_validator(
        "provider_adjusted_close_reference",
        mode="before",
    )
    @classmethod
    def exact_optional_decimal(
        cls,
        value,
    ):
        if (
            value is not None
            and type(value) is not Decimal
        ):
            raise ValueError(
                "provider_adjusted_close_reference "
                "must be exact Decimal"
            )
        return value

    @model_validator(mode="after")
    def validate_raw_bar(
        self,
    ) -> "USHistoricalDailyBar":
        if self.high < self.low:
            raise ValueError(
                "high cannot be below low"
            )

        if not (
            self.low <= self.open <= self.high
        ):
            raise ValueError(
                "open outside high-low range"
            )

        if not (
            self.low <= self.close <= self.high
        ):
            raise ValueError(
                "close outside high-low range"
            )

        if self.source_snapshot_date < self.market_date:
            raise ValueError(
                "source_snapshot_date cannot precede market_date"
            )

        return self


def _canonical_bar(
    value: USHistoricalDailyBar,
) -> USHistoricalDailyBar:
    if type(value) is not USHistoricalDailyBar:
        raise ValueError(
            "exact USHistoricalDailyBar required"
        )

    return USHistoricalDailyBar.model_validate(
        value.model_dump(mode="python")
    )


class HistoricalUSDailyBarFact(BaseModel):
    """One raw daily bar bound to reviewed retrospective evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    bar: USHistoricalDailyBar
    evidence_package: HistoricalEvidencePackage

    @field_validator("bar", mode="before")
    @classmethod
    def exact_bar_type(
        cls,
        value,
    ):
        return _canonical_bar(value)

    @field_validator(
        "evidence_package",
        mode="before",
    )
    @classmethod
    def exact_package_type(
        cls,
        value,
    ):
        if type(value) is not HistoricalEvidencePackage:
            raise ValueError(
                "exact HistoricalEvidencePackage required"
            )
        return value

    @model_validator(mode="after")
    def evidence_binding(
        self,
    ) -> "HistoricalUSDailyBarFact":
        bar = self.bar
        package = self.evidence_package

        if (
            bar.source_provider
            != package.raw_receipt.provider
        ):
            raise ValueError(
                "bar source_provider does not match "
                "historical raw receipt provider"
            )

        if (
            bar.source_sha256
            != package.raw_receipt.sha256
        ):
            raise ValueError(
                "bar source_sha256 does not match "
                "historical raw receipt sha256"
            )

        covered = set(
            package.evidence.covered_fields
        )
        missing = (
            US_DAILY_BAR_EVIDENCE_FIELDS - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US daily-bar fields: "
                + ",".join(sorted(missing))
            )

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-daily-bar-fact-v1"
            ),
            "bar": self.bar.model_dump(
                mode="json"
            ),
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

        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AdmittedUSDailyBarHistory:
    """Canonical sparse set of admitted raw daily-bar observations."""

    instrument_id: UUID
    calendar_mic: str
    coverage_start: date
    coverage_end: date
    facts: tuple[HistoricalUSDailyBarFact, ...]
    decision_at: datetime
    research_built_at: datetime
    listing_history_id: str
    session_history_id: str

    @property
    def identity(self) -> str:
        # research_built_at is deliberately excluded.
        payload = {
            "schema_version": (
                "admitted-us-daily-bar-history-v1"
            ),
            "instrument_id": str(self.instrument_id),
            "calendar_mic": self.calendar_mic,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "decision_at": self.decision_at.isoformat(),
            "listing_history_id": self.listing_history_id,
            "session_history_id": self.session_history_id,
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

        return hashlib.sha256(encoded).hexdigest()


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


def admit_us_daily_bar_history(
    facts: tuple[HistoricalUSDailyBarFact, ...],
    *,
    instrument_id: UUID,
    calendar_mic: str,
    coverage_start: date,
    coverage_end: date,
    listing_history: AdmittedUSListingHistory,
    session_history: AdmittedUSSessionHistory,
    decision_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSDailyBarHistory:
    """Admit raw US daily observations without inferring missing bars."""

    if type(facts) is not tuple:
        raise ValueError(
            "canonical daily-bar fact tuple required"
        )

    instrument_id = _require_exact_uuid(
        instrument_id,
        field_name="instrument_id",
    )
    calendar_mic = _require_calendar_mic(
        calendar_mic
    )

    coverage_start = _require_exact_date(
        coverage_start,
        field_name="coverage_start",
    )
    coverage_end = _require_exact_date(
        coverage_end,
        field_name="coverage_end",
    )

    if coverage_end < coverage_start:
        raise ValueError(
            "coverage_end cannot precede coverage_start"
        )

    decision_at = _require_exact_utc(
        decision_at,
        field_name="decision_at",
    )
    research_built_at = _require_exact_utc(
        research_built_at,
        field_name="research_built_at",
    )

    if research_built_at < decision_at:
        raise ValueError(
            "research_built_at cannot precede decision_at"
        )

    decision_market_date = (
        decision_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    if coverage_end > decision_market_date:
        raise ValueError(
            "coverage_end cannot exceed "
            "US-local decision date"
        )

    listing_history = _canonical_listing_history(
        listing_history
    )
    session_history = _canonical_session_history(
        session_history
    )

    if listing_history.decision_at != decision_at:
        raise ValueError(
            "listing history decision_at must match "
            "daily-bar decision_at"
        )

    if session_history.decision_at != decision_at:
        raise ValueError(
            "session history decision_at must match "
            "daily-bar decision_at"
        )

    if (
        listing_history.research_built_at
        > research_built_at
    ):
        raise ValueError(
            "listing history was built after "
            "daily-bar research build"
        )

    if (
        session_history.research_built_at
        > research_built_at
    ):
        raise ValueError(
            "session history was built after "
            "daily-bar research build"
        )

    if session_history.calendar_mic != calendar_mic:
        raise ValueError(
            "session history calendar_mic mismatch"
        )

    if (
        session_history.coverage_start > coverage_start
        or session_history.coverage_end < coverage_end
    ):
        raise ValueError(
            "session history does not cover "
            "daily-bar coverage interval"
        )

    session_by_date = {
        item.session.market_date: item.session
        for item in session_history.facts
    }

    admitted: list[
        HistoricalUSDailyBarFact
    ] = []

    for item in facts:
        if type(item) is not HistoricalUSDailyBarFact:
            raise ValueError(
                "exact HistoricalUSDailyBarFact required"
            )

        bar = _canonical_bar(item.bar)

        package = require_historical_evidence(
            item.evidence_package,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

        _require_package_exact_utc(package)

        canonical = HistoricalUSDailyBarFact(
            bar=bar,
            evidence_package=package,
        )

        bar = canonical.bar

        if not (
            coverage_start
            <= bar.market_date
            <= coverage_end
        ):
            raise ValueError(
                "US daily bar outside requested coverage"
            )

        if bar.market_date > decision_market_date:
            raise ValueError(
                "future-effective US daily bar "
                "cannot enter admitted history"
            )

        if bar.instrument_id != instrument_id:
            raise ValueError(
                "US daily bar instrument_id mismatch"
            )

        if bar.calendar_mic != calendar_mic:
            raise ValueError(
                "US daily bar calendar_mic mismatch"
            )

        session = session_by_date.get(
            bar.market_date
        )

        if session is None:
            raise ValueError(
                "explicit US session truth unavailable "
                "for daily-bar date"
            )

        if session.state == USSessionState.CLOSED:
            raise ValueError(
                "US daily bar cannot exist on "
                "explicitly closed session"
            )

        listings = [
            item.listing
            for item in listing_history.facts
            if (
                item.listing.effective_date
                == bar.market_date
                and item.listing.instrument_id
                == instrument_id
            )
        ]

        if len(listings) != 1:
            raise ValueError(
                "exact-dated US listing identity "
                "unavailable for daily-bar date"
            )

        listing = listings[0]

        if listing.listing_mic != calendar_mic:
            raise ValueError(
                "daily-bar MIC does not match "
                "exact-dated listing identity"
            )

        if (
            listing.canonical_symbol
            != bar.canonical_symbol
        ):
            raise ValueError(
                "daily-bar canonical_symbol does not match "
                "exact-dated listing identity"
            )

        admitted.append(canonical)

    if not admitted:
        raise ValueError(
            "at least one historical US daily-bar fact required"
        )

    admitted.sort(
        key=lambda item: (
            item.bar.market_date,
            item.identity,
        )
    )

    seen_fact_ids: set[str] = set()
    by_date: dict[
        date,
        HistoricalUSDailyBarFact,
    ] = {}

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US daily-bar fact"
            )

        seen_fact_ids.add(item.identity)

        market_date = item.bar.market_date

        if market_date in by_date:
            raise ValueError(
                "multiple US daily-bar facts for same "
                "instrument and market_date"
            )

        by_date[market_date] = item

    canonical_facts = tuple(
        by_date[item]
        for item in sorted(by_date)
    )

    return AdmittedUSDailyBarHistory(
        instrument_id=instrument_id,
        calendar_mic=calendar_mic,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        facts=canonical_facts,
        decision_at=decision_at,
        research_built_at=research_built_at,
        listing_history_id=listing_history.identity,
        session_history_id=session_history.identity,
    )


def resolve_us_daily_bar_on_date(
    history: AdmittedUSDailyBarHistory,
    *,
    market_date: date,
) -> USHistoricalDailyBar:
    """Resolve one exact admitted bar; never fill a missing observation."""

    if type(history) is not AdmittedUSDailyBarHistory:
        raise ValueError(
            "exact AdmittedUSDailyBarHistory required"
        )

    market_date = _require_exact_date(
        market_date,
        field_name="market_date",
    )

    if not (
        history.coverage_start
        <= market_date
        <= history.coverage_end
    ):
        raise ValueError(
            "market_date outside admitted daily-bar coverage"
        )

    for item in history.facts:
        if item.bar.market_date == market_date:
            return _canonical_bar(item.bar)

    raise ValueError(
        "no admitted US daily bar for market_date"
    )
