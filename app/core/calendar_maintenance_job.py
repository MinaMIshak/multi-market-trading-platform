from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.domain.enums import MarketSessionStatus
from app.core.schedule import CalendarTruth


@dataclass(frozen=True)
class CalendarMaintenanceResult:
    market_date: date
    base_status: MarketSessionStatus
    holiday_status: MarketSessionStatus
    calendar_truth: CalendarTruth


class CalendarMaintenanceJob:
    """
    Local calendar-maintenance composition.

    Order is deliberate:
    1. persist deterministic WEEKEND when applicable;
    2. promote already-persisted authoritative holiday evidence;
    3. resolve final persisted calendar truth.

    This component performs no network acquisition.
    """

    def __init__(
        self,
        *,
        base_service: Any,
        holiday_promotion_service: Any,
        truth_resolver: Any,
    ) -> None:
        self.base_service = base_service
        self.holiday_promotion_service = (
            holiday_promotion_service
        )
        self.truth_resolver = truth_resolver

    def run(
        self,
        market_date: date,
    ) -> CalendarMaintenanceResult:
        base_status = self.base_service.apply(
            market_date
        )

        holiday_outcome = (
            self.holiday_promotion_service.promote(
                market_date
            )
        )

        calendar_truth = self.truth_resolver.resolve(
            market_date
        )

        return CalendarMaintenanceResult(
            market_date=market_date,
            base_status=base_status,
            holiday_status=(
                holiday_outcome.verification.status
            ),
            calendar_truth=calendar_truth,
        )
