"""Observed changes between two independently reviewed dated EGX universes.

This module composes two existing, unchanged controls:

- ``ReferenceRepository.universe`` selects exactly one dated
  egx-universe-v1 per market date at an explicit aware ``as_of``, ignoring
  evidence received, completed or published after ``as_of`` and failing
  closed unless an independent egx-source-review-v1 for that exact retained
  artifact is available at ``as_of``;
- ``compare_egx_universes`` reports identity changes between the two
  selected documents.

It never selects "latest", never merges sources, never creates or approves
evidence, never publishes and never mutates the security master. It performs
no network access and no file writes. The only durable side effect is the
unchanged reader's own idempotent data-quality issue record when a selection
is refused.

Interpretation is deliberately NOT established: the embedded
egx-universe-changes-v1 result keeps its fixed NOT_ESTABLISHED listing,
delisting and symbol-change labels and its own source_review NOT_CHECKED
label (that pure function does not see reviews). Review status is recorded
only at this composition layer, as the exact review ingestion identities the
PIT reader accepted at ``as_of``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.egx_universe_changes import (
    EGXUniverseChanges,
    compare_egx_universes,
)
from app.data.reference import UniverseEvidence, aware
from app.storage.reference_repository import ReferenceRepository


CONTRACT_VERSION = "egx-reviewed-universe-changes-v1"

SELECTION_RULE = "EXACT_DATED_REVIEWED_AT_AS_OF_V1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewedUniverseSelection(_Model):
    effective_date: str
    ingestion_id: UUID
    provider: str = Field(min_length=1)
    retained_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # True only when the retained raw bytes are exactly the canonical bytes
    # the EGX universe publisher writes (so retained_sha256 equals the
    # embedded change report's universe digest). Fixture or third-party
    # producers may retain non-canonical but valid JSON.
    retained_bytes_canonical: bool
    review_ingestion_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _reviews(self):
        if list(self.review_ingestion_ids) != sorted(
            set(self.review_ingestion_ids), key=str
        ):
            raise ValueError("review identities must be unique and sorted")
        if self.ingestion_id in self.review_ingestion_ids:
            raise ValueError("universe cannot review itself")
        return self


class EGXReviewedUniverseChanges(_Model):
    contract_version: Literal["egx-reviewed-universe-changes-v1"] = (
        CONTRACT_VERSION
    )
    market: Literal["EGX"] = "EGX"
    as_of: str
    selection_rule: Literal["EXACT_DATED_REVIEWED_AT_AS_OF_V1"] = (
        SELECTION_RULE
    )
    previous: ReviewedUniverseSelection
    current: ReviewedUniverseSelection
    changes: EGXUniverseChanges
    source_review: Literal["REVIEWED_AT_AS_OF"] = "REVIEWED_AT_AS_OF"
    listing_interpretation: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    delisting_interpretation: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    symbol_change_interpretation: Literal["NOT_ESTABLISHED"] = (
        "NOT_ESTABLISHED"
    )
    intermediate_dates: Literal["NOT_OBSERVED"] = "NOT_OBSERVED"
    trading_effect: Literal["NONE"] = "NONE"
    mode: Literal["SHADOW"] = "SHADOW"
    live_money: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self):
        if (
            self.previous.effective_date
            != self.changes.previous_effective_date
            or self.current.effective_date
            != self.changes.current_effective_date
        ):
            raise ValueError("selection dates do not match changes")
        if self.previous.ingestion_id == self.current.ingestion_id:
            raise ValueError("one artifact selected for both dates")
        if self.previous.retained_bytes_canonical is not (
            self.previous.retained_sha256
            == self.changes.previous_universe_sha256
        ) or self.current.retained_bytes_canonical is not (
            self.current.retained_sha256
            == self.changes.current_universe_sha256
        ):
            raise ValueError("inconsistent canonical byte flag")
        return self


def _select(
    references: ReferenceRepository,
    market_date: date,
    as_of: datetime,
) -> tuple[dict, UniverseEvidence]:
    row, document, reviews = references.universe(
        market_date=market_date,
        as_of=as_of,
    )
    if type(document) is not UniverseEvidence:
        raise ValueError("selected evidence is not egx-universe-v1")
    if document.effective_date != market_date:
        raise ValueError("selected universe date mismatch")
    manifest = references.ingestions.get_manifest_by_raw_path(
        row["raw_path"]
    )
    if manifest is None or str(manifest.ingestion_id) != str(
        row["ingestion_id"]
    ):
        raise ValueError("selected universe manifest mismatch")
    selection = dict(
        effective_date=market_date.isoformat(),
        ingestion_id=manifest.ingestion_id,
        provider=manifest.provider,
        retained_sha256=manifest.sha256,
        review_ingestion_ids=tuple(
            sorted({UUID(str(item)) for item in reviews}, key=str)
        ),
    )
    return selection, document


def load_reviewed_egx_universe_changes(
    references: ReferenceRepository,
    *,
    previous_date: date,
    current_date: date,
    as_of: datetime,
) -> EGXReviewedUniverseChanges:
    """Compare the reviewed universes for two exact dates at ``as_of``.

    Fails closed (ValueError/TypeError) unless both dates are exact,
    strictly increasing, not after ``as_of`` in Africa/Cairo, and each has
    exactly one independently reviewed egx-universe-v1 available at
    ``as_of``.
    """

    if not isinstance(references, ReferenceRepository):
        raise TypeError("ReferenceRepository required")
    if type(previous_date) is not date or type(current_date) is not date:
        raise TypeError("exact market dates required")
    if type(as_of) is not datetime:
        raise TypeError("exact as_of datetime required")
    aware(as_of)
    if previous_date >= current_date:
        raise ValueError("previous date must precede current date")
    # Same rule as the unchanged reader, checked before any read.
    if current_date > as_of.astimezone(ZoneInfo("Africa/Cairo")).date():
        raise ValueError("future universe date")

    previous, previous_document = _select(references, previous_date, as_of)
    current, current_document = _select(references, current_date, as_of)

    changes = compare_egx_universes(previous_document, current_document)

    previous["retained_bytes_canonical"] = (
        previous["retained_sha256"] == changes.previous_universe_sha256
    )
    current["retained_bytes_canonical"] = (
        current["retained_sha256"] == changes.current_universe_sha256
    )

    return EGXReviewedUniverseChanges(
        as_of=as_of.isoformat(),
        previous=ReviewedUniverseSelection(**previous),
        current=ReviewedUniverseSelection(**current),
        changes=changes,
    )
