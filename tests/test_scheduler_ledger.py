from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from app.core import (
    CalendarTruth,
    CheckpointName,
    MarketSessionOrchestrator,
)
from app.storage import (
    Database,
    SchedulerRepository,
)


CAIRO = ZoneInfo("Africa/Cairo")


def at(
    hour: int,
    minute: int,
) -> datetime:
    return datetime(
        2026,
        9,
        9,
        hour,
        minute,
        tzinfo=CAIRO,
    )


def test_scheduler_ledger_is_idempotent():
    with TemporaryDirectory() as tmp:
        db = Database(
            Path(tmp) / "scheduler.db"
        )

        db.initialize()

        repo = SchedulerRepository(db)
        orchestrator = (
            MarketSessionOrchestrator()
        )

        result = orchestrator.evaluate(
            now=at(10, 16),
            market_date=at(10, 16).date(),
            calendar_truth=(
                CalendarTruth.UNVERIFIED
            ),
        )

        repo.sync_evaluation(result)

        first_count = repo.count_jobs(
            at(10, 16).date()
        )

        repo.sync_evaluation(result)

        second_count = repo.count_jobs(
            at(10, 16).date()
        )

        assert first_count == second_count
