from __future__ import annotations

import json

from datetime import date

import pytest

from app.data.provider import (
    ProviderResponse,
)
from app.data.security_master_refresh_job import (
    EGID_PROVIDER,
    SecurityMasterRefreshJobError,
)
from app.data.security_master_refresh_runtime import (
    build_security_master_refresh_runtime,
)
from app.storage import Database
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


DAY = date(2026, 9, 29)


def egid_record(
    *,
    symbol_code="EGS73541C012",
    reuters="CCAP.CA",
):
    return {
        "SYMBOL_CODE": symbol_code,
        "ARB_NAME": "القلعة للاستثمارات المالية",
        "ENG_NAME": "QALAA For Financial Investments",
        "Reuters": reuters,
        "eng_shortname": "Qalaa Holdings",
        "arb_shortname": "القلعة القابضة",
    }


class FakeEGIDProvider:
    name = EGID_PROVIDER

    def __init__(self, records) -> None:
        self.records = records
        self.calls = 0

    def fetch_security_master(self):
        self.calls += 1

        payload = json.dumps(
            self.records,
            ensure_ascii=False,
        ).encode("utf-8")

        return ProviderResponse(
            payload=payload,
            filename="market-watch-names.json",
            source_uri=(
                "https://ticker.egidegypt.com/"
                "api/DelayedFeed/"
                "getAllMarketWatchNames"
            ),
            record_count=len(self.records),
            metadata={"access": "anonymous"},
        )


class WrongProvider(FakeEGIDProvider):
    name = "not_egid"


def build(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    runtime = (
        build_security_master_refresh_runtime(
            database=database,
            root=tmp_path / "data",
        )
    )

    return database, runtime


def test_refresh_admits_canonical_instruments(
    tmp_path,
):
    database, runtime = build(tmp_path)

    provider = FakeEGIDProvider(
        [egid_record()]
    )

    result = runtime.job.run(
        provider=provider,
        snapshot_date=DAY,
    )

    assert result.provider == EGID_PROVIDER
    assert result.record_count == 1
    assert result.instrument_count == 1
    assert len(result.artifact_sha256) == 64

    repository = SecurityMasterRepository(
        database
    )

    resolved = repository.resolve(
        "CCAP", provider="canonical"
    )

    assert resolved["canonical_ticker"] == "CCAP"
    assert (
        resolved["source_market_date"]
        == DAY.isoformat()
    )
    assert (
        resolved["source_sha256"]
        == result.artifact_sha256
    )


def test_refresh_replay_is_idempotent(tmp_path):
    database, runtime = build(tmp_path)

    provider = FakeEGIDProvider(
        [egid_record()]
    )

    first = runtime.job.run(
        provider=provider,
        snapshot_date=DAY,
    )

    second = runtime.job.run(
        provider=provider,
        snapshot_date=DAY,
    )

    assert (
        first.artifact_sha256
        == second.artifact_sha256
    )

    with database.connect() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM canonical_instruments
            WHERE source_provider = ?
            """,
            (EGID_PROVIDER,),
        ).fetchone()[0]

    assert count == 1


def test_wrong_provider_is_rejected_before_fetch(
    tmp_path,
):
    _database, runtime = build(tmp_path)

    provider = WrongProvider([egid_record()])

    with pytest.raises(
        ValueError,
        match="requires provider",
    ):
        runtime.job.run(
            provider=provider,
            snapshot_date=DAY,
        )

    assert provider.calls == 0


def test_duplicate_symbol_code_fails_closed(
    tmp_path,
):
    _database, runtime = build(tmp_path)

    provider = FakeEGIDProvider(
        [
            egid_record(
                symbol_code="EGS1", reuters="AAA.CA"
            ),
            egid_record(
                symbol_code="EGS1", reuters="BBB.CA"
            ),
        ]
    )

    with pytest.raises(
        SecurityMasterRefreshJobError,
    ):
        runtime.job.run(
            provider=provider,
            snapshot_date=DAY,
        )
