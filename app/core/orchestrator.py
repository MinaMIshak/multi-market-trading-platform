from __future__ import annotations

from datetime import (
    date,
    datetime,
    timedelta,
)
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
)

from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
    MarketSchedulePolicy,
    ScheduledCheckpoint,
    SessionPhase,
)


class CheckpointWindow(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    name: CheckpointName
    scheduled_at: datetime
    expires_at: datetime


class ScheduleEvaluation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    market_date: date
    evaluated_at: datetime
    calendar_truth: CalendarTruth
    session_phase: SessionPhase

    due: list[CheckpointWindow]
    missed: list[CheckpointWindow]
    blocked: list[CheckpointWindow]
    skipped: list[CheckpointWindow]


class MarketSessionOrchestrator:
    def __init__(
        self,
        policy: MarketSchedulePolicy | None = None,
    ) -> None:
        self.policy = (
            policy
            or MarketSchedulePolicy()
        )

        self.tz = ZoneInfo(
            self.policy.timezone
        )

    def _localize(
        self,
        market_date: date,
        checkpoint: ScheduledCheckpoint,
    ) -> datetime:
        return datetime.combine(
            market_date,
            checkpoint.at,
            tzinfo=self.tz,
        )

    def session_phase(
        self,
        now: datetime,
    ) -> SessionPhase:
        if now.tzinfo is None:
            raise ValueError(
                "now must be timezone-aware"
            )

        local_now = now.astimezone(
            self.tz
        )

        clock = local_now.timetz().replace(
            tzinfo=None
        )

        if clock < self.policy.market_open:
            return SessionPhase.PRE_MARKET

        if clock < self.policy.first15_complete:
            return SessionPhase.OPENING_RANGE

        if clock < self.policy.pre_close_start:
            return SessionPhase.REGULAR

        if clock < self.policy.market_close:
            return SessionPhase.PRE_CLOSE

        return SessionPhase.POST_CLOSE

    def evaluate(
        self,
        *,
        now: datetime,
        market_date: date,
        calendar_truth: CalendarTruth,
        completed_jobs: set[
            CheckpointName | str
        ] | None = None,
    ) -> ScheduleEvaluation:
        if now.tzinfo is None:
            raise ValueError(
                "now must be timezone-aware"
            )

        local_now = now.astimezone(
            self.tz
        )

        if local_now.date() != market_date:
            raise ValueError(
                "market_date must match "
                "the local evaluation date"
            )

        completed = {
            (
                item
                if isinstance(
                    item,
                    CheckpointName,
                )
                else CheckpointName(item)
            )
            for item in (
                completed_jobs or set()
            )
        }

        due: list[CheckpointWindow] = []
        missed: list[CheckpointWindow] = []
        blocked: list[CheckpointWindow] = []
        skipped: list[CheckpointWindow] = []

        for checkpoint in self.policy.checkpoints:
            if checkpoint.name in completed:
                continue

            scheduled_at = self._localize(
                market_date,
                checkpoint,
            )

            expires_at = (
                scheduled_at
                + timedelta(
                    minutes=(
                        checkpoint
                        .max_lateness_minutes
                    )
                )
            )

            if local_now < scheduled_at:
                continue

            window = CheckpointWindow(
                name=checkpoint.name,
                scheduled_at=scheduled_at,
                expires_at=expires_at,
            )

            if (
                checkpoint.fallback_for
                is not None
                and checkpoint.fallback_for
                in completed
            ):
                skipped.append(window)
                continue

            if (
                checkpoint
                .requires_verified_trading_day
            ):
                if (
                    calendar_truth
                    != CalendarTruth
                    .VERIFIED_TRADING_DAY
                ):
                    blocked.append(window)
                    continue

            if local_now > expires_at:
                missed.append(window)
                continue

            due.append(window)

        return ScheduleEvaluation(
            market_date=market_date,
            evaluated_at=local_now,
            calendar_truth=calendar_truth,
            session_phase=self.session_phase(
                local_now
            ),
            due=due,
            missed=missed,
            blocked=blocked,
            skipped=skipped,
        )
