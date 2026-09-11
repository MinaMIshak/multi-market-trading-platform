"""US market semantic contracts.

US0 is deliberately provider-, storage-, strategy-, and execution-agnostic.
The models here describe explicit historical facts.  They do not infer
calendar truth, ticker continuity, universe membership, or corporate actions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


US_MARKET_TIMEZONE = "America/New_York"

_US_SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9._-]{0,63}$"
_MIC_PATTERN = r"^[A-Z0-9]{4}$"


class USContractModel(BaseModel):
    """Strict immutable base for US historical semantic facts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class USSecurityType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ADR = "ADR"
    ETF = "ETF"
    REIT = "REIT"
    PREFERRED = "PREFERRED"
    CLOSED_END_FUND = "CLOSED_END_FUND"
    OTHER = "OTHER"


class USSessionState(StrEnum):
    REGULAR = "REGULAR"
    EARLY_CLOSE = "EARLY_CLOSE"
    CLOSED = "CLOSED"


class USCorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    STOCK_DIVIDEND = "STOCK_DIVIDEND"
    MERGER = "MERGER"
    SPINOFF = "SPINOFF"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"
    RIGHTS = "RIGHTS"
    OTHER = "OTHER"


class USListingIdentity(USContractModel):
    """Exact-dated listing identity.

    ``instrument_id`` is the stable platform identity.  A ticker is only an
    exact-dated attribute and must never itself be treated as the security
    identity.
    """

    contract: Literal["us-listing-identity-v1"] = (
        "us-listing-identity-v1"
    )
    market: Literal["US"] = "US"

    effective_date: date
    instrument_id: UUID

    canonical_symbol: str = Field(
        pattern=_US_SYMBOL_PATTERN,
    )
    listing_mic: str = Field(
        pattern=_MIC_PATTERN,
    )
    security_type: USSecurityType

    currency: Literal["USD"] = "USD"

    provider_symbol: str = Field(
        min_length=1,
        max_length=128,
    )
    source_provider: str = Field(
        min_length=1,
        max_length=128,
    )
    source_instrument_key: str = Field(
        min_length=1,
        max_length=256,
    )

    is_primary_listing: bool = Field(strict=True)


class USUniverseMember(USContractModel):
    """One exact-dated US research-universe member."""

    instrument_id: UUID
    canonical_symbol: str = Field(
        pattern=_US_SYMBOL_PATTERN,
    )
    listing_mic: str = Field(
        pattern=_MIC_PATTERN,
    )
    security_type: USSecurityType
    eligible: bool = Field(strict=True)


class USUniverseSnapshot(USContractModel):
    """Complete exact-date universe snapshot.

    No current-membership projection, latest-wins rule, or forward filling is
    represented by this contract.
    """

    contract: Literal["us-universe-v1"] = "us-universe-v1"
    market: Literal["US"] = "US"

    effective_date: date
    complete: Literal[True] = True
    members: tuple[USUniverseMember, ...]

    @model_validator(mode="after")
    def require_unique_members(
        self,
    ) -> "USUniverseSnapshot":
        instrument_ids = [
            member.instrument_id
            for member in self.members
        ]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError(
                "duplicate/conflicting universe instrument identity"
            )

        listing_keys = [
            (
                member.listing_mic,
                member.canonical_symbol,
            )
            for member in self.members
        ]
        if len(listing_keys) != len(set(listing_keys)):
            raise ValueError(
                "duplicate/conflicting dated listing identity"
            )

        return self


def _require_exact_utc(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.tzinfo is not timezone.utc
    ):
        raise ValueError(
            f"{field_name} must use datetime.timezone.utc"
        )


class USSessionRecord(USContractModel):
    """Explicit US market-session truth.

    Times are evidence inputs. ``calendar_mic`` scopes the historical
    calendar fact to an explicit exchange/calendar MIC.  This model
    intentionally does not infer regular hours, holidays, weekends, DST
    offsets, or early-close times.
    """

    contract: Literal["us-session-v1"] = "us-session-v1"
    market: Literal["US"] = "US"

    market_date: date
    calendar_mic: str = Field(
        pattern=_MIC_PATTERN,
    )
    timezone_name: Literal["America/New_York"] = (
        US_MARKET_TIMEZONE
    )
    state: USSessionState

    opens_at_utc: datetime | None = None
    closes_at_utc: datetime | None = None

    @model_validator(mode="after")
    def validate_session_truth(
        self,
    ) -> "USSessionRecord":
        if self.state == USSessionState.CLOSED:
            if (
                self.opens_at_utc is not None
                or self.closes_at_utc is not None
            ):
                raise ValueError(
                    "closed session cannot contain open/close timestamps"
                )
            return self

        if (
            self.opens_at_utc is None
            or self.closes_at_utc is None
        ):
            raise ValueError(
                "trading session requires explicit open and close"
            )

        _require_exact_utc(
            self.opens_at_utc,
            field_name="opens_at_utc",
        )
        _require_exact_utc(
            self.closes_at_utc,
            field_name="closes_at_utc",
        )

        if self.closes_at_utc <= self.opens_at_utc:
            raise ValueError(
                "session close must be after session open"
            )

        market_tz = ZoneInfo(US_MARKET_TIMEZONE)

        if (
            self.opens_at_utc.astimezone(market_tz).date()
            != self.market_date
        ):
            raise ValueError(
                "session open does not belong to market_date"
            )

        if (
            self.closes_at_utc.astimezone(market_tz).date()
            != self.market_date
        ):
            raise ValueError(
                "session close does not belong to market_date"
            )

        return self


