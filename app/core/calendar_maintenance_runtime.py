from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.base_calendar_service import (
    BaseCalendarSessionService,
)
from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.core.calendar_maintenance_dispatcher import (
    CalendarMaintenanceDispatcher,
)
from app.core.calendar_maintenance_execution import (
    CalendarMaintenanceExecutionAdapter,
)
from app.core.calendar_maintenance_job import (
    CalendarMaintenanceJob,
)
from app.core.calendar_truth import CalendarTruthResolver
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
from app.storage.database import Database
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
)
from app.storage.repository import TradingRepository


@dataclass(frozen=True)
class CalendarMaintenanceRuntime:
    trading_repository: TradingRepository
    holiday_evidence_repository: HolidayEvidenceRepository
    transition_repository: MarketSessionTransitionRepository
    truth_resolver: CalendarTruthResolver
    base_service: BaseCalendarSessionService
    holiday_verification_service: HolidayVerificationService
    holiday_promotion_service: HolidayPromotionService
    maintenance_job: CalendarMaintenanceJob
    execution_adapter: CalendarMaintenanceExecutionAdapter
    dispatcher: CalendarMaintenanceDispatcher


def build_calendar_maintenance_runtime(
    *,
    database: Database,
    scheduler_repository: Any,
) -> CalendarMaintenanceRuntime:
    trading = TradingRepository(database)
    transition = MarketSessionTransitionRepository(database)
    evidence = HolidayEvidenceRepository(database)

    truth = CalendarTruthResolver(trading)

    base = BaseCalendarSessionService(
        trading_repository=trading,
        policy=BaseTradingCalendarPolicy(),
        transition_repository=transition,
    )

    holiday_verification = HolidayVerificationService(
        repository=evidence,
        policy=HolidayVerificationPolicy(),
    )

    holiday_promotion = HolidayPromotionService(
        verification_service=holiday_verification,
        trading_repository=trading,
        promotion_policy=HolidayPromotionPolicy(),
        transition_repository=transition,
    )

    job = CalendarMaintenanceJob(
        base_service=base,
        holiday_promotion_service=holiday_promotion,
        truth_resolver=truth,
    )

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=scheduler_repository,
        maintenance_job=job,
    )

    dispatcher = CalendarMaintenanceDispatcher(
        scheduler_repository=scheduler_repository,
        execution_adapter=adapter,
    )

    return CalendarMaintenanceRuntime(
        trading_repository=trading,
        holiday_evidence_repository=evidence,
        transition_repository=transition,
        truth_resolver=truth,
        base_service=base,
        holiday_verification_service=holiday_verification,
        holiday_promotion_service=holiday_promotion,
        maintenance_job=job,
        execution_adapter=adapter,
        dispatcher=dispatcher,
    )
