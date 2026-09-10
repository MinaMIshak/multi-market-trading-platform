from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domain.enums import MarketSessionStatus


class HolidayAuthority(StrEnum):
    EGX_OFFICIAL = "egx_official"
    EGYPT_GOVERNMENT = "egypt_government"


@dataclass(frozen=True)
class OfficialHolidayEvidence:
    authority: HolidayAuthority
    observed_date: date
    market_closed: bool
    nominal_date: date | None = None


@dataclass(frozen=True)
class HolidayVerificationDecision:
    market_date: date
    status: MarketSessionStatus
    reasons: tuple[str, ...]


class HolidayVerificationPolicy:
    def evaluate(
        self,
        *,
        market_date: date,
        evidence: list[OfficialHolidayEvidence],
    ) -> HolidayVerificationDecision:
        target = [
            item
            for item in evidence
            if item.observed_date == market_date
        ]

        egx = [
            item
            for item in target
            if item.authority
            == HolidayAuthority.EGX_OFFICIAL
        ]

        if egx:
            states = {
                item.market_closed
                for item in egx
            }

            if states == {True}:
                return HolidayVerificationDecision(
                    market_date=market_date,
                    status=MarketSessionStatus.HOLIDAY,
                    reasons=(
                        "egx_official_closure_confirmed",
                    ),
                )

            if states == {True, False}:
                return HolidayVerificationDecision(
                    market_date=market_date,
                    status=MarketSessionStatus.UNKNOWN,
                    reasons=(
                        "conflicting_egx_closure_evidence",
                    ),
                )

            return HolidayVerificationDecision(
                market_date=market_date,
                status=MarketSessionStatus.UNKNOWN,
                reasons=(
                    "egx_closure_not_confirmed",
                ),
            )

        government = any(
            item.authority
            == HolidayAuthority.EGYPT_GOVERNMENT
            for item in target
        )

        reason = (
            "awaiting_egx_closure_confirmation"
            if government
            else "no_target_date_holiday_evidence"
        )

        return HolidayVerificationDecision(
            market_date=market_date,
            status=MarketSessionStatus.UNKNOWN,
            reasons=(reason,),
        )
