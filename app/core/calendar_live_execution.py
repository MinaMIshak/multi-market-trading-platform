from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.core.schedule import CheckpointName
from app.domain.enums import MarketSessionStatus


_ALLOWED_CHECKPOINTS = frozenset(
    {
        CheckpointName.CALENDAR_LIVE_1005,
        CheckpointName.CALENDAR_LIVE_1008,
        CheckpointName.CALENDAR_LIVE_1011,
        CheckpointName.CALENDAR_LIVE_1013,
    }
)


@dataclass(frozen=True)
class CalendarLiveExecutionResult:
    market_date: date
    checkpoint_name: CheckpointName
    claimed: bool
    succeeded: bool
    verification_status: MarketSessionStatus | None = None


class CalendarLiveExecutionError(RuntimeError):
    pass


class CalendarLiveExecutionAdapter:
    """
    Scheduler-ledger boundary for authoritative
    trading-day verification.

    This component performs no network acquisition.
    It consumes only evidence already admitted into
    the canonical artifact ledger.
    """

    def __init__(
        self,
        *,
        scheduler_repository: Any,
        verification_service: Any,
    ) -> None:
        self.scheduler_repository = scheduler_repository
        self.verification_service = verification_service

    def execute(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
    ) -> CalendarLiveExecutionResult:
        if checkpoint_name not in _ALLOWED_CHECKPOINTS:
            raise ValueError(
                "checkpoint is not allowed to execute "
                "calendar-live verification"
            )

        claimed = self.scheduler_repository.claim_job(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
        )

        if not claimed:
            return CalendarLiveExecutionResult(
                market_date=market_date,
                checkpoint_name=checkpoint_name,
                claimed=False,
                succeeded=False,
            )

        try:
            decision = self.verification_service.verify(
                market_date,
                verified_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            marked = self.scheduler_repository.mark_failed(
                market_date=market_date,
                checkpoint_name=checkpoint_name,
                error=(
                    "CALENDAR_LIVE_FAILED:"
                    f"{type(exc).__name__}"
                ),
            )

            if not marked:
                raise CalendarLiveExecutionError(
                    "failed to persist calendar-live failure"
                ) from exc

            raise CalendarLiveExecutionError(
                "calendar-live verification failed"
            ) from exc

        marked = self.scheduler_repository.mark_succeeded(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
        )

        if not marked:
            raise CalendarLiveExecutionError(
                "failed to persist calendar-live success"
            )

        return CalendarLiveExecutionResult(
            market_date=market_date,
            checkpoint_name=checkpoint_name,
            claimed=True,
            succeeded=True,
            verification_status=decision.status,
        )
