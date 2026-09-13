"""Authenticated later-session facts for an existing paper position; no execution."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_facts import (
    ACTION_FIELDS, BAR_FIELDS, IDENTITY_FIELDS, SESSION_FIELDS, TRADING_STATUS_FIELDS,
    ForwardActionCoverage, ForwardIdentityFact, ForwardRawBar, ForwardTradingStatusFact,
    _canonical, _now, _require_fields, _utc,
)
from app.paper.shadow_fills import ShadowFillPolicy
from app.paper.shadow_ledger import LABEL
from app.paper.shadow_positions import audit_position_open_event
from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage, require_historical_evidence


class ContinuationCalendarDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    market: Literal["EGX", "US"]
    market_date: date
    calendar_mic: Literal["XCAI", "XNYS", "XNAS"]
    state: Literal["OPEN", "CLOSED"]
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    evidence_package_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent(self):
        if self.state == "CLOSED":
            if self.opens_at is not None or self.closes_at is not None:
                raise ValueError("closed calendar day cannot have session hours")
        else:
            if self.opens_at is None or self.closes_at is None:
                raise ValueError("open calendar day requires exact session hours")
            _utc(self.opens_at)
            _utc(self.closes_at)
            if self.closes_at <= self.opens_at:
                raise ValueError("session close must follow open")
        return self


class ForwardContinuationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-forward-continuation-v1"] = "shadow-forward-continuation-v1"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = LABEL
    continuation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    calendar_days: tuple[ContinuationCalendarDay, ...] = Field(min_length=1)
    identity: ForwardIdentityFact
    action_coverage: ForwardActionCoverage
    trading_status: ForwardTradingStatusFact
    bars: tuple[ForwardRawBar, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        days = self.calendar_days
        for previous, current in zip(days, days[1:]):
            if (current.market_date - previous.market_date).days != 1:
                raise ValueError("continuation calendar must cover every intervening date")
        target = days[-1]
        if target.state != "OPEN":
            raise ValueError("continuation observations require a final open session")
        if any(day.state != "CLOSED" for day in days[:-1]):
            raise ValueError("continuation cannot skip an intervening open session")
        if any(day.market != target.market or day.calendar_mic != target.calendar_mic for day in days):
            raise ValueError("continuation calendar market/MIC mismatch")
        identity = self.identity
        if (identity.listing_mic != target.calendar_mic
                or not identity.effective_from <= target.market_date <= identity.effective_through):
            raise ValueError("identity does not cover continuation session")
        if (self.action_coverage.instrument_id != identity.instrument_id
                or not self.action_coverage.coverage_from <= days[0].market_date
                or self.action_coverage.coverage_through < target.market_date):
            raise ValueError("actions do not cover complete continuation interval")
        status = self.trading_status
        if (status.instrument_id != identity.instrument_id
                or status.listing_mic != identity.listing_mic
                or status.coverage_start > self.bars[0].interval_start
                or status.coverage_end < self.bars[-1].interval_end):
            raise ValueError("issue-specific status does not cover continuation bars")
        previous_bar = None
        for expected, bar in enumerate(self.bars, 1):
            if (bar.sequence != expected or bar.instrument_id != identity.instrument_id
                    or bar.ticker != identity.ticker or bar.market_date != target.market_date):
                raise ValueError("continuation bar identity/date/sequence mismatch")
            if bar.interval_start < target.opens_at or bar.interval_end > target.closes_at:
                raise ValueError("continuation bar outside authenticated session")
            if previous_bar is None and bar.interval_start != target.opens_at:
                raise ValueError("continuation bars must originate at session open")
            if previous_bar is not None and bar.interval_start != previous_bar.interval_end:
                raise ValueError("continuation bar chronology has a gap or overlap")
            previous_bar = bar
        return self


def _packages(facts: ForwardContinuationBundle,
              supplied: tuple[HistoricalEvidencePackage, ...], observed_through: datetime,
              recorded_at: datetime) -> list[str]:
    package_ids = {
        *(day.evidence_package_id for day in facts.calendar_days),
        facts.identity.evidence_package_id, facts.action_coverage.evidence_package_id,
        facts.trading_status.evidence_package_id, *(bar.evidence_package_id for bar in facts.bars),
    }
    found = {}
    for package in supplied:
        if type(package) is not HistoricalEvidencePackage or package.identity in found:
            raise ValueError("exact unique continuation evidence packages required")
        found[package.identity] = package
    if set(found) != package_ids:
        raise ValueError("continuation evidence packages must match references exactly")
    for package in found.values():
        require_historical_evidence(package, decision_at=observed_through,
                                    research_built_at=recorded_at)
    for day in facts.calendar_days:
        _require_fields(found[day.evidence_package_id], SESSION_FIELDS)
    _require_fields(found[facts.identity.evidence_package_id], IDENTITY_FIELDS)
    _require_fields(found[facts.action_coverage.evidence_package_id], ACTION_FIELDS)
    _require_fields(found[facts.trading_status.evidence_package_id], TRADING_STATUS_FIELDS)
    for bar in facts.bars:
        package = found[bar.evidence_package_id]
        _require_fields(package, BAR_FIELDS)
        availability = package.evidence.availability
        latest = availability.exact_at if availability.kind == "EXACT" else availability.end
        if latest != bar.available_at:
            raise ValueError("bar availability must bind its evidence package")
    return sorted(package_ids)


def _basis(directory: Path, watchlist: ShadowWatchlist,
           watchlist_packages: tuple[HistoricalEvidencePackage, ...], original_facts,
           original_fact_packages: tuple[HistoricalEvidencePackage, ...],
           fill_policy: ShadowFillPolicy,
           fill_packages: tuple[HistoricalEvidencePackage, ...],
           facts: ForwardContinuationBundle,
           fact_packages: tuple[HistoricalEvidencePackage, ...], recorded_at: datetime) -> dict:
    position = audit_position_open_event(directory, watchlist, watchlist_packages, original_facts,
                                         original_fact_packages, fill_policy, fill_packages)
    facts = ForwardContinuationBundle.model_validate(facts.model_dump(mode="python"))
    original_date = original_facts.session.market_date
    if ((facts.calendar_days[0].market_date - original_date).days != 1
            or facts.calendar_days[-1].market != original_facts.session.market
            or facts.identity.instrument_id != original_facts.identity.instrument_id
            or facts.identity.ticker != original_facts.identity.ticker):
        raise ValueError("continuation does not bind the original position and next calendar date")
    observed_through = max(bar.available_at for bar in facts.bars)
    if max(observed_through, _utc(datetime.fromisoformat(position["recorded_at"]))) > recorded_at:
        raise ValueError("continuation publication precedes authenticated inputs")
    package_ids = _packages(facts, fact_packages, observed_through, recorded_at)
    return {
        "schema_version": "shadow-forward-continuation-event-v1", "label": LABEL,
        "event_type": "POSITION_CONTINUATION_FACTS_ADMITTED", "scoring": "NOT SCORED",
        "execution_status": "NO EXIT OR PNL INFERENCE",
        "position_event_id": position["event_id"],
        "position_event_sha256": hashlib.sha256(_canonical(position)).hexdigest(),
        "continuation_package_ids": package_ids, "facts": facts.model_dump(mode="json"),
    }


def continuation_event_path(directory: Path, position_event_id: str,
                            market_date: date) -> Path:
    if (type(position_event_id) is not str or len(position_event_id) != 64
            or any(char not in "0123456789abcdef" for char in position_event_id)):
        raise ValueError("invalid position event id")
    if type(market_date) is not date:
        raise ValueError("exact continuation market date required")
    return Path(directory) / "continuation-facts" / f"{position_event_id}.{market_date.isoformat()}.json"


def append_continuation_event(directory: Path, watchlist: ShadowWatchlist,
                              watchlist_packages, original_facts, original_fact_packages,
                              fill_policy: ShadowFillPolicy, fill_packages,
                              facts: ForwardContinuationBundle, fact_packages) -> Path:
    recorded_at = _utc(_now())
    basis = _basis(directory, watchlist, watchlist_packages, original_facts,
                   original_fact_packages, fill_policy, fill_packages, facts, fact_packages,
                   recorded_at)
    payload = basis | {"event_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                       "recorded_at": recorded_at.isoformat()}
    path = _publish_once(continuation_event_path(
        directory, basis["position_event_id"], facts.calendar_days[-1].market_date), payload)
    if _now() < recorded_at:
        path.unlink()
        raise ValueError("clock rollback during continuation publication")
    return path


def audit_continuation_event(directory: Path, watchlist: ShadowWatchlist,
                             watchlist_packages, original_facts, original_fact_packages,
                             fill_policy: ShadowFillPolicy, fill_packages,
                             facts: ForwardContinuationBundle, fact_packages) -> dict:
    path = continuation_event_path(directory, audit_position_open_event(
        directory, watchlist, watchlist_packages, original_facts, original_fact_packages,
        fill_policy, fill_packages)["event_id"], facts.calendar_days[-1].market_date)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate continuation-event field")
            result[key] = value
        return result
    event = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if type(event) is not dict or "recorded_at" not in event:
        raise ValueError("unexpected continuation-event fields")
    recorded_at = _utc(datetime.fromisoformat(event["recorded_at"]))
    basis = _basis(directory, watchlist, watchlist_packages, original_facts,
                   original_fact_packages, fill_policy, fill_packages, facts, fact_packages,
                   recorded_at)
    expected = basis | {"event_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                        "recorded_at": recorded_at.isoformat()}
    if set(event) != set(expected) or event != expected or recorded_at > _now():
        raise ValueError("continuation event does not bind authenticated inputs")
    return event
