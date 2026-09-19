from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.calendar_live_dispatcher import (
    CalendarLiveDispatcher,
)
from app.core.calendar_live_execution import (
    CalendarLiveExecutionAdapter,
    CalendarLiveExecutionError,
)
from app.core.job_state import SchedulerJobStatus
from app.core.schedule import CheckpointName
from app.domain.enums import MarketSessionStatus


DAY = date(2026, 9, 20)


class FakeSchedulerRepository:
    def __init__(
        self,
        *,
        claim_result=True,
        statuses=None,
    ):
        self.claim_result = claim_result
        self.statuses = statuses or {}
        self.claimed = []
        self.succeeded = []
        self.failed = []

    def claim_job(
        self,
        *,
        market_date,
        checkpoint_name,
        stale_after_seconds=300,
    ):
        del stale_after_seconds
        self.claimed.append(
            (market_date, checkpoint_name)
        )
        return self.claim_result

    def mark_succeeded(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.succeeded.append(
            (market_date, checkpoint_name)
        )
        return True

    def mark_failed(
        self,
        *,
        market_date,
        checkpoint_name,
        error,
    ):
        self.failed.append(
            (market_date, checkpoint_name, error)
        )
        return True

    def get_job(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        del market_date

        status = self.statuses.get(
            checkpoint_name
        )

        if status is None:
            return None

        return {
            "status": status.value
        }


class FakeVerificationService:
    def __init__(
        self,
        status=MarketSessionStatus.VERIFIED,
        error=None,
    ):
        self.status = status
        self.error = error
        self.calls = []

    def verify(
        self,
        market_date,
        *,
        verified_at=None,
    ):
        self.calls.append(
            (market_date, verified_at)
        )

        if self.error is not None:
            raise self.error

        return SimpleNamespace(
            status=self.status
        )


def evaluation(*names):
    return SimpleNamespace(
        market_date=DAY,
        due=[
            SimpleNamespace(name=name)
            for name in names
        ],
    )


def test_execution_promotes_verified_result():
    repository = FakeSchedulerRepository()
    verification = FakeVerificationService(
        MarketSessionStatus.VERIFIED
    )

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=verification,
    )

    checkpoint = (
        CheckpointName.CALENDAR_LIVE_1005
    )

    result = adapter.execute(
        market_date=DAY,
        checkpoint_name=checkpoint,
    )

    assert result.claimed is True
    assert result.succeeded is True
    assert (
        result.verification_status
        == MarketSessionStatus.VERIFIED
    )

    assert repository.claimed == [
        (DAY, checkpoint)
    ]
    assert repository.succeeded == [
        (DAY, checkpoint)
    ]
    assert repository.failed == []

    assert verification.calls[0][0] == DAY
    assert verification.calls[0][1] is not None


def test_unknown_evidence_is_successful_check_not_truth():
    repository = FakeSchedulerRepository()
    verification = FakeVerificationService(
        MarketSessionStatus.UNKNOWN
    )

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=verification,
    )

    checkpoint = (
        CheckpointName.CALENDAR_LIVE_1008
    )

    result = adapter.execute(
        market_date=DAY,
        checkpoint_name=checkpoint,
    )

    assert result.succeeded is True
    assert (
        result.verification_status
        == MarketSessionStatus.UNKNOWN
    )

    # The checkpoint executed successfully, but
    # UNKNOWN remains fail-closed calendar truth.
    assert repository.succeeded == [
        (DAY, checkpoint)
    ]


def test_unclaimed_job_does_not_verify():
    repository = FakeSchedulerRepository(
        claim_result=False
    )
    verification = FakeVerificationService()

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=verification,
    )

    result = adapter.execute(
        market_date=DAY,
        checkpoint_name=(
            CheckpointName.CALENDAR_LIVE_1011
        ),
    )

    assert result.claimed is False
    assert result.succeeded is False
    assert verification.calls == []
    assert repository.succeeded == []
    assert repository.failed == []


