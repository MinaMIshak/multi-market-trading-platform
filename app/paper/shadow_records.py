"""Strict forward shadow records; preservation is never execution admission."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_freeze import freeze_document


SHA256_PATTERN = r"^[0-9a-f]{64}$"
NONBLANK_PATTERN = r"^\S(?:.*\S)?$"


def _exact_utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("datetime.timezone.utc required")
    return value


class ShadowRecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShadowEvidenceReference(ShadowRecordModel):
    evidence_id: str = Field(pattern=SHA256_PATTERN)
    source_authority: str = Field(pattern=NONBLANK_PATTERN)
    source_locator: str = Field(pattern=NONBLANK_PATTERN)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    available_at: datetime

    @field_validator("available_at", mode="before")
    @classmethod
    def exact_time(cls, value):
        return _exact_utc(value)


class ShadowSession(ShadowRecordModel):
    market: Literal["EGX", "US"]
    market_date: date
    calendar_mic: Literal["XCAI", "XNYS", "XNAS"]
    state: Literal["OPEN"]
    opens_at: datetime
    decision_cutoff: datetime
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("opens_at", "decision_cutoff", mode="before")
    @classmethod
    def exact_times(cls, value):
        return _exact_utc(value)

    @model_validator(mode="after")
    def coherent_session(self):
        if (self.market == "EGX") != (self.calendar_mic == "XCAI"):
            raise ValueError("market/calendar MIC mismatch")
        if self.decision_cutoff > self.opens_at:
            raise ValueError("decision cutoff cannot follow session open")
        zone = ZoneInfo("Africa/Cairo" if self.market == "EGX" else "America/New_York")
        if self.opens_at.astimezone(zone).date() != self.market_date:
            raise ValueError("session open does not match local market date")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate session evidence identity")
        return self


class ShadowCandidate(ShadowRecordModel):
    candidate_id: str = Field(pattern=NONBLANK_PATTERN)
    decision_status: Literal["BUY_CANDIDATE", "WATCH", "AVOID"]
    identity_status: Literal["KNOWN", "UNKNOWN"]
    instrument_id: UUID | None
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
    thesis: str = Field(pattern=NONBLANK_PATTERN)
    context: tuple[str, ...]
    technical_setup: str = Field(pattern=NONBLANK_PATTERN)
    entry_condition: str = Field(pattern=NONBLANK_PATTERN)
    entry_rule: Literal["LONG_ENTRY_ZONE_TOUCH_V1"]
    entry_low: Decimal | None = Field(default=None, gt=0)
    entry_high: Decimal | None = Field(default=None, gt=0)
    stop: Decimal | None = Field(default=None, gt=0)
    targets: tuple[Decimal, ...]
    expected_holding_window: Literal["NOT_YET_VALIDATED"]
    confidence: Literal["EXPERIMENTAL"]
    probability: Literal["NOT_YET_VALIDATED"]
    liquidity_status: Literal["PASS", "BLOCKED", "UNKNOWN"]
    liquidity_reason: str = Field(pattern=NONBLANK_PATTERN)
    paper_quantity: int = Field(ge=0)
    paper_risk_status: Literal["APPROVED", "BLOCKED", "NOT_EVALUATED"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("entry_low", "entry_high", "stop", mode="before")
    @classmethod
    def finite_optional_decimal(cls, value):
        if value is not None and (type(value) is not Decimal or not value.is_finite()):
            raise ValueError("finite exact Decimal required")
        return value

    @field_validator("targets", mode="before")
    @classmethod
    def finite_targets(cls, value):
        if type(value) is not tuple or any(
            type(item) is not Decimal or not item.is_finite() or item <= 0 for item in value
        ):
            raise ValueError("tuple of positive finite exact Decimal targets required")
        return value

    @model_validator(mode="after")
    def coherent_candidate(self):
        if (self.identity_status == "KNOWN") != (self.instrument_id is not None):
            raise ValueError("stable identity status mismatch")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate candidate evidence identity")
        priced = (self.entry_low, self.entry_high, self.stop)
        if self.decision_status == "BUY_CANDIDATE":
            if any(value is None for value in priced) or not self.targets:
                raise ValueError("BUY_CANDIDATE requires entry zone, stop and target")
            assert self.entry_low is not None and self.entry_high is not None and self.stop is not None
            if not self.stop < self.entry_low <= self.entry_high < self.targets[0]:
                raise ValueError("invalid LONG candidate geometry")
            if any(right <= left for left, right in zip(self.targets, self.targets[1:])):
                raise ValueError("LONG targets must strictly increase")
            if self.liquidity_status != "PASS" or self.paper_risk_status != "APPROVED" or self.paper_quantity <= 0:
                raise ValueError("BUY_CANDIDATE requires passed liquidity and paper-risk size")
        elif self.paper_quantity != 0 or self.paper_risk_status == "APPROVED":
            raise ValueError("WATCH/AVOID cannot reserve paper quantity")
        return self


class ShadowWatchlist(ShadowRecordModel):
    schema_version: Literal["shadow-watchlist-v2"] = "shadow-watchlist-v2"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = "EXPERIMENTAL / PAPER ONLY"
    record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    generated_at: datetime
    information_cutoff: datetime
    session: ShadowSession
    evidence: tuple[ShadowEvidenceReference, ...] = Field(min_length=1)
    candidates: tuple[ShadowCandidate, ...]

    @field_validator("generated_at", "information_cutoff", mode="before")
    @classmethod
    def exact_times(cls, value):
        return _exact_utc(value)

    @model_validator(mode="after")
    def evidence_and_clocks(self):
        if not self.information_cutoff <= self.generated_at < self.session.decision_cutoff:
            raise ValueError("watchlist generated outside cutoff window")
        evidence = {item.evidence_id: item for item in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate evidence identity")
        referenced = set(self.session.evidence_ids)
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("duplicate candidate identity")
        for candidate in self.candidates:
            referenced.update(candidate.evidence_ids)
        if referenced != set(evidence):
            raise ValueError("evidence must be referenced exactly")
        if any(item.available_at > self.information_cutoff for item in self.evidence):
            raise ValueError("post-cutoff evidence forbidden")
        return self


def freeze_watchlist(directory: Path, watchlist: ShadowWatchlist) -> Path:
    """Validate, serialize deterministically, and preserve a watchlist once."""
    if type(watchlist) is not ShadowWatchlist:
        raise ValueError("exact ShadowWatchlist required")
    watchlist = ShadowWatchlist.model_validate(watchlist.model_dump(mode="python"))
    # This is an additional local-clock consistency check, not independent
    # timestamp attestation. freeze_document repeats cutoff/rollback checks.
    from app.paper import shadow_freeze
    if watchlist.generated_at > shadow_freeze._now():
        raise ValueError("future generated_at forbidden")
    document = json.dumps(
        watchlist.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return freeze_document(
        directory, record_id=watchlist.record_id, market=watchlist.session.market,
        information_cutoff=watchlist.information_cutoff,
        decision_cutoff=watchlist.session.decision_cutoff, document=document,
    )
