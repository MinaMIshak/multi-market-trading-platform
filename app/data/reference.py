"""Local, provider-neutral reference contracts. No provider adapter or inference.

Dates are exact evidence dates, never open-ended membership intervals. A complete
universe is evidence for one date only; action coverage is explicitly bounded.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class ReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("complete", "approved", mode="before", check_fields=False)
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("explicit boolean true required")
        return value


class UniverseMember(ReferenceModel):
    instrument_id: UUID
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
    eligible: bool = Field(strict=True)


class UniverseEvidence(ReferenceModel):
    contract: Literal["egx-universe-v1"]
    market: Literal["EGX"]
    effective_date: date
    published_at: AwareDatetime
    complete: Literal[True]
    members: tuple[UniverseMember, ...]

    @model_validator(mode="after")
    def unique_members(self):
        ids = [m.instrument_id for m in self.members]
        symbols = [m.symbol for m in self.members]
        if len(set(ids)) != len(ids) or len(set(symbols)) != len(symbols):
            raise ValueError("duplicate/conflicting universe identity")
        return self


class ActionEvidenceRow(ReferenceModel):
    event_id: str = Field(min_length=1)
    effective_date: date
    action_type: Literal[
        "SPLIT", "DIVIDEND", "RIGHTS", "CAPITAL_INCREASE",
        "CAPITAL_REDUCTION", "SYMBOL_CHANGE", "DELISTING", "OTHER",
    ]
    # Explicit transformation semantics. Legacy non-split rows remain
    # unsupported unless reviewed evidence states their split-adjustment effect.
    adjustment_effect: Literal["NONE", "SHARE_RATIO"] | None = None
    new_shares: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    old_shares: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    details: str = Field(min_length=1)

    @model_validator(mode="after")
    def split_terms(self):
        has_new = self.new_shares is not None
        has_old = self.old_shares is not None

        if has_new != has_old:
            raise ValueError(
                "share-ratio adjustment requires explicit new and old shares"
            )

        if self.action_type == "SPLIT":
            if not has_new:
                raise ValueError("split requires explicit new and old shares")
            if self.adjustment_effect not in (None, "SHARE_RATIO"):
                raise ValueError("split cannot declare a NONE adjustment effect")

        elif self.adjustment_effect == "SHARE_RATIO":
            if not has_new:
                raise ValueError(
                    "share-ratio adjustment requires explicit new and old shares"
                )

        elif has_new:
            raise ValueError(
                "share ratio requires SPLIT or explicit SHARE_RATIO adjustment"
            )

        return self


class ActionEvidence(ReferenceModel):
    contract: Literal["egx-actions-v1"]
    instrument_id: UUID
    coverage_start: date
    coverage_end: date
    published_at: AwareDatetime
    complete: Literal[True]
    actions: tuple[ActionEvidenceRow, ...]

    @model_validator(mode="after")
    def coverage(self):
        if self.coverage_end < self.coverage_start:
            raise ValueError("invalid action coverage")
        ids = [a.event_id for a in self.actions]
        dates = [a.effective_date for a in self.actions]
        if len(set(ids)) != len(ids) or len(set(dates)) != len(dates):
            raise ValueError("duplicate/ambiguous corporate actions")
        if any(not self.coverage_start <= d <= self.coverage_end for d in dates):
            raise ValueError("action outside declared coverage")
        return self


class ValidationEvidence(ReferenceModel):
    contract: Literal["egx-source-review-v1"]
    subject_ingestion_id: UUID
    subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_provider: str = Field(min_length=1)
    subject_contract: Literal[
        "egx-universe-v1", "egx-actions-v1", "egx-daily-semantic-v1",
    ]
    reviewed_at: AwareDatetime
    reviewer: str = Field(min_length=1)
    evidence_uri: str = Field(min_length=1)
    methodology: str = Field(min_length=1)
    approved: Literal[True]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value
