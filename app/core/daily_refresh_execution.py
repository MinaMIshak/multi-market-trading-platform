from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.core.schedule import CheckpointName
from app.data.daily_refresh_job import (
    DailyRefreshJobError,
)


_ALLOWED_CHECKPOINTS = {
    CheckpointName.AFTER_SESSION_PRIMARY,
    CheckpointName.AFTER_SESSION_FALLBACK,
}


@dataclass(frozen=True)
class DailyRefreshExecutionResult:
    market_date: date
    checkpoint_name: CheckpointName
    claimed: bool
    succeeded: bool
    item_count: int = 0


class DailyRefreshExecutionError(
    RuntimeError
):
    pass


def _safe_failure_message(
    exc: Exception,
) -> str:
    if isinstance(
        exc,
        DailyRefreshJobError,
    ):
        return (
            "DAILY_REFRESH_FAILED:"
            f"{exc.canonical_symbol}:"
            f"{exc.cause_type}"
        )

    return (
        "DAILY_REFRESH_FAILED:"
        f"{type(exc).__name__}"
    )


class DailyRefreshExecutionAdapter:
    """
    Scheduler-ledger boundary for DailyRefreshJob.

    Provider creation and secret loading are
    intentionally outside this component.
    """

    def __init__(
        self,
        *,
        scheduler_repository: Any,
        refresh_job: Any,
        lookback_days: int = 400,
    ) -> None:
        if lookback_days < 1:
            raise ValueError(
                "lookback_days must be positive"
            )

        self.scheduler_repository = (
            scheduler_repository
        )
        self.refresh_job = refresh_job
        self.lookback_days = lookback_days

    def execute(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
        provider: Any,
    ) -> DailyRefreshExecutionResult:
        if checkpoint_name not in (
            _ALLOWED_CHECKPOINTS
        ):
            raise ValueError(
                "checkpoint is not allowed "
                "to execute daily refresh"
            )

        if (
            checkpoint_name
            == CheckpointName
            .AFTER_SESSION_FALLBACK
        ):
            completed = (
                self.scheduler_repository
                .successful_checkpoints(
                    market_date
                )
            )

            if (
                CheckpointName
                .AFTER_SESSION_PRIMARY
                in completed
            ):
                return (
                    DailyRefreshExecutionResult(
                        market_date=market_date,
                        checkpoint_name=(
                            checkpoint_name
                        ),
                        claimed=False,
                        succeeded=False,
                    )
                )

        claimed = (
            self.scheduler_repository
            .claim_job(
                market_date=market_date,
                checkpoint_name=(
                    checkpoint_name
                ),
            )
        )

        if not claimed:
            return DailyRefreshExecutionResult(
                market_date=market_date,
                checkpoint_name=(
                    checkpoint_name
                ),
                claimed=False,
                succeeded=False,
            )

        start_date = (
            market_date
            - timedelta(
                days=self.lookback_days
            )
        )

        try:
            result = self.refresh_job.run(
                provider=provider,
                start_date=start_date,
                end_date=market_date,
                snapshot_date=market_date,
            )

        except Exception as exc:
            marked = (
                self.scheduler_repository
                .mark_failed(
                    market_date=market_date,
                    checkpoint_name=(
                        checkpoint_name
                    ),
                    error=(
                        _safe_failure_message(
                            exc
                        )
                    ),
                )
            )

            if not marked:
                raise (
                    DailyRefreshExecutionError(
                        "failed to persist "
                        "daily refresh failure"
                    )
                ) from exc

            raise

        marked = (
            self.scheduler_repository
            .mark_succeeded(
                market_date=market_date,
                checkpoint_name=(
                    checkpoint_name
                ),
            )
        )

        if not marked:
            raise DailyRefreshExecutionError(
                "failed to persist "
                "daily refresh success"
            )

        return DailyRefreshExecutionResult(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
            claimed=True,
            succeeded=True,
            item_count=len(
                result.items
            ),
        )
