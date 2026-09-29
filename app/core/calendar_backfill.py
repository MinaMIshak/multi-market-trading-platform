from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.core.base_calendar_service import (
    BaseCalendarSessionService,
)
from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.core.calendar_verification import (
    CalendarVerificationPolicy,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.core.holiday_promotion import HolidayPromotionPolicy
from app.core.holiday_promotion_service import (
    HolidayPromotionService,
)
from app.core.holiday_verification import (
    HolidayVerificationPolicy,
)
from app.core.holiday_verification_service import (
    HolidayVerificationService,
)
from app.domain.enums import MarketSessionStatus
from app.storage.database import Database
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
)
from app.storage.repository import TradingRepository


@dataclass(frozen=True)
class CalendarBackfillDateResult:
    market_date: date
    base_status: MarketSessionStatus
    holiday_status: MarketSessionStatus
    verification_status: MarketSessionStatus


@dataclass(frozen=True)
class CalendarBackfillRuntime:
    """
    Offline/manual recovery of calendar-session truth from evidence
    already admitted to this database.

    Reuses the same components the scheduler dispatches
    (BaseCalendarSessionService, HolidayPromotionService,
    CalendarVerificationService) without the scheduler job ledger, so a
    date range already covered by already-persisted official-index or
    holiday evidence can be resolved in one pass. Performs no network
    acquisition: WEEKEND is deterministic weekday classification;
    HOLIDAY and VERIFIED require evidence already present in the
    database and fail closed to UNKNOWN otherwise.
    """

    base_service: BaseCalendarSessionService
    holiday_promotion_service: HolidayPromotionService
    verification_service: CalendarVerificationService

    def run_date(
        self,
        market_date: date,
    ) -> CalendarBackfillDateResult:
        base_status = self.base_service.apply(market_date)

        holiday_outcome = self.holiday_promotion_service.promote(
            market_date
        )

        verification_decision = self.verification_service.verify(
            market_date
        )

        return CalendarBackfillDateResult(
            market_date=market_date,
            base_status=base_status,
            holiday_status=holiday_outcome.verification.status,
            verification_status=verification_decision.status,
        )

    def run_range(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[CalendarBackfillDateResult, ...]:
        if end_date < start_date:
            raise ValueError(
                "end_date must not precede start_date"
            )

        results = []
        current = start_date

        while current <= end_date:
            results.append(self.run_date(current))
            current += timedelta(days=1)

        return tuple(results)


def build_calendar_backfill_runtime(
    *,
    database: Database,
) -> CalendarBackfillRuntime:
    trading_repository = TradingRepository(database)
    transition_repository = MarketSessionTransitionRepository(
        database
    )
    holiday_evidence_repository = HolidayEvidenceRepository(
        database
    )
    official_index_evidence_repository = (
        OfficialIndexEvidenceRepository(database)
    )

    base_service = BaseCalendarSessionService(
        trading_repository=trading_repository,
        policy=BaseTradingCalendarPolicy(),
        transition_repository=transition_repository,
    )

    holiday_verification_service = HolidayVerificationService(
        repository=holiday_evidence_repository,
        policy=HolidayVerificationPolicy(),
    )

    holiday_promotion_service = HolidayPromotionService(
        verification_service=holiday_verification_service,
        trading_repository=trading_repository,
        promotion_policy=HolidayPromotionPolicy(),
        transition_repository=transition_repository,
    )

    verification_service = CalendarVerificationService(
        evidence_repository=official_index_evidence_repository,
        trading_repository=trading_repository,
        policy=CalendarVerificationPolicy(),
        transition_repository=transition_repository,
    )

    return CalendarBackfillRuntime(
        base_service=base_service,
        holiday_promotion_service=holiday_promotion_service,
        verification_service=verification_service,
    )
