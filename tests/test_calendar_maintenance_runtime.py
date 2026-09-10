from datetime import date

from app.core.calendar_maintenance_runtime import (
    build_calendar_maintenance_runtime,
)
from app.core.schedule import CalendarTruth
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


def build(tmp_path):
    database = Database(
        str(tmp_path / "platform.db")
    )
    database.initialize()

    scheduler = SchedulerRepository(database)

    runtime = build_calendar_maintenance_runtime(
        database=database,
        scheduler_repository=scheduler,
    )

    return database, scheduler, runtime


def test_dependency_graph_uses_shared_objects(
    tmp_path,
):
    database, scheduler, runtime = build(
        tmp_path
    )

    assert (
        runtime.trading_repository.database
        is database
    )
    assert (
        runtime.transition_repository.database
        is database
    )
    assert (
        runtime.holiday_evidence_repository.database
        is database
    )

    assert (
        runtime.base_service.transition_repository
        is runtime.transition_repository
    )
    assert (
        runtime.holiday_promotion_service
        .transition_repository
        is runtime.transition_repository
    )

    assert (
        runtime.execution_adapter
        .scheduler_repository
        is scheduler
    )
    assert (
        runtime.dispatcher.scheduler_repository
        is scheduler
    )
    assert (
        runtime.dispatcher.execution_adapter
        is runtime.execution_adapter
    )


def test_weekend_is_persisted_and_resolved(
    tmp_path,
):
    _, _, runtime = build(tmp_path)

    friday = date(2026, 9, 11)

    result = runtime.maintenance_job.run(
        friday
    )

    assert (
        result.base_status
        == MarketSessionStatus.WEEKEND
    )
    assert (
        result.calendar_truth
        == CalendarTruth.VERIFIED_NON_TRADING_DAY
    )

    session = (
        runtime.trading_repository
        .get_market_session(friday)
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )


def test_plain_weekday_remains_unverified(
    tmp_path,
):
    _, _, runtime = build(tmp_path)

    thursday = date(2026, 9, 10)

    result = runtime.maintenance_job.run(
        thursday
    )

    assert (
        result.base_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        result.holiday_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        result.calendar_truth
        == CalendarTruth.UNVERIFIED
    )

    assert (
        runtime.trading_repository
        .get_market_session(thursday)
        is None
    )
