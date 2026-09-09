from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.core.daily_refresh_execution import (
    DailyRefreshExecutionAdapter,
    DailyRefreshExecutionError,
)
from app.core.schedule import CheckpointName
from app.data.daily_refresh_job import (
    DailyRefreshJobError,
)


MARKET_DATE = date(2026, 9, 9)


class FakeSchedulerRepository:
    def __init__(
        self,
        *,
        claims=None,
        mark_success=True,
        mark_failure=True,
    ):
        self.claims = list(
            claims
            if claims is not None
            else [True]
        )
        self.mark_success = mark_success
        self.mark_failure = mark_failure

        self.claim_calls = []
        self.success_calls = []
        self.failure_calls = []
        self.completed = set()

    def successful_checkpoints(
        self,
        market_date,
    ):
        del market_date
        return set(self.completed)

    def claim_job(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.claim_calls.append(
            (
                market_date,
                checkpoint_name,
            )
        )

        if not self.claims:
            return False

        return self.claims.pop(0)

    def mark_succeeded(
        self,
        *,
        market_date,
        checkpoint_name,
    ):
        self.success_calls.append(
            (
                market_date,
                checkpoint_name,
            )
        )
        return self.mark_success

    def mark_failed(
        self,
        *,
        market_date,
        checkpoint_name,
        error,
    ):
        self.failure_calls.append(
            (
                market_date,
                checkpoint_name,
                error,
            )
        )
        return self.mark_failure


class FakeRefreshJob:
    def __init__(
        self,
        *,
        failure=None,
    ):
        self.failure = failure
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)

        if self.failure is not None:
            raise self.failure

        return SimpleNamespace(
            items=(
                object(),
                object(),
            )
        )


def execute(
    adapter,
    *,
    checkpoint=(
        CheckpointName
        .AFTER_SESSION_PRIMARY
    ),
):
    return adapter.execute(
        market_date=MARKET_DATE,
        checkpoint_name=checkpoint,
        provider=object(),
    )


def test_claim_then_success():
    repo = FakeSchedulerRepository()
    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
        lookback_days=400,
    )

    result = execute(adapter)

    assert result.claimed is True
    assert result.succeeded is True
    assert result.item_count == 2

    assert repo.success_calls == [
        (
            MARKET_DATE,
            CheckpointName
            .AFTER_SESSION_PRIMARY,
        )
    ]

    assert repo.failure_calls == []

    assert len(job.calls) == 1

    call = job.calls[0]

    assert call["start_date"] == (
        MARKET_DATE
        - timedelta(days=400)
    )
    assert call["end_date"] == MARKET_DATE
    assert (
        call["snapshot_date"]
        == MARKET_DATE
    )


def test_unclaimed_job_does_not_run():
    repo = FakeSchedulerRepository(
        claims=[False]
    )
    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    result = execute(adapter)

    assert result.claimed is False
    assert result.succeeded is False
    assert result.item_count == 0

    assert job.calls == []
    assert repo.success_calls == []
    assert repo.failure_calls == []


def test_failure_marks_job_failed():
    repo = FakeSchedulerRepository()

    failure = RuntimeError(
        "provider failed"
    )

    job = FakeRefreshJob(
        failure=failure
    )

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(
        RuntimeError,
        match="provider failed",
    ):
        execute(adapter)

    assert repo.success_calls == []

    assert len(repo.failure_calls) == 1

    assert repo.failure_calls[0][2] == (
        "DAILY_REFRESH_FAILED:"
        "RuntimeError"
    )


def test_retry_reuses_same_dates():
    repo = FakeSchedulerRepository(
        claims=[True, True]
    )

    class FailOnceJob:
        def __init__(self):
            self.calls = []
            self.failed = False

        def run(self, **kwargs):
            self.calls.append(kwargs)

            if not self.failed:
                self.failed = True
                raise RuntimeError(
                    "temporary"
                )

            return SimpleNamespace(
                items=(object(),)
            )

    job = FailOnceJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
        lookback_days=400,
    )

    with pytest.raises(RuntimeError):
        execute(adapter)

    result = execute(adapter)

    assert result.succeeded is True
    assert len(job.calls) == 2

    first = job.calls[0]
    second = job.calls[1]

    assert first["start_date"] == (
        second["start_date"]
    )
    assert first["end_date"] == (
        second["end_date"]
    )
    assert first["snapshot_date"] == (
        second["snapshot_date"]
    )


