from datetime import date
from types import SimpleNamespace

import pytest

from app.core.calendar_maintenance_execution import (
    CalendarMaintenanceExecutionAdapter,
    CalendarMaintenanceExecutionError,
)
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.domain.enums import (
    MarketSessionStatus,
)


DAY = date(2026, 9, 10)


class FakeRepository:
    def __init__(
        self,
        *,
        claim=True,
        succeed=True,
        fail=True,
    ):
        self.claim = claim
        self.succeed = succeed
        self.fail = fail
        self.calls = []
        self.failure_error = None

    def claim_job(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.calls.append("claim")
        return self.claim

    def mark_succeeded(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.calls.append("succeed")
        return self.succeed

    def mark_failed(
        self,
        *,
        market_date,
        checkpoint_name,
        error,
    ):
        self.calls.append("fail")
        self.failure_error = error
        return self.fail


class FakeJob:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def run(self, market_date):
        self.calls.append(market_date)

        if self.error is not None:
            raise self.error

        return SimpleNamespace(
            calendar_truth=CalendarTruth.UNVERIFIED,
            base_status=MarketSessionStatus.UNKNOWN,
            holiday_status=MarketSessionStatus.UNKNOWN,
        )


def test_only_calendar_maintenance_is_allowed():
    repo = FakeRepository()
    job = FakeJob()

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    with pytest.raises(
        ValueError,
        match="not allowed",
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=(
                CheckpointName.MASTER_HEALTH_0945
            ),
        )

    assert repo.calls == []
    assert job.calls == []


def test_unclaimed_job_does_not_run():
    repo = FakeRepository(claim=False)
    job = FakeJob()

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    result = adapter.execute(
        market_date=DAY,
        checkpoint_name=(
            CheckpointName.CALENDAR_MAINTENANCE
        ),
    )

    assert result.claimed is False
    assert result.succeeded is False
    assert repo.calls == ["claim"]
    assert job.calls == []


def test_success_completes_running_job():
    repo = FakeRepository()
    job = FakeJob()

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    result = adapter.execute(
        market_date=DAY,
        checkpoint_name=(
            CheckpointName.CALENDAR_MAINTENANCE
        ),
    )

    assert result.claimed is True
    assert result.succeeded is True
    assert (
        result.calendar_truth
        == CalendarTruth.UNVERIFIED
    )
    assert repo.calls == [
        "claim",
        "succeed",
    ]
    assert job.calls == [DAY]


def test_failure_is_sanitized_and_persisted():
    repo = FakeRepository()
    job = FakeJob(
        RuntimeError(
            "secret-value-must-not-leak"
        )
    )

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    with pytest.raises(
        CalendarMaintenanceExecutionError,
        match=(
            "CALENDAR_MAINTENANCE_FAILED:"
            "RuntimeError"
        ),
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=(
                CheckpointName
                .CALENDAR_MAINTENANCE
            ),
        )

    assert repo.calls == [
        "claim",
        "fail",
    ]

    assert repo.failure_error == (
        "CALENDAR_MAINTENANCE_FAILED:"
        "RuntimeError"
    )

    assert (
        "secret-value"
        not in repo.failure_error
    )


def test_failed_failure_persistence_is_fatal():
    repo = FakeRepository(fail=False)
    job = FakeJob(RuntimeError("boom"))

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    with pytest.raises(
        CalendarMaintenanceExecutionError,
        match="persist calendar maintenance failure",
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=(
                CheckpointName
                .CALENDAR_MAINTENANCE
            ),
        )


def test_failed_success_persistence_is_fatal():
    repo = FakeRepository(succeed=False)
    job = FakeJob()

    adapter = CalendarMaintenanceExecutionAdapter(
        scheduler_repository=repo,
        maintenance_job=job,
    )

    with pytest.raises(
        CalendarMaintenanceExecutionError,
        match="persist calendar maintenance success",
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=(
                CheckpointName
                .CALENDAR_MAINTENANCE
            ),
        )
