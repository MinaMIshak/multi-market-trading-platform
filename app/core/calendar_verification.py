from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.enums import (
    MarketSessionStatus,
)


@dataclass(frozen=True)
class OfficialIndexEvidence:
    provider: str
    index_name: str
    source_snapshot_date: date
    newest_market_date: date | None
    validated: bool


@dataclass(frozen=True)
class CalendarVerificationDecision:
    market_date: date
    status: MarketSessionStatus
    reasons: tuple[str, ...]


class CalendarVerificationPolicy:
    REQUIRED_INDICES = frozenset(
        {
            "CASE30",
            "EGX70_EWI",
            "EGX100_EWI",
        }
    )

    OFFICIAL_PROVIDER = "egx_official_public"

    def evaluate(
        self,
        *,
        market_date: date,
        evidence: list[OfficialIndexEvidence],
    ) -> CalendarVerificationDecision:
        by_index: dict[
            str,
            list[OfficialIndexEvidence],
        ] = {}

        for item in evidence:
            name = item.index_name.strip().upper()

            if name not in self.REQUIRED_INDICES:
                continue

            by_index.setdefault(
                name,
                [],
            ).append(item)

        reasons: list[str] = []

        for index_name in sorted(
            self.REQUIRED_INDICES
        ):
            items = by_index.get(
                index_name,
                [],
            )

            if len(items) != 1:
                reasons.append(
                    f"{index_name}:"
                    f"evidence_count={len(items)}"
                )
                continue

            item = items[0]

            if (
                item.provider.strip().lower()
                != self.OFFICIAL_PROVIDER
            ):
                reasons.append(
                    f"{index_name}:provider"
                )

            if not item.validated:
                reasons.append(
                    f"{index_name}:not_validated"
                )

            if (
                item.source_snapshot_date
                != market_date
            ):
                reasons.append(
                    f"{index_name}:stale_snapshot"
                )

            if (
                item.newest_market_date
                != market_date
            ):
                reasons.append(
                    f"{index_name}:"
                    "target_bar_absent"
                )

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
                "official_indices_confirm_target_date",
            ),
        )
