import json
from datetime import date

from app.data.daily_canonical_pipeline import (
    DailyCanonicalPipeline,
)
from app.data.daily_canonical_store import (
    DailyCanonicalStore,
)
from app.data.daily_ingestion import (
    DailyBarIngestor,
)
from app.data.daily_refresh_job import (
    DailyRefreshJob,
    DailyRefreshTarget,
)
from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.provider import ProviderResponse
from app.data.raw_store import ImmutableRawStore
from app.storage import Database
from app.storage.daily_canonical_artifact_repository import (
    DailyCanonicalArtifactRepository,
)


INSTRUMENT_ID = (
    "11111111-1111-1111-1111-111111111111"
)


class Resolver:
    def resolve(
        self,
        value,
        *,
        provider=None,
    ):
        assert (value, provider) in (("COMI", "canonical"), ("COMI.EGX", "eodhd"))

        return {
            "instrument_id": INSTRUMENT_ID,
            "canonical_ticker": "COMI",
            "matched_provider": provider,
            "matched_alias_value": value,
        }


class Provider:
    name = "eodhd"

    def __init__(self):
        self.calls = 0

    def fetch_daily_bars(
        self,
        *,
        symbol,
        start_date,
        end_date,
    ):
        self.calls += 1

        rows = [
            {
                "date": "2026-09-08",
                "open": 100.0,
                "high": 103.0,
                "low": 99.0,
                "close": 102.0,
                "adjusted_close": 102.0,
                "volume": 1000,
            },
            {
                "date": "2026-09-09",
                "open": 102.0,
                "high": 105.0,
                "low": 101.0,
                "close": 104.0,
                "adjusted_close": 104.0,
                "volume": 1200,
            },
        ]

        payload = json.dumps(
            rows,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        return ProviderResponse(
            payload=payload,
            filename=(
                f"{symbol}-"
                f"{start_date.isoformat()}-"
                f"{end_date.isoformat()}-D1.json"
            ),
            source_uri=(
                "https://example.test/api/eod/"
                + symbol
            ),
            record_count=len(rows),
            metadata={"endpoint": "eod"},
        )


def test_real_pipeline_exact_replay(
    tmp_path,
):
    db = Database(
        tmp_path / "platform.db"
    )
    db.initialize()

    with db.connect() as con:
        con.execute(
            """
            INSERT INTO canonical_instruments (
                instrument_id,
                instrument_type,
                canonical_ticker,
                source_provider,
                source_symbol_code,
                source_sha256,
                normalization_notes_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                INSTRUMENT_ID,
                "EQUITY",
                "COMI",
                "egid",
                "COMI",
                "a" * 64,
                "[]",
                "2026-09-09T00:00:00+00:00",
            ),
        )

    raw_store = ImmutableRawStore(
        tmp_path / "raw"
    )

    ingestor = DailyBarIngestor(
        raw_store=raw_store,
        repository=DataIngestionRepository(
            db
        ),
        resolver=Resolver(),
    )

    job = DailyRefreshJob(
        ingestor=ingestor,
        pipeline=DailyCanonicalPipeline(
            raw_store=raw_store
        ),
        canonical_store=DailyCanonicalStore(
            tmp_path / "canonical"
        ),
        artifact_repository=(
            DailyCanonicalArtifactRepository(
                db
            )
        ),
        targets=(
            DailyRefreshTarget(
                "COMI",
                "COMI.EGX",
            ),
        ),
    )

    provider = Provider()

    kwargs = {
        "provider": provider,
        "start_date": date(
            2026,
            9,
            8,
        ),
        "end_date": date(
            2026,
            9,
            9,
        ),
        "snapshot_date": date(
            2026,
            9,
            9,
        ),
    }

    first = job.run(**kwargs)
    second = job.run(**kwargs)

    first_item = first.items[0]
    second_item = second.items[0]

    assert (
        first_item.ingestion_id
        == second_item.ingestion_id
    )
    assert (
        first_item.artifact_id
        == second_item.artifact_id
    )

    assert first_item.record_count == 2
    assert first_item.valid_bar_count == 2
    assert (
        first_item.quarantined_bar_count
        == 0
    )

    assert provider.calls == 2

    with db.connect() as con:
        ingestions = con.execute(
            """
            SELECT
                raw_path,
                status
            FROM data_ingestions
            """
        ).fetchall()

        artifacts = con.execute(
            """
            SELECT
                canonical_path,
                status
            FROM daily_canonical_artifacts
            """
        ).fetchall()

        source_count = con.execute(
            """
            SELECT COUNT(*)
            FROM daily_canonical_sources
            """
        ).fetchone()[0]

        integrity = con.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

    assert len(ingestions) == 1
    assert len(artifacts) == 1
    assert source_count == 1

    assert (
        ingestions[0]["status"]
        == "VALIDATED"
    )
    assert (
        artifacts[0]["status"]
        == "VALIDATED"
    )
    assert integrity == "ok"

    raw_file = (
        tmp_path
        / "raw"
        / ingestions[0]["raw_path"]
    )
    canonical_file = (
        tmp_path
        / "canonical"
        / artifacts[0]["canonical_path"]
    )

    assert raw_file.is_file()
    assert canonical_file.is_file()

    assert (
        raw_file.stat().st_mode & 0o777
    ) == 0o660

    assert (
        canonical_file.stat().st_mode & 0o777
    ) == 0o660
