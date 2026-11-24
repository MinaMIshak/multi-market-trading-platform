"""Pure observed changes between two dated egx-universe-v1 evidence documents.

This module is pure:
- no database access,
- no network access,
- no file writes,
- no source-review approval.

It reports what differs between two exact dated universe documents, keyed by
durable canonical instrument identity (instrument_id), never by symbol alone:

- ADDED: identity present only in the later document,
- ABSENT: identity present only in the earlier document,
- SYMBOL_CHANGED: same identity, different symbol,
- ELIGIBILITY_CHANGED: same identity, different eligible flag,
- SYMBOL_REASSIGNED: same symbol bound to different identities.

Interpretation is deliberately NOT established. An identity appearing is not
proof of a new listing or IPO; an identity disappearing is not proof of a
delisting or suspension; a changed symbol is not proof of a reviewed symbol
change corporate action. Dates between the two effective dates were not
observed, so intermediate changes may be missing. Source review of either
document is not checked here; PIT consumption still requires the existing
independent egx-source-review-v1 control.
"""

from __future__ import annotations

import hashlib
import json

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.data.reference import UniverseEvidence


CONTRACT_VERSION = "egx-universe-changes-v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UniverseIdentityChange(_Model):
    instrument_id: UUID
    symbol: str = Field(min_length=1)
    eligible: bool


class UniverseSymbolChange(_Model):
    instrument_id: UUID
    previous_symbol: str = Field(min_length=1)
    current_symbol: str = Field(min_length=1)


class UniverseEligibilityChange(_Model):
    instrument_id: UUID
    symbol: str = Field(min_length=1)
    previous_eligible: bool
    current_eligible: bool


class UniverseSymbolReassignment(_Model):
    symbol: str = Field(min_length=1)
    previous_instrument_id: UUID
    current_instrument_id: UUID


class EGXUniverseChanges(_Model):
    contract_version: Literal["egx-universe-changes-v1"] = CONTRACT_VERSION
    market: Literal["EGX"] = "EGX"
    previous_effective_date: str
    current_effective_date: str
    previous_universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_member_count: int = Field(ge=1)
    current_member_count: int = Field(ge=1)
    added: tuple[UniverseIdentityChange, ...]
    absent: tuple[UniverseIdentityChange, ...]
    symbol_changed: tuple[UniverseSymbolChange, ...]
    eligibility_changed: tuple[UniverseEligibilityChange, ...]
    symbol_reassigned: tuple[UniverseSymbolReassignment, ...]
    unchanged_count: int = Field(ge=0)
    identity_basis: Literal["CANONICAL_INSTRUMENT_ID"] = (
        "CANONICAL_INSTRUMENT_ID"
    )
    listing_interpretation: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    delisting_interpretation: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    symbol_change_interpretation: Literal["NOT_ESTABLISHED"] = (
        "NOT_ESTABLISHED"
    )
    intermediate_dates: Literal["NOT_OBSERVED"] = "NOT_OBSERVED"
    source_review: Literal["NOT_CHECKED"] = "NOT_CHECKED"
    trading_effect: Literal["NONE"] = "NONE"
    mode: Literal["SHADOW"] = "SHADOW"
    live_money: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self):
        if self.previous_effective_date >= self.current_effective_date:
            raise ValueError("previous effective date must precede current")
        if self.previous_universe_sha256 == self.current_universe_sha256:
            raise ValueError("identical universe documents")
        # Identities present in both documents.
        shared = self.previous_member_count - len(self.absent)
        if shared != self.current_member_count - len(self.added) or shared < 0:
            raise ValueError("inconsistent member counts")
        changed_ids = {item.instrument_id for item in self.symbol_changed} | {
            item.instrument_id for item in self.eligibility_changed
        }
        if self.unchanged_count != shared - len(changed_ids):
            raise ValueError("inconsistent unchanged count")
        return self


