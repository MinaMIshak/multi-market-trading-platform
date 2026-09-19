from __future__ import annotations

import json

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

import pytest

from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.calendar_verification import (
    CalendarVerificationPolicy,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.core.schedule import (
    CalendarTruth,
)
from app.data.official_index_refresh_job import (
    OFFICIAL_PROVIDER,
    REQUIRED_OFFICIAL_INDICES,
)
from app.data.official_index_refresh_runtime import (
    build_official_index_refresh_runtime,
)
from app.data.provider import (
    ProviderBatchResponse,
    ProviderResponse,
)
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage import (
    Database,
    TradingRepository,
)


DAY = date(
    2026,
    9,
    20,
)


def index_row(
    market_date: date,
):
    return {
        "change": 5,
        "changePer": 5,
        "high": 110,
        "indexClose": 105,
        "indexDay": (
            market_date.isoformat()
            + "T00:00:00"
        ),
        "indexOpen": 100,
        "low": 90,
    }


class FakeOfficialProvider:
    name = OFFICIAL_PROVIDER

    def __init__(
        self,
        *,
        market_date: date,
    ) -> None:
        self.market_date = market_date
        self.calls = []

    def fetch_index_bars(
        self,
        *,
        index_name,
        start_date,
        end_date,
        page_size=1000,
    ):
        self.calls.append(
            (
                index_name,
                start_date,
                end_date,
                page_size,
            )
        )

        document = {
            "success": True,
            "data": [
                index_row(
                    self.market_date
                )
            ],
            "page": 1,
            "pageSize": page_size,
            "totalCount": 1,
            "totalPages": 1,
            "hasNextPage": False,
            "hasPreviousPage": False,
        }

        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        response = ProviderResponse(
            payload=payload,
            filename=(
                f"{index_name}-"
                f"{start_date.isoformat()}-"
                f"{end_date.isoformat()}-"
                "page-0001.json"
            ),
            source_uri=(
                "https://example.test/"
                "official-index"
            ),
            record_count=1,
            metadata={
                "page_number": 1,
            },
        )

        return ProviderBatchResponse(
            responses=(response,),
            record_count=1,
            metadata={
                "provider": self.name,
                "index_name": index_name,
                "calculated_page_count": 1,
            },
        )


class WrongProvider(
    FakeOfficialProvider
):
    name = "not_official"


def build(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    runtime = (
        build_official_index_refresh_runtime(
            database=database,
            root=tmp_path / "data",
        )
    )

    return database, runtime


def verification_service(
    database,
):
    trading = TradingRepository(
        database
    )

    service = (
        CalendarVerificationService(
            evidence_repository=(
                OfficialIndexEvidenceRepository(
                    database
                )
            ),
            trading_repository=trading,
            policy=(
                CalendarVerificationPolicy()
            ),
        )
    )

    resolver = CalendarTruthResolver(
        trading
    )

    return service, resolver


def test_refresh_promotes_three_required_indices(
    tmp_path,
):
    database, runtime = build(
        tmp_path
    )

    provider = FakeOfficialProvider(
        market_date=DAY
    )

    result = runtime.job.run(
        provider=provider,
        start_date=DAY,
        end_date=DAY,
        snapshot_date=DAY,
    )

    assert tuple(
        item.index_name
        for item in result.items
    ) == REQUIRED_OFFICIAL_INDICES

    assert len(provider.calls) == 3

    with database.connect() as connection:
        artifact_rows = connection.execute(
            """
            SELECT
                symbol,
                status,
                newest_market_date
            FROM canonical_data_artifacts
            WHERE provider = ?
              AND source_snapshot_date = ?
            ORDER BY symbol
            """,
            (
                OFFICIAL_PROVIDER,
                DAY.isoformat(),
            ),
        ).fetchall()

        ingestion_rows = connection.execute(
            """
            SELECT status
            FROM data_ingestions
            WHERE provider = ?
              AND market_date = ?
            """,
            (
                OFFICIAL_PROVIDER,
                DAY.isoformat(),
            ),
        ).fetchall()

    assert len(artifact_rows) == 3

    assert {
        row["symbol"]
        for row in artifact_rows
    } == set(
        REQUIRED_OFFICIAL_INDICES
    )

    assert all(
        row["status"] == "VALIDATED"
        for row in artifact_rows
    )

    assert all(
        row["newest_market_date"]
        == DAY.isoformat()
        for row in artifact_rows
    )

    assert len(ingestion_rows) == 3

    assert all(
        row["status"] == "VALIDATED"
        for row in ingestion_rows
    )


def test_refresh_to_calendar_verified_truth(
    tmp_path,
):
    database, runtime = build(
        tmp_path
    )

    runtime.job.run(
        provider=(
            FakeOfficialProvider(
                market_date=DAY
            )
        ),
        start_date=DAY,
        end_date=DAY,
        snapshot_date=DAY,
    )

    service, resolver = (
        verification_service(
            database
        )
    )

    decision = service.verify(
        DAY,
        verified_at=datetime(
            2026,
            9,
            20,
            7,
            15,
            tzinfo=timezone.utc,
        ),
    )

    assert (
        decision.status
        == MarketSessionStatus.VERIFIED
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth
        .VERIFIED_TRADING_DAY
    )


def test_stale_official_rows_fail_closed(
    tmp_path,
):
    database, runtime = build(
        tmp_path
    )

    runtime.job.run(
        provider=(
            FakeOfficialProvider(
                market_date=(
                    DAY
                    - timedelta(days=1)
                )
            )
        ),
        start_date=(
            DAY
            - timedelta(days=1)
        ),
        end_date=DAY,
        snapshot_date=DAY,
    )

    service, resolver = (
        verification_service(
            database
        )
    )

    decision = service.verify(
        DAY,
        verified_at=datetime(
            2026,
            9,
            20,
            7,
            15,
            tzinfo=timezone.utc,
        ),
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth.UNVERIFIED
    )


def test_exact_refresh_replay_is_idempotent(
    tmp_path,
):
    database, runtime = build(
        tmp_path
    )

    provider = FakeOfficialProvider(
        market_date=DAY
    )

    first = runtime.job.run(
        provider=provider,
        start_date=DAY,
        end_date=DAY,
        snapshot_date=DAY,
    )

    second = runtime.job.run(
        provider=provider,
        start_date=DAY,
        end_date=DAY,
        snapshot_date=DAY,
    )

    assert [
        item.artifact_id
        for item in first.items
    ] == [
        item.artifact_id
        for item in second.items
    ]

    with database.connect() as connection:
        artifacts = connection.execute(
            """
            SELECT COUNT(*)
            FROM canonical_data_artifacts
            WHERE provider = ?
              AND source_snapshot_date = ?
            """,
            (
                OFFICIAL_PROVIDER,
                DAY.isoformat(),
            ),
        ).fetchone()[0]

        ingestions = connection.execute(
            """
            SELECT COUNT(*)
            FROM data_ingestions
            WHERE provider = ?
              AND market_date = ?
            """,
            (
                OFFICIAL_PROVIDER,
                DAY.isoformat(),
            ),
        ).fetchone()[0]

    assert artifacts == 3
    assert ingestions == 3


def test_wrong_provider_is_rejected_before_fetch(
    tmp_path,
):
    _database, runtime = build(
        tmp_path
    )

    provider = WrongProvider(
        market_date=DAY
    )

    with pytest.raises(
        ValueError,
        match="requires egx_official_public",
    ):
        runtime.job.run(
            provider=provider,
            start_date=DAY,
            end_date=DAY,
            snapshot_date=DAY,
        )

    assert provider.calls == []
