from __future__ import annotations

from datetime import date

from app.core.holiday_verification import (
    HolidayAuthority,
    HolidayVerificationDecision,
    HolidayVerificationPolicy,
    OfficialHolidayEvidence,
)
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)


class HolidayVerificationService:
    """
    Read persisted holiday evidence and evaluate
    it through the pure verification policy.

    Unknown authorities fail closed to UNKNOWN.
    This service performs no writes and no network
    operations.
    """

    def __init__(
        self,
        *,
        repository: HolidayEvidenceRepository,
        policy: HolidayVerificationPolicy,
    ) -> None:
        self.repository = repository
        self.policy = policy

    def evaluate(
        self,
        market_date: date,
    ) -> HolidayVerificationDecision:
        records = (
            self.repository
            .list_for_observed_date(
                market_date
            )
        )

        evidence: list[
            OfficialHolidayEvidence
        ] = []

        for record in records:
            try:
                authority = HolidayAuthority(
                    record.authority
                )
            except ValueError:
                return HolidayVerificationDecision(
                    market_date=market_date,
                    status=(
                        MarketSessionStatus.UNKNOWN
                    ),
                    reasons=(
                        "unsupported_holiday_authority",
                    ),
                )

            evidence.append(
                OfficialHolidayEvidence(
                    authority=authority,
                    observed_date=(
                        record.observed_date
                    ),
                    nominal_date=(
                        record.nominal_date
                    ),
                    market_closed=(
                        record.market_closed
                    ),
                )
            )

        return self.policy.evaluate(
            market_date=market_date,
            evidence=evidence,
        )
