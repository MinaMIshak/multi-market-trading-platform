"""US5A retrospective US universe admission.

This module admits complete exact-date historical US research-universe
snapshots.

It deliberately does not:
- project current membership backward;
- forward-fill or back-fill universe snapshots;
- infer membership from bars, listings, volume, or price history;
- require identity/session/bar evidence at this boundary;
- remove securities merely because they later delisted or disappeared.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.contracts import (
    USUniverseSnapshot,
    US_MARKET_TIMEZONE,
)


US_UNIVERSE_EVIDENCE_FIELDS = frozenset(
    {
        "effective_date",
        "complete",
        "members",
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


def _canonical_snapshot(
    value: USUniverseSnapshot,
) -> USUniverseSnapshot:
    if type(value) is not USUniverseSnapshot:
        raise ValueError(
            "exact USUniverseSnapshot required"
        )

    _require_exact_date(
        value.effective_date,
        field_name="snapshot.effective_date",
    )

    return USUniverseSnapshot.model_validate(
        value.model_dump(mode="python")
    )


class HistoricalUSUniverseFact(BaseModel):
    """One complete exact-date US universe snapshot and its evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    snapshot: USUniverseSnapshot
    evidence_package: HistoricalEvidencePackage

    @field_validator(
        "snapshot",
        mode="before",
    )
    @classmethod
    def exact_snapshot_type(
        cls,
        value,
    ):
        return _canonical_snapshot(value)

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
    def evidence_scope(
        self,
    ) -> "HistoricalUSUniverseFact":
        covered = set(
            self.evidence_package.evidence.covered_fields
        )

        missing = (
            US_UNIVERSE_EVIDENCE_FIELDS
            - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US universe fields: "
                + ",".join(sorted(missing))
            )

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-universe-fact-v1"
            ),
            "snapshot": self.snapshot.model_dump(
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
class AdmittedUSUniverseHistory:
    """Canonical exact-date historical universe snapshots."""

    facts: tuple[HistoricalUSUniverseFact, ...]
    decision_at: datetime
    research_built_at: datetime

    @property
    def identity(self) -> str:
        # research_built_at is intentionally excluded.
        payload = {
            "schema_version": (
                "admitted-us-universe-history-v1"
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
    fact: HistoricalUSUniverseFact,
):
    return (
        fact.snapshot.effective_date,
        fact.identity,
    )


def admit_us_universe_history(
    facts: tuple[HistoricalUSUniverseFact, ...],
    *,
    decision_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSUniverseHistory:
    """Admit exact-date complete retrospective US universe snapshots.

    Missing dates remain missing. No neighboring snapshot is ever projected.
    """

    if type(facts) is not tuple:
        raise ValueError(
            "canonical universe fact tuple required"
        )

    if not facts:
        raise ValueError(
            "at least one historical US universe fact required"
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
        HistoricalUSUniverseFact
    ] = []

    for item in facts:
        if type(item) is not HistoricalUSUniverseFact:
            raise ValueError(
                "exact HistoricalUSUniverseFact required"
            )

        snapshot = _canonical_snapshot(
            item.snapshot
        )

        package = require_historical_evidence(
            item.evidence_package,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

        _require_package_exact_utc(package)

        canonical = HistoricalUSUniverseFact(
            snapshot=snapshot,
            evidence_package=package,
        )

        if (
            canonical.snapshot.effective_date
            > decision_market_date
        ):
            raise ValueError(
                "future-effective US universe snapshot "
                "cannot enter admitted history"
            )

        admitted.append(canonical)

    admitted.sort(
        key=_fact_sort_key
    )

    seen_fact_ids: set[str] = set()
    by_date: dict[
        date,
        HistoricalUSUniverseFact,
    ] = {}

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US universe fact"
            )

        seen_fact_ids.add(item.identity)

        effective_date = (
            item.snapshot.effective_date
        )

        if effective_date in by_date:
            raise ValueError(
                "multiple US universe snapshots "
                "for same effective date"
            )

        by_date[effective_date] = item

    return AdmittedUSUniverseHistory(
        facts=tuple(
            by_date[item]
            for item in sorted(by_date)
        ),
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


def resolve_us_universe_on_date(
    history: AdmittedUSUniverseHistory,
    *,
    effective_date: date,
) -> USUniverseSnapshot:
    """Resolve one exact historical universe snapshot only."""

    if type(history) is not AdmittedUSUniverseHistory:
        raise ValueError(
            "exact AdmittedUSUniverseHistory required"
        )

    effective_date = _require_exact_date(
        effective_date,
        field_name="effective_date",
    )

    decision_market_date = (
        history.decision_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    if effective_date > decision_market_date:
        raise ValueError(
            "effective_date is after admitted decision horizon"
        )

    matches = [
        item.snapshot
        for item in history.facts
        if (
            item.snapshot.effective_date
            == effective_date
        )
    ]

    if not matches:
        raise ValueError(
            "no exact-dated US universe snapshot"
        )

    if len(matches) != 1:
        raise ValueError(
            "ambiguous exact-dated US universe snapshot"
        )

    return _canonical_snapshot(
        matches[0]
    )


__all__ = [
    "US_UNIVERSE_EVIDENCE_FIELDS",
    "HistoricalUSUniverseFact",
    "AdmittedUSUniverseHistory",
    "admit_us_universe_history",
    "resolve_us_universe_on_date",
]
