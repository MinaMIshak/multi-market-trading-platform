from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.domain.enums import (
    MarketSessionStatus,
)


@dataclass(frozen=True)
class CalendarMaintenanceExecutionResult:
    market_date: date
    checkpoint_name: CheckpointName
    claimed: bool
    succeeded: bool
    calendar_truth: CalendarTruth | None = None
    base_status: MarketSessionStatus | None = None
    holiday_status: MarketSessionStatus | None = None


class CalendarMaintenanceExecutionError(
    RuntimeError
):
    pass


def _safe_failure_message(
    exc: Exception,
) -> str:
    return (
        "CALENDAR_MAINTENANCE_FAILED:"
        f"{type(exc).__name__}"
    )


class CalendarMaintenanceExecutionAdapter:
    """
    Scheduler-ledger boundary for local calendar
    maintenance.

    Selection/retry policy belongs to the
    dispatcher. This adapter only performs the
    atomic claim -> run -> complete lifecycle.
    """

    def __init__(
        self,
        *,
        scheduler_repository: Any,
        maintenance_job: Any,
    ) -> None:
        self.scheduler_repository = (
            scheduler_repository
        )
        self.maintenance_job = maintenance_job

    def execute(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
    ) -> CalendarMaintenanceExecutionResult:
        if (
            checkpoint_name
            != CheckpointName.CALENDAR_MAINTENANCE
        ):
            raise ValueError(
                "checkpoint is not allowed to "
                "execute calendar maintenance"
            )

        claimed = (
            self.scheduler_repository.claim_job(
                market_date=market_date,
                checkpoint_name=checkpoint_name,
            )
        )

        if not claimed:
            return CalendarMaintenanceExecutionResult(
                market_date=market_date,
                checkpoint_name=checkpoint_name,
                claimed=False,
                succeeded=False,
            )

        try:
            result = self.maintenance_job.run(
                market_date
            )

        except Exception as exc:
            safe_error = _safe_failure_message(
                exc
            )

            marked = (
                self.scheduler_repository
                .mark_failed(
                    market_date=market_date,
                    checkpoint_name=(
                        checkpoint_name
                    ),
                    error=safe_error,
                )
            )

            if not marked:
                raise (
                    CalendarMaintenanceExecutionError(
                        "failed to persist calendar "
                        "maintenance failure"
                    )
                ) from exc

            raise CalendarMaintenanceExecutionError(
                safe_error
            ) from exc

        marked = (
            self.scheduler_repository
            .mark_succeeded(
                market_date=market_date,
                checkpoint_name=checkpoint_name,
            )
        )

        if not marked:
            raise CalendarMaintenanceExecutionError(
                "failed to persist calendar "
                "maintenance success"
            )

        return CalendarMaintenanceExecutionResult(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
            claimed=True,
            succeeded=True,
            calendar_truth=result.calendar_truth,
            base_status=result.base_status,
            holiday_status=result.holiday_status,
        )
