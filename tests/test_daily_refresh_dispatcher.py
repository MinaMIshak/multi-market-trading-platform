from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.daily_refresh_dispatcher import (
    DailyRefreshDispatcher,
)
from app.core.daily_refresh_execution import (
    DailyRefreshExecutionError,
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


MARKET_DATE = date(2026, 9, 10)
CAIRO = ZoneInfo("Africa/Cairo")


class FakeAdapter:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def execute(self, **kwargs):
        self.calls.append(kwargs)

        if self.exc is not None:
            raise self.exc

        return SimpleNamespace(
            claimed=True,
            succeeded=True,
            item_count=5,
        )


def make_repo(tmp_path):
    db = Database(
        tmp_path / "platform.db"
    )
    db.initialize()

    return SchedulerRepository(db)


def evaluate(
    repository,
    *,
    hour,
    minute,
    truth=CalendarTruth.VERIFIED_TRADING_DAY,
):
    orchestrator = (
        MarketSessionOrchestrator()
    )

    result = orchestrator.evaluate(
        now=datetime(
            2026,
            9,
            10,
            hour,
            minute,
            tzinfo=CAIRO,
        ),
        market_date=MARKET_DATE,
        calendar_truth=truth,
        completed_jobs=(
            repository
            .successful_checkpoints(
                MARKET_DATE
            )
        ),
    )

    repository.sync_evaluation(result)

    return result


def make_dispatcher(repository, adapter):
    return DailyRefreshDispatcher(
        scheduler_repository=repository,
        execution_adapter=adapter,
    )


def test_unverified_never_dispatches(tmp_path):
    repo = make_repo(tmp_path)
    adapter = FakeAdapter()

    result = evaluate(
        repo,
        hour=16,
        minute=15,
        truth=CalendarTruth.UNVERIFIED,
    )

    outcomes = make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    )

    assert outcomes == ()
    assert adapter.calls == []


def test_pending_primary_dispatches(tmp_path):
    repo = make_repo(tmp_path)
    adapter = FakeAdapter()

    result = evaluate(
        repo,
        hour=16,
        minute=15,
    )

    outcomes = make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    )

    assert len(outcomes) == 1
    assert (
        outcomes[0].checkpoint_name
        == CheckpointName
        .AFTER_SESSION_PRIMARY
    )

    assert len(adapter.calls) == 1


def test_failed_primary_is_not_poll_retried(
    tmp_path,
):
    repo = make_repo(tmp_path)

    evaluate(
        repo,
        hour=16,
        minute=15,
    )

    assert repo.claim_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert repo.mark_failed(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
        error="TEST_FAILURE",
    )

    result = evaluate(
        repo,
        hour=16,
        minute=16,
    )

    adapter = FakeAdapter()
    dispatcher = make_dispatcher(
        repo,
        adapter,
    )

    for _ in range(3):
        assert dispatcher.dispatch(
            evaluation=result,
            provider=object(),
        ) == ()

    assert adapter.calls == []

    row = repo.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 1


def test_fallback_wins_at_1815(tmp_path):
    repo = make_repo(tmp_path)
    adapter = FakeAdapter()

    result = evaluate(
        repo,
        hour=18,
        minute=15,
    )

    primary = repo.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    fallback = repo.get_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
    )

    assert primary["status"] == "PENDING"
    assert fallback["status"] == "PENDING"

    outcomes = make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    )

    assert len(outcomes) == 1
    assert (
        outcomes[0].checkpoint_name
        == CheckpointName
        .AFTER_SESSION_FALLBACK
    )

    assert len(adapter.calls) == 1


def test_running_primary_blocks_fallback(
    tmp_path,
):
    repo = make_repo(tmp_path)

    result = evaluate(
        repo,
        hour=18,
        minute=15,
    )

    assert repo.claim_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    adapter = FakeAdapter()

    assert make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    ) == ()

    assert adapter.calls == []


def test_succeeded_primary_blocks_stale_fallback(
    tmp_path,
):
    repo = make_repo(tmp_path)

    result = evaluate(
        repo,
        hour=18,
        minute=15,
    )

    assert repo.claim_job(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    assert repo.mark_succeeded(
        market_date=MARKET_DATE,
        checkpoint_name=(
            CheckpointName
            .AFTER_SESSION_PRIMARY
        ),
    )

    adapter = FakeAdapter()

    assert make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    ) == ()

    assert adapter.calls == []


def test_execution_error_is_contained(
    tmp_path,
):
    repo = make_repo(tmp_path)

    result = evaluate(
        repo,
        hour=16,
        minute=15,
    )

    adapter = FakeAdapter(
        DailyRefreshExecutionError(
            "internal test detail"
        )
    )

    outcomes = make_dispatcher(
        repo,
        adapter,
    ).dispatch(
        evaluation=result,
        provider=object(),
    )

    assert len(outcomes) == 1
    assert outcomes[0].claimed is None
    assert outcomes[0].succeeded is False
    assert (
        outcomes[0].error_type
        == "DailyRefreshExecutionError"
    )

    assert (
        "internal test detail"
        not in repr(outcomes[0])
    )
