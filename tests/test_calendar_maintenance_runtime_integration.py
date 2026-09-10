from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import (
    CalendarTruth,
    CheckpointName,
    MarketSessionOrchestrator,
)
from app.core.calendar_maintenance_runtime import (
    build_calendar_maintenance_runtime,
)
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


TZ = ZoneInfo("Africa/Cairo")

CHECKPOINT = (
    CheckpointName.CALENDAR_MAINTENANCE
)


def build(tmp_path):
    database = Database(
        str(tmp_path / "platform.db")
    )
    database.initialize()

    scheduler = SchedulerRepository(
        database
    )

    runtime = build_calendar_maintenance_runtime(
        database=database,
        scheduler_repository=scheduler,
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    return (
        database,
        scheduler,
        runtime,
        orchestrator,
    )


def evaluate_and_sync(
    *,
    scheduler,
    runtime,
    orchestrator,
    now,
):
    market_date = now.date()

    truth = runtime.truth_resolver.resolve(
        market_date
    )

    completed = (
        scheduler.successful_checkpoints(
            market_date
        )
    )

    evaluation = orchestrator.evaluate(
        now=now,
        market_date=market_date,
        calendar_truth=truth,
        completed_jobs=completed,
    )

    scheduler.sync_evaluation(
        evaluation
    )

    return evaluation


def get_calendar_job(
    scheduler,
    market_date,
):
    row = scheduler.get_job(
        market_date=market_date,
        checkpoint_name=CHECKPOINT,
    )

    assert row is not None

    return row


def test_friday_composed_runtime_persists_weekend(
    tmp_path,
):
    (
        _,
        scheduler,
        runtime,
        orchestrator,
    ) = build(tmp_path)

    now = datetime(
        2026,
        9,
        11,
        8,
        31,
        tzinfo=TZ,
    )

    market_date = now.date()

    evaluation = evaluate_and_sync(
        scheduler=scheduler,
        runtime=runtime,
        orchestrator=orchestrator,
        now=now,
    )

    before = get_calendar_job(
        scheduler,
        market_date,
    )

    assert before["status"] == "PENDING"
    assert before["attempt_count"] == 0

    outcomes = runtime.dispatcher.dispatch(
        evaluation=evaluation
    )

    assert len(outcomes) == 1

    outcome = outcomes[0]

    assert outcome.claimed is True
    assert outcome.succeeded is True
    assert (
        outcome.base_status
        == MarketSessionStatus.WEEKEND
    )
    assert (
        outcome.holiday_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        outcome.calendar_truth
        == CalendarTruth.VERIFIED_NON_TRADING_DAY
    )

    session = (
        runtime.trading_repository
        .get_market_session(
            market_date
        )
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )

    after = get_calendar_job(
        scheduler,
        market_date,
    )

    assert after["status"] == "SUCCEEDED"
    assert after["attempt_count"] == 1
    assert after["last_error"] is None

    assert (
        runtime.truth_resolver.resolve(
            market_date
        )
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )

    # Real next polling cycle.
    second_evaluation = evaluate_and_sync(
        scheduler=scheduler,
        runtime=runtime,
        orchestrator=orchestrator,
        now=now,
    )

    second = runtime.dispatcher.dispatch(
        evaluation=second_evaluation
    )

    assert second == ()

    stable = get_calendar_job(
        scheduler,
        market_date,
    )

    assert stable["status"] == "SUCCEEDED"
    assert stable["attempt_count"] == 1


def test_thursday_composed_runtime_stays_unverified(
    tmp_path,
):
    (
        _,
        scheduler,
        runtime,
        orchestrator,
    ) = build(tmp_path)

    now = datetime(
        2026,
        9,
        10,
        8,
        31,
        tzinfo=TZ,
    )

    market_date = now.date()

    evaluation = evaluate_and_sync(
        scheduler=scheduler,
        runtime=runtime,
        orchestrator=orchestrator,
        now=now,
    )

    before = get_calendar_job(
        scheduler,
        market_date,
    )

    assert before["status"] == "PENDING"
    assert before["attempt_count"] == 0

    outcomes = runtime.dispatcher.dispatch(
        evaluation=evaluation
    )

    assert len(outcomes) == 1

    outcome = outcomes[0]

    assert outcome.claimed is True
    assert outcome.succeeded is True
    assert (
        outcome.base_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        outcome.holiday_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        outcome.calendar_truth
        == CalendarTruth.UNVERIFIED
    )

    assert (
        runtime.trading_repository
        .get_market_session(
            market_date
        )
        is None
    )

    after = get_calendar_job(
        scheduler,
        market_date,
    )

    assert after["status"] == "SUCCEEDED"
    assert after["attempt_count"] == 1
    assert after["last_error"] is None

    assert (
        runtime.truth_resolver.resolve(
            market_date
        )
        == CalendarTruth.UNVERIFIED
    )

    # Maintenance completed successfully,
    # but no trading-day truth was fabricated.
    second_evaluation = evaluate_and_sync(
        scheduler=scheduler,
        runtime=runtime,
        orchestrator=orchestrator,
        now=now,
    )

    second = runtime.dispatcher.dispatch(
        evaluation=second_evaluation
    )

    assert second == ()

    stable = get_calendar_job(
        scheduler,
        market_date,
    )

    assert stable["status"] == "SUCCEEDED"
    assert stable["attempt_count"] == 1

    assert (
        runtime.trading_repository
        .get_market_session(
            market_date
        )
        is None
    )
