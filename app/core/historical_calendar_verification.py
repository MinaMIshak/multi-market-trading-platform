from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.calendar_verification import (
    CalendarVerificationDecision,
)
from app.data.validated_index_repository import (
    ValidatedCanonicalIndexError,
    ValidatedCanonicalIndexRepository,
)
from app.domain.enums import MarketSessionStatus


OFFICIAL_PROVIDER = "egx_official_public"

REQUIRED_INDICES = (
    "CASE30",
    "EGX70_EWI",
    "EGX100_EWI",
)


@dataclass(frozen=True)
class HistoricalOfficialIndexEvidence:
    provider: str
    index_name: str
    artifact_id: str
    source_snapshot_date: date
    bar_present: bool


class HistoricalOfficialIndexEvidenceRepository:
    """
    Historical session evidence from already-admitted official index
    history.

    For a target date D, each required index contributes the earliest
    VALIDATED egx_official_public D1 artifact whose snapshot was taken
    strictly after D (so session D had closed) and whose dated range
    covers D. The artifact is read only through
    ValidatedCanonicalIndexRepository (catalog status, sha256, row
    provenance); an artifact that fails those checks is skipped, never
    repaired. bar_present records whether a FULL_OHLC_VALID bar dated
    D exists in that artifact.

    Same-day snapshots are deliberately excluded: they belong to the
    live CalendarVerificationPolicy path.
    """

    def __init__(
        self,
        *,
        validated_reader: ValidatedCanonicalIndexRepository,
    ) -> None:
        self.validated = validated_reader

    def load(
        self,
        market_date: date,
    ) -> list[HistoricalOfficialIndexEvidence]:
        evidence = []

        for index_name in REQUIRED_INDICES:
            for snapshot in self.validated.covering_snapshot_dates(
                index_name=index_name,
                market_date=market_date,
                provider=OFFICIAL_PROVIDER,
            ):
                try:
                    dataset = self.validated.load(
                        index_name=index_name,
                        source_snapshot_date=snapshot,
                        provider=OFFICIAL_PROVIDER,
                    )
                except ValidatedCanonicalIndexError:
                    continue

                evidence.append(
                    HistoricalOfficialIndexEvidence(
                        provider=dataset.provider,
                        index_name=dataset.index_name,
                        artifact_id=dataset.artifact_id,
                        source_snapshot_date=(
                            dataset.source_snapshot_date
                        ),
                        bar_present=any(
                            row.market_date == market_date
                            for row in dataset.full_ohlc_rows
                        ),
                    )
                )
                break

        return evidence


class HistoricalCalendarVerificationPolicy:
    """
    VERIFIED only when every required official index has exactly one
    admitted historical artifact, snapshotted strictly after the
    target date, containing a FULL_OHLC_VALID bar for that date.

    A missing bar is never read as HOLIDAY: holidays require holiday
    evidence. Every other outcome fails closed to UNKNOWN.
    """

    REQUIRED_INDICES = frozenset(REQUIRED_INDICES)

    def evaluate(
        self,
        *,
        market_date: date,
        evidence: list[HistoricalOfficialIndexEvidence],
    ) -> CalendarVerificationDecision:
        by_index: dict[
            str,
            list[HistoricalOfficialIndexEvidence],
        ] = {}

        for item in evidence:
            name = item.index_name.strip().upper()

            if name in self.REQUIRED_INDICES:
                by_index.setdefault(name, []).append(item)

        reasons: list[str] = []

        for index_name in sorted(self.REQUIRED_INDICES):
            items = by_index.get(index_name, [])

            if len(items) != 1:
                reasons.append(
                    f"{index_name}:evidence_count={len(items)}"
                )
                continue

            item = items[0]

            if item.provider.strip().lower() != OFFICIAL_PROVIDER:
                reasons.append(f"{index_name}:provider")

            if item.source_snapshot_date <= market_date:
                reasons.append(
                    f"{index_name}:snapshot_not_after_target"
                )

            if not item.bar_present:
                reasons.append(f"{index_name}:target_bar_absent")

        if reasons:
            return CalendarVerificationDecision(
                market_date=market_date,
                status=MarketSessionStatus.UNKNOWN,
                reasons=tuple(reasons),
            )

        return CalendarVerificationDecision(
            market_date=market_date,
            status=MarketSessionStatus.VERIFIED,
            reasons=(
                "historical_official_index_bars_confirm_target_date",
            ),
        )
