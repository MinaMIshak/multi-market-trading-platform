"""US1 retrospective US listing-identity admission.

This module binds exact-dated US listing facts to reviewed retrospective
historical-evidence packages.

It deliberately does not infer ticker continuity, listing intervals, universe
membership, delistings, or current-to-historical mappings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.contracts import (
    USListingIdentity,
    US_MARKET_TIMEZONE,
)


US_LISTING_EVIDENCE_FIELDS = frozenset(
    {
        "effective_date",
        "instrument_id",
        "canonical_symbol",
        "listing_mic",
        "security_type",
        "currency",
        "provider_symbol",
        "source_provider",
        "source_instrument_key",
        "is_primary_listing",
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
    """Tighten R1.1 clocks to exact ``datetime.timezone.utc``."""

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


def _canonical_listing(
    value: USListingIdentity,
) -> USListingIdentity:
    if type(value) is not USListingIdentity:
        raise ValueError(
            "exact USListingIdentity required"
        )

    return USListingIdentity.model_validate(
        value.model_dump(mode="python")
    )


class HistoricalUSListingFact(BaseModel):
    """One exact-dated US listing fact and its source evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    listing: USListingIdentity
    evidence_package: HistoricalEvidencePackage

    @field_validator("listing", mode="before")
    @classmethod
    def exact_listing_type(
        cls,
        value,
    ):
        return _canonical_listing(value)

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
    def source_binding(
        self,
    ) -> "HistoricalUSListingFact":
        listing = self.listing
        package = self.evidence_package

        if (
            listing.source_provider
            != package.raw_receipt.provider
        ):
            raise ValueError(
                "listing source_provider does not match "
                "historical raw receipt provider"
            )

        covered = set(
            package.evidence.covered_fields
        )
        missing = (
            US_LISTING_EVIDENCE_FIELDS - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US listing fields: "
                + ",".join(sorted(missing))
            )

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-listing-fact-v1"
            ),
            "listing": self.listing.model_dump(
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
class AdmittedUSListingHistory:
    """Canonical admitted exact-date identity facts."""

    facts: tuple[HistoricalUSListingFact, ...]
    decision_at: datetime
    research_built_at: datetime

    @property
    def identity(self) -> str:
        # research_built_at is intentionally excluded:
        # rebuilding the same admitted historical semantic subset later
        # must not change its semantic identity.
        payload = {
            "schema_version": (
                "admitted-us-listing-history-v1"
            ),
            "decision_at": (
                self.decision_at.isoformat()
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

        return hashlib.sha256(encoded).hexdigest()


def _fact_sort_key(
    fact: HistoricalUSListingFact,
):
    listing = fact.listing

    return (
        listing.effective_date,
        str(listing.instrument_id),
        listing.listing_mic,
        listing.canonical_symbol,
        listing.source_provider,
        listing.source_instrument_key,
        fact.identity,
    )


def admit_us_listing_history(
    facts: tuple[HistoricalUSListingFact, ...],
    *,
    decision_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSListingHistory:
    """Admit retrospective exact-dated US identity facts.

    Every submitted fact must be historically admissible.  Future or
    unavailable evidence is not silently filtered.
    """

    if type(facts) is not tuple:
        raise ValueError(
            "canonical listing fact tuple required"
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

    admitted: list[
        HistoricalUSListingFact
    ] = []

    for item in facts:
        if type(item) is not HistoricalUSListingFact:
            raise ValueError(
                "exact HistoricalUSListingFact required"
            )

        listing = _canonical_listing(
            item.listing
        )

        package = require_historical_evidence(
            item.evidence_package,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

        _require_package_exact_utc(package)

        canonical = HistoricalUSListingFact(
            listing=listing,
            evidence_package=package,
        )

        if (
            canonical.listing.effective_date
            > decision_market_date
        ):
            raise ValueError(
                "future-effective listing identity "
                "cannot enter admitted history"
            )

        admitted.append(canonical)

    admitted.sort(key=_fact_sort_key)

    seen_fact_ids: set[str] = set()
    instrument_date: dict[
        tuple[date, UUID],
        HistoricalUSListingFact,
    ] = {}
    listing_date: dict[
        tuple[date, str, str],
        UUID,
    ] = {}
    provider_key_date: dict[
        tuple[date, str, str],
        UUID,
    ] = {}

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US listing fact"
            )
        seen_fact_ids.add(item.identity)

        listing = item.listing

        instrument_key = (
            listing.effective_date,
            listing.instrument_id,
        )
        if instrument_key in instrument_date:
            raise ValueError(
                "multiple identity facts for same "
                "instrument and effective date"
            )
        instrument_date[instrument_key] = item

        listing_key = (
            listing.effective_date,
            listing.listing_mic,
            listing.canonical_symbol,
        )

        existing_instrument = listing_date.get(
            listing_key
        )
        if (
            existing_instrument is not None
            and existing_instrument
            != listing.instrument_id
        ):
            raise ValueError(
                "ambiguous dated US listing key"
            )

        listing_date[listing_key] = (
            listing.instrument_id
        )

        provider_key = (
            listing.effective_date,
            listing.source_provider,
            listing.source_instrument_key,
        )

        existing_provider_instrument = (
            provider_key_date.get(provider_key)
        )

        if (
            existing_provider_instrument
            is not None
            and existing_provider_instrument
            != listing.instrument_id
        ):
            raise ValueError(
                "provider instrument key maps to "
                "multiple instruments on same date"
            )

        provider_key_date[provider_key] = (
            listing.instrument_id
        )

    return AdmittedUSListingHistory(
        facts=tuple(admitted),
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


def resolve_us_listing_on_date(
    history: AdmittedUSListingHistory,
    *,
    instrument_id: UUID,
    market_date: date,
) -> USListingIdentity:
    """Resolve only an exact-dated identity fact.

    There is deliberately no latest-wins, forward-fill, backward-fill,
    ticker-continuity inference, or current-symbol projection.
    """

    if type(history) is not AdmittedUSListingHistory:
        raise ValueError(
            "AdmittedUSListingHistory required"
        )

    decision_market_date = (
        history.decision_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    if market_date > decision_market_date:
        raise ValueError(
            "market_date is after admitted decision horizon"
        )

    matches = [
        item.listing
        for item in history.facts
        if (
            item.listing.instrument_id
            == instrument_id
            and item.listing.effective_date
            == market_date
        )
    ]

    if not matches:
        raise ValueError(
            "no exact-dated US listing identity fact"
        )

    if len(matches) != 1:
        raise ValueError(
            "ambiguous exact-dated US listing identity"
        )

    return matches[0]


__all__ = [
    "US_LISTING_EVIDENCE_FIELDS",
    "HistoricalUSListingFact",
    "AdmittedUSListingHistory",
    "admit_us_listing_history",
    "resolve_us_listing_on_date",
]