class USCorporateActionEvent(USContractModel):
    """One explicit historical corporate-action event."""

    event_id: str = Field(
        min_length=1,
        max_length=256,
    )
    effective_date: date
    action_type: USCorporateActionType

    # Split ratio is explicit new shares / old shares.
    new_shares: Decimal | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    old_shares: Decimal | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )

    # Symbol changes are represented explicitly rather than inferred.
    old_symbol: str | None = Field(
        default=None,
        pattern=_US_SYMBOL_PATTERN,
    )
    new_symbol: str | None = Field(
        default=None,
        pattern=_US_SYMBOL_PATTERN,
    )

    # Cash distributions are represented explicitly but are not yet an
    # executable price-adjustment rule.
    cash_amount: Decimal | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    cash_currency: Literal["USD"] | None = None

    details: str = Field(
        min_length=1,
        max_length=2048,
    )

    @model_validator(mode="after")
    def require_action_specific_terms(
        self,
    ) -> "USCorporateActionEvent":
        ratio_present = (
            self.new_shares is not None
            or self.old_shares is not None
        )
        symbols_present = (
            self.old_symbol is not None
            or self.new_symbol is not None
        )
        cash_present = (
            self.cash_amount is not None
            or self.cash_currency is not None
        )

        if self.action_type == USCorporateActionType.SPLIT:
            if (
                self.new_shares is None
                or self.old_shares is None
            ):
                raise ValueError(
                    "split requires explicit new_shares and old_shares"
                )
            if symbols_present or cash_present:
                raise ValueError(
                    "split cannot contain symbol-change or cash terms"
                )
            return self

        if (
            self.action_type
            == USCorporateActionType.SYMBOL_CHANGE
        ):
            if (
                self.old_symbol is None
                or self.new_symbol is None
            ):
                raise ValueError(
                    "symbol change requires old_symbol and new_symbol"
                )
            if self.old_symbol == self.new_symbol:
                raise ValueError(
                    "symbol change requires distinct symbols"
                )
            if ratio_present or cash_present:
                raise ValueError(
                    "symbol change cannot contain split or cash terms"
                )
            return self

        if (
            self.action_type
            == USCorporateActionType.CASH_DIVIDEND
        ):
            if (
                self.cash_amount is None
                or self.cash_currency != "USD"
            ):
                raise ValueError(
                    "cash dividend requires explicit USD cash amount"
                )
            if ratio_present or symbols_present:
                raise ValueError(
                    "cash dividend cannot contain split or symbol terms"
                )
            return self

        if ratio_present:
            raise ValueError(
                "share ratio is supported only for explicit splits"
            )

        if symbols_present:
            raise ValueError(
                "symbol fields are supported only for symbol changes"
            )

        if cash_present:
            raise ValueError(
                "cash fields are supported only for cash dividends"
            )

        return self


class USCorporateActionCoverage(USContractModel):
    """Complete action coverage for one stable instrument and date range."""

    contract: Literal["us-actions-v1"] = "us-actions-v1"
    market: Literal["US"] = "US"

    instrument_id: UUID
    coverage_start: date
    coverage_end: date

    complete: Literal[True] = True
    actions: tuple[USCorporateActionEvent, ...]

    @model_validator(mode="after")
    def validate_coverage(
        self,
    ) -> "USCorporateActionCoverage":
        if self.coverage_end < self.coverage_start:
            raise ValueError(
                "invalid corporate-action coverage"
            )

        event_ids = [
            action.event_id
            for action in self.actions
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(
                "duplicate corporate-action event_id"
            )

        for action in self.actions:
            if not (
                self.coverage_start
                <= action.effective_date
                <= self.coverage_end
            ):
                raise ValueError(
                    "corporate action outside declared coverage"
                )

        return self


__all__ = [
    "US_MARKET_TIMEZONE",
    "USSecurityType",
    "USSessionState",
    "USCorporateActionType",
    "USListingIdentity",
    "USUniverseMember",
    "USUniverseSnapshot",
    "USSessionRecord",
    "USCorporateActionEvent",
    "USCorporateActionCoverage",
]