def test_fallback_uses_same_market_contract():
    repo = FakeSchedulerRepository()
    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
        lookback_days=400,
    )

    result = execute(
        adapter,
        checkpoint=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
    )

    assert result.succeeded is True

    assert repo.claim_calls == [
        (
            MARKET_DATE,
            CheckpointName
            .AFTER_SESSION_FALLBACK,
        )
    ]

    call = job.calls[0]

    assert call["start_date"] == (
        MARKET_DATE
        - timedelta(days=400)
    )
    assert call["end_date"] == MARKET_DATE
    assert (
        call["snapshot_date"]
        == MARKET_DATE
    )


def test_wrong_checkpoint_fails_before_claim():
    repo = FakeSchedulerRepository()
    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(
        ValueError,
        match="checkpoint is not allowed",
    ):
        execute(
            adapter,
            checkpoint=(
                CheckpointName.D1_OPEN
            ),
        )

    assert repo.claim_calls == []
    assert job.calls == []


def test_secret_text_never_enters_failure_ledger():
    secret = (
        "SUPER_PRIVATE_TOKEN_"
        "DO_NOT_PERSIST"
    )

    repo = FakeSchedulerRepository()

    job = FakeRefreshJob(
        failure=RuntimeError(
            "request failed token="
            + secret
        )
    )

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(RuntimeError):
        execute(adapter)

    stored_error = (
        repo.failure_calls[0][2]
    )

    assert secret not in stored_error
    assert "token=" not in stored_error
    assert stored_error == (
        "DAILY_REFRESH_FAILED:"
        "RuntimeError"
    )


def test_daily_refresh_error_is_sanitized():
    secret = "SECRET_VALUE"

    cause = RuntimeError(
        "network failure "
        + secret
    )

    failure = DailyRefreshJobError(
        canonical_symbol="COMI",
        completed=(),
        cause=cause,
    )

    repo = FakeSchedulerRepository()
    job = FakeRefreshJob(
        failure=failure
    )

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(
        DailyRefreshJobError
    ):
        execute(adapter)

    stored_error = (
        repo.failure_calls[0][2]
    )

    assert secret not in stored_error

    assert stored_error == (
        "DAILY_REFRESH_FAILED:"
        "COMI:RuntimeError"
    )


def test_failed_success_persistence_fails_closed():
    repo = FakeSchedulerRepository(
        mark_success=False
    )
    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(
        DailyRefreshExecutionError,
        match="persist.*success",
    ):
        execute(adapter)


def test_failed_failure_persistence_fails_closed():
    repo = FakeSchedulerRepository(
        mark_failure=False
    )
    job = FakeRefreshJob(
        failure=RuntimeError(
            "temporary"
        )
    )

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    with pytest.raises(
        DailyRefreshExecutionError,
        match="persist.*failure",
    ):
        execute(adapter)

def test_fallback_is_suppressed_after_primary_success():
    repo = FakeSchedulerRepository()

    repo.completed.add(
        CheckpointName
        .AFTER_SESSION_PRIMARY
    )

    job = FakeRefreshJob()

    adapter = DailyRefreshExecutionAdapter(
        scheduler_repository=repo,
        refresh_job=job,
    )

    result = execute(
        adapter,
        checkpoint=(
            CheckpointName
            .AFTER_SESSION_FALLBACK
        ),
    )

    assert result.claimed is False
    assert result.succeeded is False

    assert repo.claim_calls == []
    assert repo.success_calls == []
    assert repo.failure_calls == []
    assert job.calls == []
