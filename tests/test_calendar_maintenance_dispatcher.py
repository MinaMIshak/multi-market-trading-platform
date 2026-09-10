from datetime import date
from types import SimpleNamespace

import pytest

from app.core.calendar_maintenance_dispatcher import (
    CalendarMaintenanceDispatcher,
)
from app.core.calendar_maintenance_execution import (
    CalendarMaintenanceExecutionError,
)
from app.core.job_state import SchedulerJobStatus
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.domain.enums import (
    MarketSessionStatus,
)


DAY = date(2026, 9, 10)


def evaluation(
    *,
    due=True,
    truth=CalendarTruth.UNVERIFIED,
):
    windows = []

    if due:
        windows.append(
            SimpleNamespace(
                name=(
                    CheckpointName
                    .CALENDAR_MAINTENANCE
                )
            )
        )

    return SimpleNamespace(
        market_date=DAY,
        calendar_truth=truth,
        due=windows,
    )


class FakeRepository:
    def __init__(self, status=None):
        self.status = status
        self.calls = []

    def get_job(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.calls.append(
            (market_date, checkpoint_name)
        )

        if self.status is None:
            return None

        return {
            "status": self.status.value
        }


class FakeExecutionAdapter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def execute(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.calls.append(
            (market_date, checkpoint_name)
        )

        if self.error is not None:
            raise self.error

        return SimpleNamespace(
            claimed=True,
            succeeded=True,
            calendar_truth=(
                CalendarTruth.UNVERIFIED
            ),
            base_status=(
                MarketSessionStatus.UNKNOWN
            ),
            holiday_status=(
                MarketSessionStatus.UNKNOWN
            ),
        )


def build(status, *, error=None):
    repository = FakeRepository(status)
    adapter = FakeExecutionAdapter(error)

    dispatcher = CalendarMaintenanceDispatcher(
        scheduler_repository=repository,
        execution_adapter=adapter,
    )

    return dispatcher, repository, adapter


def test_pending_due_is_selected_even_unverified():
    dispatcher, _, _ = build(
        SchedulerJobStatus.PENDING
    )

    selected = dispatcher.select_checkpoint(
        evaluation(
            truth=CalendarTruth.UNVERIFIED
        )
    )

    assert selected == (
        CheckpointName.CALENDAR_MAINTENANCE
    )


def test_not_due_is_not_selected():
    dispatcher, repository, adapter = build(
        SchedulerJobStatus.PENDING
    )

    result = dispatcher.dispatch(
        evaluation=evaluation(due=False)
    )

    assert result == ()
    assert repository.calls == []
    assert adapter.calls == []


@pytest.mark.parametrize(
    "status",
    [
        SchedulerJobStatus.RUNNING,
        SchedulerJobStatus.SUCCEEDED,
        SchedulerJobStatus.FAILED,
        SchedulerJobStatus.BLOCKED,
        SchedulerJobStatus.MISSED,
        SchedulerJobStatus.SKIPPED,
    ],
)
def test_only_pending_is_automatic(status):
    dispatcher, _, adapter = build(status)

    result = dispatcher.dispatch(
        evaluation=evaluation()
    )

    assert result == ()
    assert adapter.calls == []


def test_missing_ledger_row_does_not_execute():
    dispatcher, _, adapter = build(None)

    result = dispatcher.dispatch(
        evaluation=evaluation()
    )

    assert result == ()
    assert adapter.calls == []


def test_success_is_returned():
    dispatcher, _, adapter = build(
        SchedulerJobStatus.PENDING
    )

    result = dispatcher.dispatch(
        evaluation=evaluation()
    )

    assert len(result) == 1

    outcome = result[0]

    assert outcome.claimed is True
    assert outcome.succeeded is True
    assert (
        outcome.calendar_truth
        == CalendarTruth.UNVERIFIED
    )
    assert (
        outcome.base_status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        outcome.holiday_status
        == MarketSessionStatus.UNKNOWN
    )

    assert adapter.calls == [
        (
            DAY,
            CheckpointName.CALENDAR_MAINTENANCE,
        )
    ]


def test_execution_error_is_contained():
    dispatcher, _, _ = build(
        SchedulerJobStatus.PENDING,
        error=CalendarMaintenanceExecutionError(
            "internal-message"
        ),
    )

    result = dispatcher.dispatch(
        evaluation=evaluation()
    )

    assert len(result) == 1

    outcome = result[0]

    assert outcome.claimed is None
    assert outcome.succeeded is False
    assert outcome.error_type == (
        "CalendarMaintenanceExecutionError"
    )

    assert "internal-message" not in (
        outcome.error_type
    )


def test_calendar_truth_does_not_gate_maintenance():
    dispatcher, _, adapter = build(
        SchedulerJobStatus.PENDING
    )

    result = dispatcher.dispatch(
        evaluation=evaluation(
            truth=(
                CalendarTruth
                .VERIFIED_NON_TRADING_DAY
            )
        )
    )

    assert len(result) == 1
    assert len(adapter.calls) == 1
