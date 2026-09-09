import json
from datetime import (
    date,
    datetime,
    timedelta,
)
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.daily_refresh_runtime import (
    build_daily_refresh_runtime,
)
from app.core.orchestrator import (
    MarketSessionOrchestrator,
)
from app.core.schedule import (
    CalendarTruth,
    CheckpointName,
)
from app.data.daily_refresh_job import (
    DailyRefreshTarget,
)
from app.data.provider import ProviderResponse
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)


MARKET_DATE = date(2026, 9, 10)
CAIRO = ZoneInfo("Africa/Cairo")


class FakeEODHDProvider:
    name = "eodhd"

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

        payload = json.dumps(
            [
                {
                    "date": "2026-09-09",
                    "open": 75.0,
                    "high": 77.0,
                    "low": 74.5,
                    "close": 76.5,
                    "adjusted_close": 76.5,
                    "volume": 1000000,
                },
                {
                    "date": "2026-09-10",
                    "open": 76.5,
                    "high": 78.0,
                    "low": 76.0,
                    "close": 77.5,
                    "adjusted_close": 77.5,
                    "volume": 1200000,
                },
            ],
            separators=(",", ":"),
        ).encode("utf-8")

        return ProviderResponse(
            payload=payload,
            filename=(
                f"{symbol}-"
                f"{start_date.isoformat()}-"
                f"{end_date.isoformat()}-"
                "D1.json"
            ),
            source_uri=(
                "https://example.test/api/eod/"
                + symbol
            ),
            record_count=2,
            metadata={
                "endpoint": "eod",
                "symbol": symbol,
            },
        )


def seed_comi(database):
    instrument_id = str(uuid4())

    with database.connect() as con:
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
                instrument_id,
                "EQUITY",
                "COMI",
                "egid",
                "COMI",
                "a" * 64,
                "[]",
                "2026-09-10T00:00:00+00:00",
            ),
        )

        con.execute(
            """
            INSERT INTO instrument_aliases (
                instrument_id,
                provider,
                alias_type,
                alias_value,
                normalized_value,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                "canonical",
                "CANONICAL_TICKER",
                "COMI",
                "COMI",
                "2026-09-10T00:00:00+00:00",
            ),
        )

    return instrument_id


def test_runtime_executes_real_pipeline_offline(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    instrument_id = seed_comi(
        database
    )

    scheduler_repository = (
        SchedulerRepository(
            database
        )
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    evaluation = orchestrator.evaluate(
        now=datetime(
            2026,
            9,
            10,
            16,
            15,
            tzinfo=CAIRO,
        ),
        market_date=MARKET_DATE,
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=set(),
    )

    scheduler_repository.sync_evaluation(
        evaluation
    )

    provider = FakeEODHDProvider()

    data_root = tmp_path / "data"

    runtime = build_daily_refresh_runtime(
        database=database,
        scheduler_repository=(
            scheduler_repository
        ),
        data_root=data_root,
        provider=provider,
        targets=(
            DailyRefreshTarget(
                "COMI",
                "COMI.EGX",
            ),
        ),
        lookback_days=400,
    )

    result = (
        runtime.execution_adapter.execute(
            market_date=MARKET_DATE,
            checkpoint_name=(
                CheckpointName
                .AFTER_SESSION_PRIMARY
            ),
            provider=runtime.provider,
        )
    )

    assert result.claimed is True
    assert result.succeeded is True
    assert result.item_count == 1

    assert provider.calls == [
        (
            "COMI.EGX",
            MARKET_DATE
            - timedelta(days=400),
            MARKET_DATE,
        )
    ]

    with database.connect() as con:
        ingestion = con.execute(
            """
            SELECT *
            FROM data_ingestions
            """
        ).fetchone()

        artifact = con.execute(
            """
            SELECT *
            FROM daily_canonical_artifacts
            """
        ).fetchone()

        source_count = con.execute(
            """
            SELECT COUNT(*)
            FROM daily_canonical_sources
            """
        ).fetchone()[0]

        job = con.execute(
            """
            SELECT
                status,
                attempt_count
            FROM scheduled_jobs
            WHERE market_date = ?
              AND checkpoint_name = ?
            """,
            (
                MARKET_DATE.isoformat(),
                CheckpointName
                .AFTER_SESSION_PRIMARY
                .value,
            ),
        ).fetchone()

        integrity = con.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

    assert ingestion["status"] == "VALIDATED"
    assert ingestion["symbol"] == "COMI"

    assert (
        artifact["instrument_id"]
        == instrument_id
    )
    assert artifact["status"] == "VALIDATED"
    assert artifact["record_count"] == 2
    assert artifact["valid_bar_count"] == 2

    assert source_count == 1

    assert job["status"] == "SUCCEEDED"
    assert job["attempt_count"] == 1

    assert integrity == "ok"

    raw_path = (
        data_root
        / "raw"
        / ingestion["raw_path"]
    )

    canonical_path = (
        data_root
        / "canonical"
        / artifact["canonical_path"]
    )

    assert raw_path.is_file()
    assert canonical_path.is_file()
