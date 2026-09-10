from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.calendar_maintenance_execution import (
    CalendarMaintenanceExecutionError,
)
from app.core.job_state import SchedulerJobStatus
from app.core.orchestrator import ScheduleEvaluation
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.domain.enums import (
    MarketSessionStatus,
)


_CHECKPOINT = (
    CheckpointName.CALENDAR_MAINTENANCE
)


@dataclass(frozen=True)
class CalendarMaintenanceDispatchOutcome:
    checkpoint_name: CheckpointName
    claimed: bool | None
    succeeded: bool
    calendar_truth: CalendarTruth | None = None
    base_status: MarketSessionStatus | None = None
    holiday_status: MarketSessionStatus | None = None
    error_type: str | None = None


class CalendarMaintenanceDispatcher:
    """
    Automatic dispatcher for local calendar
    maintenance.

    Automatic execution is PENDING-only.
    FAILED jobs are deliberately not retried by
    the scheduler polling loop.
    """

    def __init__(
        self,
        *,
        scheduler_repository: Any,
        execution_adapter: Any,
    ) -> None:
        self.scheduler_repository = (
            scheduler_repository
        )
        self.execution_adapter = (
            execution_adapter
        )

    def _status(
        self,
        *,
        market_date,
    ) -> SchedulerJobStatus | None:
        row = self.scheduler_repository.get_job(
            market_date=market_date,
            checkpoint_name=_CHECKPOINT,
        )

        if row is None:
            return None

        return SchedulerJobStatus(
            row["status"]
        )

    def select_checkpoint(
        self,
        evaluation: ScheduleEvaluation,
    ) -> CheckpointName | None:
        due = {
            window.name
            for window in evaluation.due
        }

        if _CHECKPOINT not in due:
            return None

        status = self._status(
            market_date=evaluation.market_date
        )

        if status != SchedulerJobStatus.PENDING:
            return None

        return _CHECKPOINT

    def dispatch(
        self,
        *,
        evaluation: ScheduleEvaluation,
    ) -> tuple[
        CalendarMaintenanceDispatchOutcome,
        ...,
    ]:
        checkpoint = self.select_checkpoint(
            evaluation
        )

        if checkpoint is None:
            return ()

        try:
            result = (
                self.execution_adapter.execute(
                    market_date=(
                        evaluation.market_date
                    ),
                    checkpoint_name=checkpoint,
                )
            )

        except CalendarMaintenanceExecutionError as exc:
            return (
                CalendarMaintenanceDispatchOutcome(
                    checkpoint_name=checkpoint,
                    claimed=None,
                    succeeded=False,
                    error_type=type(exc).__name__,
                ),
            )

        return (
            CalendarMaintenanceDispatchOutcome(
                checkpoint_name=checkpoint,
                claimed=result.claimed,
                succeeded=result.succeeded,
                calendar_truth=(
                    result.calendar_truth
                ),
                base_status=result.base_status,
                holiday_status=(
                    result.holiday_status
                ),
            ),
        )
