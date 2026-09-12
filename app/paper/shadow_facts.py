"""Fail-closed forward facts; admission creates no trigger or fill."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_ledger import LABEL, audit_candidate_event
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage, require_historical_evidence


SHA256_PATTERN = r"^[0-9a-f]{64}$"
NONBLANK_PATTERN = r"^\S(?:.*\S)?$"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError("datetime.timezone.utc required")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


SESSION_FIELDS = {"market", "market_date", "calendar_mic", "state", "opens_at", "closes_at"}
IDENTITY_FIELDS = {"instrument_id", "ticker", "listing_mic", "effective_from", "effective_through"}
ACTION_FIELDS = {"instrument_id", "coverage_from", "coverage_through", "status", "actions"}
BAR_FIELDS = {
    "instrument_id", "ticker", "market_date", "sequence", "interval_start",
    "interval_end", "available_at", "is_final", "price_basis", "open", "high",
    "low", "close", "volume", "source_row",
}


def _require_fields(package: HistoricalEvidencePackage, required: set[str]) -> None:
    if not required <= set(package.evidence.covered_fields):
        raise ValueError("evidence package does not cover required fact fields")


class _FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ForwardSessionFact(_FactModel):
    market: Literal["EGX", "US"]
    market_date: date
    calendar_mic: Literal["XCAI", "XNYS", "XNAS"]
    state: Literal["OPEN"]
    opens_at: datetime
    closes_at: datetime
    evidence_package_id: str = Field(pattern=SHA256_PATTERN)

    @field_validator("opens_at", "closes_at", mode="before")
    @classmethod
    def exact_times(cls, value):
        return _utc(value)

    @model_validator(mode="after")
    def coherent(self):
        if self.closes_at <= self.opens_at:
            raise ValueError("session close must follow open")
        return self


class ForwardIdentityFact(_FactModel):
    instrument_id: UUID
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
    listing_mic: Literal["XCAI", "XNYS", "XNAS"]
    effective_from: date
    effective_through: date
    evidence_package_id: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def coherent(self):
        if self.effective_through < self.effective_from:
            raise ValueError("identity interval is reversed")
        return self


class ForwardActionFact(_FactModel):
    event_id: str = Field(pattern=NONBLANK_PATTERN)
    action_type: Literal[
        "SPLIT", "CASH_DIVIDEND", "STOCK_DIVIDEND", "MERGER", "SPINOFF",
        "SYMBOL_CHANGE", "DELISTING", "RIGHTS", "OTHER",
    ]
    effective_date: date
    terms: str = Field(pattern=NONBLANK_PATTERN)


class ForwardActionCoverage(_FactModel):
    instrument_id: UUID
    coverage_from: date
    coverage_through: date
    status: Literal["COMPLETE"]
    actions: tuple[ForwardActionFact, ...]
    evidence_package_id: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def coherent(self):
        if self.coverage_through < self.coverage_from:
            raise ValueError("action coverage interval is reversed")
        if len({item.event_id for item in self.actions}) != len(self.actions):
            raise ValueError("duplicate corporate action event")
        if any(not self.coverage_from <= item.effective_date <= self.coverage_through
               for item in self.actions):
            raise ValueError("action falls outside coverage")
        return self


class ForwardRawBar(_FactModel):
    instrument_id: UUID
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
    market_date: date
    sequence: int = Field(ge=1)
    interval_start: datetime
    interval_end: datetime
    available_at: datetime
    is_final: Literal[True]
    price_basis: Literal["RAW_UNADJUSTED"]
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)
    source_row: str = Field(pattern=NONBLANK_PATTERN)
    evidence_package_id: str = Field(pattern=SHA256_PATTERN)

    @field_validator("interval_start", "interval_end", "available_at", mode="before")
    @classmethod
    def exact_times(cls, value):
        return _utc(value)

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def exact_prices(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("finite exact Decimal required")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.interval_end <= self.interval_start or self.available_at < self.interval_end:
            raise ValueError("invalid bar interval or availability")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.low > self.high:
            raise ValueError("incoherent raw OHLC")
        return self


class ForwardFactBundle(_FactModel):
    schema_version: Literal["shadow-forward-facts-v1"] = "shadow-forward-facts-v1"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = LABEL
    record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    candidate_id: str = Field(pattern=NONBLANK_PATTERN)
    session: ForwardSessionFact
    identity: ForwardIdentityFact
    action_coverage: ForwardActionCoverage
    bars: tuple[ForwardRawBar, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        session, identity = self.session, self.identity
        if not identity.effective_from <= session.market_date <= identity.effective_through:
            raise ValueError("identity does not cover session")
        if identity.listing_mic != session.calendar_mic:
            raise ValueError("identity/session MIC mismatch")
        if (self.action_coverage.instrument_id != identity.instrument_id
                or not self.action_coverage.coverage_from <= session.market_date
                <= self.action_coverage.coverage_through):
            raise ValueError("action coverage does not cover session identity")
        previous = None
        for expected, bar in enumerate(self.bars, 1):
            if (bar.sequence != expected or bar.instrument_id != identity.instrument_id
                    or bar.ticker != identity.ticker or bar.market_date != session.market_date):
                raise ValueError("bar identity/date/sequence mismatch")
            if bar.interval_start < session.opens_at or bar.interval_end > session.closes_at:
                raise ValueError("bar outside authenticated session")
            if previous is None and bar.interval_start != session.opens_at:
                raise ValueError("bars must originate at session open")
            if previous is not None and bar.interval_start != previous.interval_end:
                raise ValueError("bar chronology has a gap or overlap")
            previous = bar
        return self


def append_forward_fact_event(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
) -> Path:
    """Admit and preserve exact forward facts without inferring execution."""
    if type(facts) is not ForwardFactBundle or type(fact_packages) is not tuple:
        raise ValueError("exact fact bundle and package tuple required")
    facts = ForwardFactBundle.model_validate(facts.model_dump(mode="python"))
    candidate_event = audit_candidate_event(directory, watchlist, watchlist_packages)
    candidates = {item.candidate_id: item for item in watchlist.candidates}
    candidate = candidates.get(facts.candidate_id)
    if candidate is None or candidate.identity_status != "KNOWN":
        raise ValueError("facts require an exact known candidate")
    if (facts.record_id != watchlist.record_id or facts.session.market != watchlist.session.market
            or facts.session.market_date != watchlist.session.market_date
            or facts.session.calendar_mic != watchlist.session.calendar_mic
            or facts.session.opens_at != watchlist.session.opens_at
            or facts.identity.instrument_id != candidate.instrument_id
            or facts.identity.ticker != candidate.ticker):
        raise ValueError("fact bundle does not bind candidate event")

    observed_through = max(bar.available_at for bar in facts.bars)
    recorded_at = _now()
    if not observed_through <= recorded_at:
        raise ValueError("facts are not yet observable")
    package_ids = {
        facts.session.evidence_package_id, facts.identity.evidence_package_id,
        facts.action_coverage.evidence_package_id,
        *(bar.evidence_package_id for bar in facts.bars),
    }
    supplied = {}
    for package in fact_packages:
        if type(package) is not HistoricalEvidencePackage or package.identity in supplied:
            raise ValueError("exact unique fact evidence packages required")
        supplied[package.identity] = package
    if set(supplied) != package_ids:
        raise ValueError("fact evidence packages must match references exactly")
    for identity, package in supplied.items():
        require_historical_evidence(package, decision_at=observed_through,
                                    research_built_at=recorded_at)
    _require_fields(supplied[facts.session.evidence_package_id], SESSION_FIELDS)
    _require_fields(supplied[facts.identity.evidence_package_id], IDENTITY_FIELDS)
    _require_fields(supplied[facts.action_coverage.evidence_package_id], ACTION_FIELDS)
    for bar in facts.bars:
        _require_fields(supplied[bar.evidence_package_id], BAR_FIELDS)
        availability = supplied[bar.evidence_package_id].evidence.availability
        latest = availability.exact_at if availability.kind == "EXACT" else availability.end
        if latest != bar.available_at:
            raise ValueError("bar availability must bind its evidence package")

    facts_json = facts.model_dump(mode="json")
    basis = {
        "schema_version": "shadow-forward-fact-event-v1", "label": LABEL,
        "event_type": "FORWARD_FACTS_ADMITTED", "scoring": "NOT SCORED",
        "execution_status": "NO TRIGGER OR FILL INFERENCE",
        "candidate_event_id": candidate_event["event_id"],
        "candidate_event_sha256": hashlib.sha256(_canonical(candidate_event)).hexdigest(),
        "fact_package_ids": sorted(package_ids), "facts": facts_json,
    }
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()
    payload = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    result = _publish_once(Path(directory) / "forward-facts" / f"{event_id}.json", payload)
    if _now() < recorded_at:
        result.unlink()
        raise ValueError("clock rollback during fact publication")
    return result


def audit_forward_fact_event(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Read and verify a fact event against exact caller-supplied inputs."""
    if type(facts) is not ForwardFactBundle or type(fact_packages) is not tuple:
        raise ValueError("exact fact bundle and package tuple required")
    facts = ForwardFactBundle.model_validate(facts.model_dump(mode="python"))
    candidate_event = audit_candidate_event(directory, watchlist, watchlist_packages)
    candidates = {item.candidate_id: item for item in watchlist.candidates}
    candidate = candidates.get(facts.candidate_id)
    if candidate is None or candidate.identity_status != "KNOWN" or (
        facts.record_id != watchlist.record_id
        or facts.session.market != watchlist.session.market
        or facts.session.market_date != watchlist.session.market_date
        or facts.session.calendar_mic != watchlist.session.calendar_mic
        or facts.session.opens_at != watchlist.session.opens_at
        or facts.identity.instrument_id != candidate.instrument_id
        or facts.identity.ticker != candidate.ticker
    ):
        raise ValueError("fact bundle does not bind candidate event")
    package_ids = {
        facts.session.evidence_package_id, facts.identity.evidence_package_id,
        facts.action_coverage.evidence_package_id,
        *(bar.evidence_package_id for bar in facts.bars),
    }
    supplied = {package.identity: package for package in fact_packages
                if type(package) is HistoricalEvidencePackage}
    if len(supplied) != len(fact_packages) or set(supplied) != package_ids:
        raise ValueError("fact evidence packages must match references exactly")
    basis = {
        "schema_version": "shadow-forward-fact-event-v1", "label": LABEL,
        "event_type": "FORWARD_FACTS_ADMITTED", "scoring": "NOT SCORED",
        "execution_status": "NO TRIGGER OR FILL INFERENCE",
        "candidate_event_id": candidate_event["event_id"],
        "candidate_event_sha256": hashlib.sha256(_canonical(candidate_event)).hexdigest(),
        "fact_package_ids": sorted(package_ids), "facts": facts.model_dump(mode="json"),
    }
    event_id = hashlib.sha256(_canonical(basis)).hexdigest()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate fact-event field")
            result[key] = value
        return result

    event = json.loads(
        (Path(directory) / "forward-facts" / f"{event_id}.json").read_bytes(),
        object_pairs_hook=unique_object,
    )
    if type(event) is not dict or set(event) != set(basis) | {"event_id", "recorded_at"}:
        raise ValueError("unexpected fact-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    observed_through = max(bar.available_at for bar in facts.bars)
    if not observed_through <= recorded_at <= _now():
        raise ValueError("invalid fact-event clock ordering")
    for package in supplied.values():
        require_historical_evidence(package, decision_at=observed_through,
                                    research_built_at=recorded_at)
    _require_fields(supplied[facts.session.evidence_package_id], SESSION_FIELDS)
    _require_fields(supplied[facts.identity.evidence_package_id], IDENTITY_FIELDS)
    _require_fields(supplied[facts.action_coverage.evidence_package_id], ACTION_FIELDS)
    for bar in facts.bars:
        _require_fields(supplied[bar.evidence_package_id], BAR_FIELDS)
        availability = supplied[bar.evidence_package_id].evidence.availability
        latest = availability.exact_at if availability.kind == "EXACT" else availability.end
        if latest != bar.available_at:
            raise ValueError("bar availability must bind its evidence package")
    expected = basis | {"event_id": event_id, "recorded_at": recorded_at.isoformat()}
    if event != expected:
        raise ValueError("fact event does not bind admitted facts")
    return event
