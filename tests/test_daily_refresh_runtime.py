from datetime import date

import pytest

from app.core.daily_refresh_runtime import (
    DEFAULT_EODHD_TARGETS,
    DailyRefreshRuntime,
    build_daily_refresh_runtime,
)
from app.data.provider import ProviderResponse
from app.data.providers.eodhd import (
    EODHDProvider,
)
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    def fetch_daily_bars(
        self,
        *,
        symbol,
        start_date,
        end_date,
    ):
        self.calls.append(
            (
                symbol,
                start_date,
                end_date,
            )
        )

        return ProviderResponse(
            payload=b"[]",
            filename="fake.json",
            source_uri=(
                "https://example.test/daily"
            ),
            record_count=0,
            metadata={},
        )


def make_database(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    return database


def test_default_target_contract():
    assert [
        (
            item.canonical_symbol,
            item.provider_symbol,
        )
        for item in DEFAULT_EODHD_TARGETS
    ] == [
        ("COMI", "COMI.EGX"),
        ("EAST", "EAST.EGX"),
        ("FWRY", "FWRY.EGX"),
        ("ORAS", "ORAS.EGX"),
        ("SWDY", "SWDY.EGX"),
    ]


def test_injected_provider_requires_no_token(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    scheduler_repository = (
        SchedulerRepository(
            database
        )
    )

    provider = FakeProvider()

    runtime = build_daily_refresh_runtime(
        database=database,
        scheduler_repository=(
            scheduler_repository
        ),
        data_root=tmp_path / "data",
        provider=provider,
        api_token=None,
    )

    assert isinstance(
        runtime,
        DailyRefreshRuntime,
    )

    assert runtime.provider is provider

    assert runtime.refresh_job.targets == (
        DEFAULT_EODHD_TARGETS
    )

    assert (
        runtime
        .execution_adapter
        .refresh_job
        is runtime.refresh_job
    )

    assert provider.calls == []


def test_production_provider_requires_token(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    scheduler_repository = (
        SchedulerRepository(
            database
        )
    )

    with pytest.raises(
        ValueError,
        match="runtime token",
    ):
        build_daily_refresh_runtime(
            database=database,
            scheduler_repository=(
                scheduler_repository
            ),
            data_root=tmp_path / "data",
            api_token="   ",
        )


def test_production_build_has_no_network_side_effect(
    tmp_path,
    monkeypatch,
):
    database = make_database(
        tmp_path
    )

    scheduler_repository = (
        SchedulerRepository(
            database
        )
    )

    network_calls = []

    def forbidden_read(
        self,
        req,
    ):
        del self
        del req

        network_calls.append(True)

        raise AssertionError(
            "network must not run "
            "during runtime build"
        )

    monkeypatch.setattr(
        EODHDProvider,
        "_read",
        forbidden_read,
    )

    secret = (
        "TEST_RUNTIME_SECRET_"
        "DO_NOT_PERSIST"
    )

    runtime = build_daily_refresh_runtime(
        database=database,
        scheduler_repository=(
            scheduler_repository
        ),
        data_root=tmp_path / "data",
        api_token=secret,
    )

    assert isinstance(
        runtime.provider,
        EODHDProvider,
    )

    assert network_calls == []

    # The runtime bundle itself must not
    # duplicate the credential.
    assert "api_token" not in (
        runtime.__dict__
    )

    assert secret not in repr(runtime)

    # Provider needs the credential only
    # for future request execution.
    assert (
        runtime.provider.api_token
        == secret
    )

    assert runtime.provider.fetch_daily_bars


def test_runtime_wires_admission_policy(
    tmp_path,
):
    database = make_database(tmp_path)

    repository = SchedulerRepository(
        database
    )

    runtime = build_daily_refresh_runtime(
        database=database,
        scheduler_repository=repository,
        data_root=tmp_path / "data",
        provider=FakeProvider(),
        minimum_valid_bars=275,
    )

    assert (
        runtime
        .admission_policy
        .minimum_valid_bars
        == 275
    )

    assert (
        runtime
        .refresh_job
        .ingestor
        .admission_policy
        is runtime.admission_policy
    )
