from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.daily_refresh_dispatcher import (
    DailyRefreshDispatcher,
)
from app.core.scheduler_execution_context import (
    SchedulerMode,
    build_scheduler_execution_context,
)


class ForbiddenCall:
    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        raise AssertionError(
            f"{self.name} must not be called"
        )


def test_observe_reads_no_secret_and_builds_no_runtime():
    context = build_scheduler_execution_context(
        mode="observe",
        database=object(),
        scheduler_repository=object(),
        db_path="/tmp/platform.db",
        secret_path="/tmp/secret",
        secret_reader=ForbiddenCall(
            "secret_reader"
        ),
        runtime_builder=ForbiddenCall(
            "runtime_builder"
        ),
    )

    assert context.mode == SchedulerMode.OBSERVE
    assert context.execution_enabled is False
    assert context.provider is None
    assert context.dispatcher is None


def test_observe_mode_is_normalized():
    context = build_scheduler_execution_context(
        mode="  OBSERVE  ",
        database=object(),
        scheduler_repository=object(),
        db_path="/tmp/platform.db",
        secret_path="/tmp/secret",
        secret_reader=ForbiddenCall(
            "secret_reader"
        ),
        runtime_builder=ForbiddenCall(
            "runtime_builder"
        ),
    )

    assert context.mode == SchedulerMode.OBSERVE
    assert context.execution_enabled is False


def test_unknown_mode_fails_before_secret_read():
    with pytest.raises(ValueError):
        build_scheduler_execution_context(
            mode="live_money",
            database=object(),
            scheduler_repository=object(),
            db_path="/tmp/platform.db",
            secret_path="/tmp/secret",
            secret_reader=ForbiddenCall(
                "secret_reader"
            ),
            runtime_builder=ForbiddenCall(
                "runtime_builder"
            ),
        )


def test_paper_refresh_reads_secret_once_and_builds_runtime_once():
    database = object()
    repository = object()
    provider = object()
    execution_adapter = object()

    calls = {
        "secret": [],
        "runtime": [],
    }

    def secret_reader(path):
        calls["secret"].append(path)
        return "private-test-token"

    def runtime_builder(**kwargs):
        calls["runtime"].append(kwargs)

        return SimpleNamespace(
            provider=provider,
            execution_adapter=(
                execution_adapter
            ),
        )

    context = build_scheduler_execution_context(
        mode="paper_refresh",
        database=database,
        scheduler_repository=repository,
        db_path="/tmp/egx/platform.db",
        secret_path="/run/secrets/test_token",
        secret_reader=secret_reader,
        runtime_builder=runtime_builder,
    )

    assert (
        context.mode
        == SchedulerMode.PAPER_REFRESH
    )
    assert context.execution_enabled is True
    assert context.provider is provider

    assert isinstance(
        context.dispatcher,
        DailyRefreshDispatcher,
    )

    assert calls["secret"] == [
        "/run/secrets/test_token"
    ]

    assert len(calls["runtime"]) == 1

    kwargs = calls["runtime"][0]

    assert kwargs["database"] is database
    assert (
        kwargs["scheduler_repository"]
        is repository
    )
    assert kwargs["api_token"] == (
        "private-test-token"
    )
    assert kwargs["data_root"] == Path(
        "/tmp/egx"
    )


def test_token_is_not_retained_on_context():
    def secret_reader(path):
        del path
        return "private-test-token"

    runtime = SimpleNamespace(
        provider=object(),
        execution_adapter=object(),
    )

    context = build_scheduler_execution_context(
        mode=SchedulerMode.PAPER_REFRESH,
        database=object(),
        scheduler_repository=object(),
        db_path="/tmp/platform.db",
        secret_path="/tmp/secret",
        secret_reader=secret_reader,
        runtime_builder=lambda **kwargs: runtime,
    )

    assert not hasattr(
        context,
        "api_token",
    )

    assert (
        "private-test-token"
        not in repr(context)
    )