def test_verification_failure_is_safely_recorded():
    repository = FakeSchedulerRepository()

    verification = FakeVerificationService(
        error=RuntimeError(
            "do-not-persist-sensitive-detail"
        )
    )

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=verification,
    )

    checkpoint = (
        CheckpointName.CALENDAR_LIVE_1013
    )

    with pytest.raises(
        CalendarLiveExecutionError
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=checkpoint,
        )

    assert len(repository.failed) == 1

    _, _, message = repository.failed[0]

    assert (
        message
        == "CALENDAR_LIVE_FAILED:RuntimeError"
    )
    assert (
        "do-not-persist-sensitive-detail"
        not in message
    )


def test_rejects_non_calendar_live_checkpoint():
    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=(
            FakeSchedulerRepository()
        ),
        verification_service=(
            FakeVerificationService()
        ),
    )

    with pytest.raises(
        ValueError,
        match="not allowed",
    ):
        adapter.execute(
            market_date=DAY,
            checkpoint_name=(
                CheckpointName
                .AFTER_SESSION_PRIMARY
            ),
        )


def test_dispatcher_selects_first_due_pending():
    statuses = {
        CheckpointName.CALENDAR_LIVE_1005:
            SchedulerJobStatus.SUCCEEDED,
        CheckpointName.CALENDAR_LIVE_1008:
            SchedulerJobStatus.PENDING,
        CheckpointName.CALENDAR_LIVE_1011:
            SchedulerJobStatus.PENDING,
    }

    repository = FakeSchedulerRepository(
        statuses=statuses
    )

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=(
            FakeVerificationService(
                MarketSessionStatus.UNKNOWN
            )
        ),
    )

    dispatcher = CalendarLiveDispatcher(
        scheduler_repository=repository,
        execution_adapter=adapter,
    )

    checkpoint = dispatcher.select_checkpoint(
        evaluation(
            CheckpointName.CALENDAR_LIVE_1005,
            CheckpointName.CALENDAR_LIVE_1008,
            CheckpointName.CALENDAR_LIVE_1011,
        )
    )

    assert (
        checkpoint
        == CheckpointName.CALENDAR_LIVE_1008
    )


def test_dispatcher_does_not_auto_retry_failed_job():
    repository = FakeSchedulerRepository(
        statuses={
            CheckpointName.CALENDAR_LIVE_1005:
                SchedulerJobStatus.FAILED
        }
    )

    dispatcher = CalendarLiveDispatcher(
        scheduler_repository=repository,
        execution_adapter=SimpleNamespace(),
    )

    checkpoint = dispatcher.select_checkpoint(
        evaluation(
            CheckpointName.CALENDAR_LIVE_1005
        )
    )

    assert checkpoint is None


def test_dispatcher_runs_pending_verification():
    checkpoint = (
        CheckpointName.CALENDAR_LIVE_1005
    )

    repository = FakeSchedulerRepository(
        statuses={
            checkpoint:
                SchedulerJobStatus.PENDING
        }
    )

    adapter = CalendarLiveExecutionAdapter(
        scheduler_repository=repository,
        verification_service=(
            FakeVerificationService(
                MarketSessionStatus.VERIFIED
            )
        ),
    )

    dispatcher = CalendarLiveDispatcher(
        scheduler_repository=repository,
        execution_adapter=adapter,
    )

    outcomes = dispatcher.dispatch(
        evaluation=evaluation(checkpoint)
    )

    assert len(outcomes) == 1
    assert outcomes[0].checkpoint_name == checkpoint
    assert outcomes[0].claimed is True
    assert outcomes[0].succeeded is True
    assert (
        outcomes[0].verification_status
        == MarketSessionStatus.VERIFIED
    )


def test_worker_defaults_calendar_live_to_disabled():
    source = Path(
        "app/core/scheduler_worker.py"
    ).read_text()

    assert "EGX_CALENDAR_LIVE_MODE" in source
    assert '"disabled"' in source
    assert "build_calendar_live_runtime" in source
    assert "CALENDAR_LIVE_DISPATCH" in source


def test_runtime_contains_no_network_provider():
    source = Path(
        "app/core/calendar_live_runtime.py"
    ).read_text()

    assert (
        "OfficialIndexEvidenceRepository"
        in source
    )

    assert (
        "CalendarVerificationService"
        in source
    )

    assert (
        "EGXOfficialPublicProvider"
        not in source
    )

    assert (
        "IndexHistoryIngestor"
        not in source
    )
