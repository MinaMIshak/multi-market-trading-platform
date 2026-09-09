from datetime import date, datetime
from zoneinfo import ZoneInfo

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


MARKET_DATE = date(2026, 9, 10)
CAIRO = ZoneInfo("Africa/Cairo")


def test_failed_survives_resync_and_remains_retryable(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = SchedulerRepository(
        database
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    first = orchestrator.evaluate(
        now=datetime(
            2026,
            9,
            10,
            16,
            15,
            tzinfo=CAIRO,
        ),
        market_date=MARKET_DATE,
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=set(),
    )

    repository.sync_evaluation(first)

    assert repository.claim_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert repository.mark_failed(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
        error="TEST_FAILURE",
    )

    second = orchestrator.evaluate(
        now=datetime(
            2026,
            9,
            10,
            18,
            15,
            tzinfo=CAIRO,
        ),
        market_date=MARKET_DATE,
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=(
            repository
            .successful_checkpoints(
                MARKET_DATE
            )
        ),
    )

    repository.sync_evaluation(second)

    with database.connect() as con:
        row = con.execute(
            """
            SELECT
                status,
                attempt_count,
                last_error
            FROM scheduled_jobs
            WHERE market_date = ?
              AND checkpoint_name = ?
            """,
            (
                MARKET_DATE.isoformat(),
                CheckpointName
                .AFTER_SESSION_PRIMARY
                .value,
            ),
        ).fetchone()

    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 1
    assert row["last_error"] == "TEST_FAILURE"

    # Preservation must not disable an
    # intentional retry of the checkpoint.
    assert repository.claim_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    with database.connect() as con:
        retried = con.execute(
            """
            SELECT
                status,
                attempt_count,
                last_error
            FROM scheduled_jobs
            WHERE market_date = ?
              AND checkpoint_name = ?
            """,
            (
                MARKET_DATE.isoformat(),
                CheckpointName
                .AFTER_SESSION_PRIMARY
                .value,
            ),
        ).fetchone()

    assert retried["status"] == "RUNNING"
    assert retried["attempt_count"] == 2
    assert retried["last_error"] is None
