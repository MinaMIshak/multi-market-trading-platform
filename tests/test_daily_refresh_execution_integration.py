from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.daily_refresh_execution import (
    DailyRefreshExecutionAdapter,
)
from app.core.orchestrator import (
    MarketSessionOrchestrator,
)
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


MARKET_DATE = date(2026, 9, 9)
CAIRO = ZoneInfo("Africa/Cairo")


class FakeRefreshJob:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)

        return SimpleNamespace(
            items=(object(),)
        )


def test_primary_success_blocks_stale_fallback(
    tmp_path,
):
    db = Database(
        tmp_path / "platform.db"
    )
    db.initialize()

    repository = SchedulerRepository(db)

    orchestrator = (
        MarketSessionOrchestrator()
    )

    now = datetime(
        2026,
        9,
        9,
        18,
        15,
        tzinfo=CAIRO,
    )

    first_evaluation = (
        orchestrator.evaluate(
            now=now,
            market_date=MARKET_DATE,
            calendar_truth=(
                CalendarTruth
                .VERIFIED_TRADING_DAY
            ),
            completed_jobs=set(),
        )
    )

    due = {
        window.name
        for window
        in first_evaluation.due
    }

    assert (
        CheckpointName
        .AFTER_SESSION_PRIMARY
        in due
    )

    assert (
        CheckpointName
        .AFTER_SESSION_FALLBACK
        in due
    )

    repository.sync_evaluation(
        first_evaluation
    )

    refresh_job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repository,
        refresh_job=refresh_job,
    )

    primary = adapter.execute(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
        provider=object(),
    )

    assert primary.claimed is True
    assert primary.succeeded is True
    assert len(refresh_job.calls) == 1

    # Simulate a worker still holding the
    # original 18:15 due-list.
    fallback = adapter.execute(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
        provider=object(),
    )

    assert fallback.claimed is False
    assert fallback.succeeded is False

    # Fallback must not invoke refresh again.
    assert len(refresh_job.calls) == 1

    with db.connect() as con:
        rows = con.execute(
            """
            SELECT
                checkpoint_name,
                status,
                attempt_count
            FROM scheduled_jobs
            WHERE market_date = ?
              AND checkpoint_name IN (?, ?)
            """,
            (
                MARKET_DATE.isoformat(),
                (
                    CheckpointName
                    .AFTER_SESSION_PRIMARY
                    .value
                ),
                (
                    CheckpointName
                    .AFTER_SESSION_FALLBACK
                    .value
                ),
            ),
        ).fetchall()

        integrity = con.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

    state = {
        row["checkpoint_name"]: (
            row["status"],
            row["attempt_count"],
        )
        for row in rows
    }

    assert state[
        CheckpointName
        .AFTER_SESSION_PRIMARY
        .value
    ] == (
        "SUCCEEDED",
        1,
    )

    assert state[
        CheckpointName
        .AFTER_SESSION_FALLBACK
        .value
    ] == (
        "PENDING",
        0,
    )

    assert integrity == "ok"

    completed = (
        repository
        .successful_checkpoints(
            MARKET_DATE
        )
    )

    second_evaluation = (
        orchestrator.evaluate(
            now=now,
            market_date=MARKET_DATE,
            calendar_truth=(
                CalendarTruth
                .VERIFIED_TRADING_DAY
            ),
            completed_jobs=completed,
        )
    )

    skipped = {
        window.name
        for window
        in second_evaluation.skipped
    }

    assert (
        CheckpointName
        .AFTER_SESSION_FALLBACK
        in skipped
    )
