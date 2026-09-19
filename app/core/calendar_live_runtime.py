from __future__ import annotations

from dataclasses import dataclass

from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.core.calendar_live_dispatcher import (
    CalendarLiveDispatcher,
)
from app.core.calendar_live_execution import (
    CalendarLiveExecutionAdapter,
)
from app.core.calendar_verification import (
    CalendarVerificationPolicy,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.storage import TradingRepository


@dataclass(frozen=True)
class CalendarLiveRuntime:
    dispatcher: CalendarLiveDispatcher


def build_calendar_live_runtime(
    *,
    database,
    scheduler_repository,
) -> CalendarLiveRuntime:
    """
    Build DB-only calendar verification runtime.

    No provider is constructed here and no network
    acquisition occurs. Verification consumes only
    already-validated canonical official-index
    evidence.
    """

    trading_repository = TradingRepository(database)

    evidence_repository = (
        OfficialIndexEvidenceRepository(database)
    )

    verification_service = (
        CalendarVerificationService(
            evidence_repository=evidence_repository,
            trading_repository=trading_repository,
            policy=CalendarVerificationPolicy(),
        )
    )

    execution_adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=scheduler_repository,
        verification_service=verification_service,
    )

    dispatcher = CalendarLiveDispatcher(
        scheduler_repository=scheduler_repository,
        execution_adapter=execution_adapter,
    )

    return CalendarLiveRuntime(
        dispatcher=dispatcher
    )
