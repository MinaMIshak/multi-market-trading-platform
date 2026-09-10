from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core import (
    CalendarTruth,
    CheckpointName,
    MarketSessionOrchestrator,
)
from app.core.calendar_maintenance_dispatcher import (
    CalendarMaintenanceDispatcher,
)
from app.core.calendar_maintenance_execution import (
    CalendarMaintenanceExecutionAdapter,
)
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


NOW = datetime(
    2026,
    9,
    10,
    8,
    31,
    tzinfo=ZoneInfo("Africa/Cairo"),
)

DAY = NOW.date()

CHECKPOINT = (
    CheckpointName.CALENDAR_MAINTENANCE
)


class SuccessfulMaintenanceJob:
    def __init__(self) -> None:
        self.calls = []

    def run(self, market_date):
        self.calls.append(market_date)

        return SimpleNamespace(
            market_date=market_date,
            base_status=(
                MarketSessionStatus.UNKNOWN
            ),
            holiday_status=(
                MarketSessionStatus.UNKNOWN
            ),
            calendar_truth=(
                CalendarTruth.UNVERIFIED
            ),
        )


class FailingMaintenanceJob:
    def __init__(self) -> None:
        self.calls = []

    def run(self, market_date):
        self.calls.append(market_date)

        raise RuntimeError(
            "sensitive-detail-must-not-leak"
        )


def build_runtime(
    tmp_path,
    maintenance_job,
):
    db_path = tmp_path / "platform.db"

    database = Database(
        str(db_path)
    )
    database.initialize()

    repository = SchedulerRepository(
        database
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    evaluation = orchestrator.evaluate(
        now=NOW,
        market_date=DAY,
        calendar_truth=(
            CalendarTruth.UNVERIFIED
        ),
        completed_jobs=set(),
    )

    repository.sync_evaluation(
        evaluation
    )

    adapter = (
        CalendarMaintenanceExecutionAdapter(
            scheduler_repository=repository,
            maintenance_job=maintenance_job,
        )
    )

    dispatcher = (
        CalendarMaintenanceDispatcher(
            scheduler_repository=repository,
            execution_adapter=adapter,
        )
    )

    return (
        repository,
        evaluation,
        dispatcher,
    )


def get_row(repository):
    row = repository.get_job(
        market_date=DAY,
        checkpoint_name=CHECKPOINT,
    )

    assert row is not None

    return row


def test_real_ledger_pending_to_succeeded(
    tmp_path,
):
    job = SuccessfulMaintenanceJob()

    (
        repository,
        evaluation,
        dispatcher,
    ) = build_runtime(
        tmp_path,
        job,
    )

    before = get_row(repository)

    assert before["status"] == "PENDING"
    assert before["attempt_count"] == 0

    outcomes = dispatcher.dispatch(
        evaluation=evaluation
    )

    assert len(outcomes) == 1
    assert outcomes[0].claimed is True
    assert outcomes[0].succeeded is True

    after = get_row(repository)

    assert after["status"] == "SUCCEEDED"
    assert after["attempt_count"] == 1
    assert after["last_error"] is None

    assert job.calls == [DAY]

    # Simulate the next scheduler poll.
    repository.sync_evaluation(
        evaluation
    )

    stable = get_row(repository)

    assert stable["status"] == "SUCCEEDED"
    assert stable["attempt_count"] == 1

    second = dispatcher.dispatch(
        evaluation=evaluation
    )

    assert second == ()
    assert job.calls == [DAY]


def test_real_ledger_failed_is_not_auto_retried(
    tmp_path,
):
    job = FailingMaintenanceJob()

    (
        repository,
        evaluation,
        dispatcher,
    ) = build_runtime(
        tmp_path,
        job,
    )

    before = get_row(repository)

    assert before["status"] == "PENDING"
    assert before["attempt_count"] == 0

    outcomes = dispatcher.dispatch(
        evaluation=evaluation
    )

    assert len(outcomes) == 1
    assert outcomes[0].claimed is None
    assert outcomes[0].succeeded is False
    assert outcomes[0].error_type == (
        "CalendarMaintenanceExecutionError"
    )

    failed = get_row(repository)

    assert failed["status"] == "FAILED"
    assert failed["attempt_count"] == 1
    assert failed["last_error"] == (
        "CALENDAR_MAINTENANCE_FAILED:"
        "RuntimeError"
    )

    assert (
        "sensitive-detail"
        not in failed["last_error"]
    )

    assert job.calls == [DAY]

    # Simulate the normal next polling cycle.
    # sync_evaluation must preserve FAILED.
    repository.sync_evaluation(
        evaluation
    )

    preserved = get_row(repository)

    assert preserved["status"] == "FAILED"
    assert preserved["attempt_count"] == 1

    second = dispatcher.dispatch(
        evaluation=evaluation
    )

    assert second == ()

    final = get_row(repository)

    assert final["status"] == "FAILED"
    assert final["attempt_count"] == 1

    # No second automatic execution.
    assert job.calls == [DAY]
