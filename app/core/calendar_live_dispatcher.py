from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.calendar_live_execution import (
    CalendarLiveExecutionError,
)
from app.core.job_state import SchedulerJobStatus
from app.core.orchestrator import ScheduleEvaluation
from app.core.schedule import CheckpointName
from app.domain.enums import MarketSessionStatus


_CHECKPOINTS = (
    CheckpointName.CALENDAR_LIVE_1005,
    CheckpointName.CALENDAR_LIVE_1008,
    CheckpointName.CALENDAR_LIVE_1011,
    CheckpointName.CALENDAR_LIVE_1013,
)


@dataclass(frozen=True)
class CalendarLiveDispatchOutcome:
    checkpoint_name: CheckpointName
    claimed: bool | None
    succeeded: bool
    verification_status: MarketSessionStatus | None = None
    error_type: str | None = None


class CalendarLiveDispatcher:
    """
    Execute at most one due calendar-live checkpoint
    per scheduler polling cycle.

    Only PENDING jobs are started automatically.
    Each retry window is represented by a distinct
    scheduled checkpoint.
    """

    def __init__(
        self,
        *,
        scheduler_repository: Any,
        execution_adapter: Any,
    ) -> None:
        self.scheduler_repository = scheduler_repository
        self.execution_adapter = execution_adapter

    def _status(
        self,
        *,
        market_date,
        checkpoint_name,
    ) -> SchedulerJobStatus | None:
        row = self.scheduler_repository.get_job(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
        )

        if row is None:
            return None

        return SchedulerJobStatus(row["status"])

    def select_checkpoint(
        self,
        evaluation: ScheduleEvaluation,
    ) -> CheckpointName | None:
        due = {
            window.name
            for window in evaluation.due
        }

        for checkpoint in _CHECKPOINTS:
            if checkpoint not in due:
                continue

            status = self._status(
                market_date=evaluation.market_date,
                checkpoint_name=checkpoint,
            )

            if status == SchedulerJobStatus.PENDING:
                return checkpoint

        return None

    def dispatch(
        self,
        *,
        evaluation: ScheduleEvaluation,
    ) -> tuple[CalendarLiveDispatchOutcome, ...]:
        checkpoint = self.select_checkpoint(evaluation)

        if checkpoint is None:
            return ()

        try:
            result = self.execution_adapter.execute(
                market_date=evaluation.market_date,
                checkpoint_name=checkpoint,
            )
        except CalendarLiveExecutionError as exc:
            return (
                CalendarLiveDispatchOutcome(
                    checkpoint_name=checkpoint,
                    claimed=None,
                    succeeded=False,
                    error_type=type(exc).__name__,
                ),
            )

        return (
            CalendarLiveDispatchOutcome(
                checkpoint_name=checkpoint,
                claimed=result.claimed,
                succeeded=result.succeeded,
                verification_status=(
                    result.verification_status
                ),
            ),
        )
