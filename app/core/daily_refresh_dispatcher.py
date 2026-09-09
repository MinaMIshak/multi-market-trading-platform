from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.daily_refresh_execution import (
    DailyRefreshExecutionError,
)
from app.core.job_state import SchedulerJobStatus
from app.core.orchestrator import ScheduleEvaluation
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.data.daily_refresh_job import (
    DailyRefreshJobError,
)


_PRIMARY = (
    CheckpointName.AFTER_SESSION_PRIMARY
)

_FALLBACK = (
    CheckpointName.AFTER_SESSION_FALLBACK
)


@dataclass(frozen=True)
class DailyRefreshDispatchOutcome:
    checkpoint_name: CheckpointName
    claimed: bool | None
    succeeded: bool
    item_count: int = 0
    error_type: str | None = None


class DailyRefreshDispatcher:
    """
    Select at most one automatic daily-refresh
    execution from a scheduler evaluation.

    Automatic dispatch is PENDING-only.
    FAILED jobs are intentionally not retried by
    the normal polling loop.
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
        checkpoint_name,
    ) -> SchedulerJobStatus | None:
        row = (
            self.scheduler_repository
            .get_job(
                market_date=market_date,
                checkpoint_name=(
                    checkpoint_name
                ),
            )
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
        if (
            evaluation.calendar_truth
            != CalendarTruth
            .VERIFIED_TRADING_DAY
        ):
            return None

        due = {
            window.name
            for window in evaluation.due
        }

        # Once fallback is due, never start a
        # new primary automatically.
        if _FALLBACK in due:
            fallback_status = self._status(
                market_date=(
                    evaluation.market_date
                ),
                checkpoint_name=_FALLBACK,
            )

            if (
                fallback_status
                != SchedulerJobStatus.PENDING
            ):
                return None

            primary_status = self._status(
                market_date=(
                    evaluation.market_date
                ),
                checkpoint_name=_PRIMARY,
            )

            if primary_status in {
                SchedulerJobStatus.RUNNING,
                SchedulerJobStatus.SUCCEEDED,
            }:
                return None

            return _FALLBACK

        if _PRIMARY not in due:
            return None

        primary_status = self._status(
            market_date=evaluation.market_date,
            checkpoint_name=_PRIMARY,
        )

        if (
            primary_status
            != SchedulerJobStatus.PENDING
        ):
            return None

        return _PRIMARY

    def dispatch(
        self,
        *,
        evaluation: ScheduleEvaluation,
        provider: Any,
    ) -> tuple[
        DailyRefreshDispatchOutcome,
        ...,
    ]:
        checkpoint = self.select_checkpoint(
            evaluation
        )

        if checkpoint is None:
            return ()

        try:
            result = (
                self.execution_adapter
                .execute(
                    market_date=(
                        evaluation.market_date
                    ),
                    checkpoint_name=checkpoint,
                    provider=provider,
                )
            )

        except (
            DailyRefreshJobError,
            DailyRefreshExecutionError,
        ) as exc:
            return (
                DailyRefreshDispatchOutcome(
                    checkpoint_name=checkpoint,
                    claimed=None,
                    succeeded=False,
                    error_type=(
                        type(exc).__name__
                    ),
                ),
            )

        return (
            DailyRefreshDispatchOutcome(
                checkpoint_name=checkpoint,
                claimed=result.claimed,
                succeeded=result.succeeded,
                item_count=result.item_count,
            ),
        )
