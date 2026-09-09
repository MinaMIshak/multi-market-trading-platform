from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.daily_refresh_dispatcher import (
    DailyRefreshDispatcher,
)
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
from app.data.daily_refresh_job import (
    DailyRefreshJobError,
)
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


MARKET_DATE = date(2026, 9, 10)
CAIRO = ZoneInfo("Africa/Cairo")


class ControlledRefreshJob:
    def __init__(self):
        self.calls = []
        self.fail = True

    def run(self, **kwargs):
        self.calls.append(kwargs)

        if self.fail:
            raise DailyRefreshJobError(
                canonical_symbol="COMI",
                completed=(),
                cause=ValueError(
                    "private provider detail"
                ),
            )

        return SimpleNamespace(
            items=(object(),)
        )


def evaluate_and_sync(
    *,
    repository,
    orchestrator,
    hour,
    minute,
):
    evaluation = orchestrator.evaluate(
        now=datetime(
            2026,
            9,
            10,
            hour,
            minute,
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

    repository.sync_evaluation(
        evaluation
    )

    return evaluation


def test_failed_primary_no_poll_storm_then_fallback(
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

    refresh_job = ControlledRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repository,
        refresh_job=refresh_job,
        lookback_days=400,
    )

    dispatcher = DailyRefreshDispatcher(
        scheduler_repository=repository,
        execution_adapter=adapter,
    )

    primary_evaluation = (
        evaluate_and_sync(
            repository=repository,
            orchestrator=orchestrator,
            hour=16,
            minute=15,
        )
    )

    first = dispatcher.dispatch(
        evaluation=primary_evaluation,
        provider=object(),
    )

    assert len(first) == 1
    assert first[0].succeeded is False
    assert (
        first[0].error_type
        == "DailyRefreshJobError"
    )

    primary = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert primary["status"] == "FAILED"
    assert primary["attempt_count"] == 1
    assert (
        primary["last_error"]
        == "DAILY_REFRESH_FAILED:COMI:ValueError"
    )

    assert (
        "private provider detail"
        not in primary["last_error"]
    )

    assert len(refresh_job.calls) == 1

    # Simulate repeated scheduler polling with
    # the same due evaluation. FAILED must not
    # be automatically claimed again.
    for _ in range(3):
        assert dispatcher.dispatch(
            evaluation=primary_evaluation,
            provider=object(),
        ) == ()

    assert len(refresh_job.calls) == 1

    primary = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert primary["status"] == "FAILED"
    assert primary["attempt_count"] == 1

    refresh_job.fail = False

    fallback_evaluation = (
        evaluate_and_sync(
            repository=repository,
            orchestrator=orchestrator,
            hour=18,
            minute=15,
        )
    )

    primary = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    fallback = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
    )

    assert primary["status"] == "FAILED"
    assert primary["attempt_count"] == 1

    assert fallback["status"] == "PENDING"
    assert fallback["attempt_count"] == 0

    outcome = dispatcher.dispatch(
        evaluation=fallback_evaluation,
        provider=object(),
    )

    assert len(outcome) == 1
    assert (
        outcome[0].checkpoint_name
        == CheckpointName
        .AFTER_SESSION_FALLBACK
    )
    assert outcome[0].claimed is True
    assert outcome[0].succeeded is True
    assert outcome[0].item_count == 1

    assert len(refresh_job.calls) == 2

    primary = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    fallback = repository.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
    )

    assert primary["status"] == "FAILED"
    assert primary["attempt_count"] == 1

    assert fallback["status"] == "SUCCEEDED"
    assert fallback["attempt_count"] == 1

    with database.connect() as con:
        assert (
            con.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]
            == "ok"
        )