def universe_sha256(universe: UniverseEvidence) -> str:
    """SHA-256 of the canonical bytes the EGX universe publisher retains."""

    payload = json.dumps(
        universe.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _revalidated(value, name: str) -> UniverseEvidence:
    if type(value) is not UniverseEvidence:
        raise TypeError(f"exact UniverseEvidence required for {name}")
    # Re-run validation so a model_construct bypass cannot smuggle in
    # duplicate identities or symbols.
    return UniverseEvidence.model_validate(value.model_dump())


def compare_egx_universes(
    previous: UniverseEvidence,
    current: UniverseEvidence,
) -> EGXUniverseChanges:
    """Report observed identity changes from ``previous`` to ``current``.

    Fails closed unless both are exact, valid egx-universe-v1 documents with
    strictly increasing effective dates and different canonical bytes.
    Output ordering is deterministic and independent of member order.
    """

    previous = _revalidated(previous, "previous")
    current = _revalidated(current, "current")

    if previous.effective_date >= current.effective_date:
        raise ValueError("previous effective date must precede current")

    previous_sha = universe_sha256(previous)
    current_sha = universe_sha256(current)

    if previous_sha == current_sha:
        raise ValueError("identical universe documents")

    before = {member.instrument_id: member for member in previous.members}
    after = {member.instrument_id: member for member in current.members}

    added = tuple(
        UniverseIdentityChange(
            instrument_id=member.instrument_id,
            symbol=member.symbol,
            eligible=member.eligible,
        )
        for member in sorted(
            (after[key] for key in after.keys() - before.keys()),
            key=lambda item: (item.symbol, str(item.instrument_id)),
        )
    )

    absent = tuple(
        UniverseIdentityChange(
            instrument_id=member.instrument_id,
            symbol=member.symbol,
            eligible=member.eligible,
        )
        for member in sorted(
            (before[key] for key in before.keys() - after.keys()),
            key=lambda item: (item.symbol, str(item.instrument_id)),
        )
    )

    shared = sorted(
        before.keys() & after.keys(),
        key=lambda key: (after[key].symbol, str(key)),
    )

    symbol_changed = tuple(
        UniverseSymbolChange(
            instrument_id=key,
            previous_symbol=before[key].symbol,
            current_symbol=after[key].symbol,
        )
        for key in shared
        if before[key].symbol != after[key].symbol
    )

    eligibility_changed = tuple(
        UniverseEligibilityChange(
            instrument_id=key,
            symbol=after[key].symbol,
            previous_eligible=before[key].eligible,
            current_eligible=after[key].eligible,
        )
        for key in shared
        if before[key].eligible is not after[key].eligible
    )

    changed_ids = {item.instrument_id for item in symbol_changed} | {
        item.instrument_id for item in eligibility_changed
    }

    before_by_symbol = {
        member.symbol: member.instrument_id for member in previous.members
    }
    after_by_symbol = {
        member.symbol: member.instrument_id for member in current.members
    }

    symbol_reassigned = tuple(
        UniverseSymbolReassignment(
            symbol=symbol,
            previous_instrument_id=before_by_symbol[symbol],
            current_instrument_id=after_by_symbol[symbol],
        )
        for symbol in sorted(before_by_symbol.keys() & after_by_symbol.keys())
        if before_by_symbol[symbol] != after_by_symbol[symbol]
    )

    return EGXUniverseChanges(
        previous_effective_date=previous.effective_date.isoformat(),
        current_effective_date=current.effective_date.isoformat(),
        previous_universe_sha256=previous_sha,
        current_universe_sha256=current_sha,
        previous_member_count=len(previous.members),
        current_member_count=len(current.members),
        added=added,
        absent=absent,
        symbol_changed=symbol_changed,
        eligibility_changed=eligibility_changed,
        symbol_reassigned=symbol_reassigned,
        unchanged_count=len(shared) - len(changed_ids),
    )
