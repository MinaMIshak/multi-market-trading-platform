from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from uuid import uuid4

from app.core.calendar_backfill import (
    build_calendar_backfill_runtime,
)
from app.data.index_canonical_store import (
    SEMANTIC_CONTRACT_VERSION,
    SERIALIZATION_FORMAT,
)
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.repository import TradingRepository


FRIDAY = date(2026, 9, 11)
SATURDAY = date(2026, 9, 12)
THURSDAY = date(2026, 9, 10)

VERIFIED_AT = datetime(
    2026, 9, 10, 14, 45, tzinfo=timezone.utc,
)


def insert_index_artifact(
    database,
    symbol,
    *,
    snapshot,
    newest,
):
    artifact_id = str(uuid4())
    digest = hashlib.sha256(symbol.encode()).hexdigest()

    with database.connect() as con:
        con.execute(
            """
            INSERT INTO canonical_data_artifacts (
                artifact_id,
                provider,
                asset_type,
                granularity,
                symbol,
                source_snapshot_date,
                canonical_path,
                sha256,
                byte_size,
                record_count,
                oldest_market_date,
                newest_market_date,
                semantic_contract_version,
                serialization_format,
                full_ohlc_valid_count,
                legacy_close_reference_count,
                quarantined_anomaly_count,
                full_ohlc_usable_count,
                close_history_usable_count,
                status,
                created_at,
                validated_at,
                metadata_json
            )
            VALUES (
                ?, 'egx_official_public', 'INDEX_BARS', 'D1', ?, ?, ?, ?,
                1, 1, ?, ?, ?, ?, 1, 0, 0, 1, 1, 'VALIDATED', ?, ?, '{}'
            )
            """,
            (
                artifact_id,
                symbol,
                snapshot.isoformat(),
                f"canonical/index/{symbol}/test.json",
                digest,
                newest.isoformat(),
                newest.isoformat(),
                SEMANTIC_CONTRACT_VERSION,
                SERIALIZATION_FORMAT,
                VERIFIED_AT.isoformat(),
                VERIFIED_AT.isoformat(),
            ),
        )


def build(tmp_path):
    database = Database(tmp_path / "platform.db")
    database.initialize()

    return database, build_calendar_backfill_runtime(
        database=database
    )


def test_run_date_persists_deterministic_weekend(tmp_path):
    database, runtime = build(tmp_path)

    result = runtime.run_date(FRIDAY)

    assert result.market_date == FRIDAY
    assert result.base_status == MarketSessionStatus.WEEKEND
    assert result.holiday_status == MarketSessionStatus.UNKNOWN
    assert result.verification_status == MarketSessionStatus.UNKNOWN

    session = TradingRepository(database).get_market_session(FRIDAY)
    assert session is not None
    assert session.status == MarketSessionStatus.WEEKEND


def test_run_date_leaves_unverified_weekday_unrecorded(tmp_path):
    database, runtime = build(tmp_path)

    result = runtime.run_date(THURSDAY)

    assert result.base_status == MarketSessionStatus.UNKNOWN
    assert result.holiday_status == MarketSessionStatus.UNKNOWN
    assert result.verification_status == MarketSessionStatus.UNKNOWN

    assert (
        TradingRepository(database).get_market_session(THURSDAY)
        is None
    )


def test_run_date_promotes_verified_from_admitted_index_evidence(
    tmp_path,
):
    database, runtime = build(tmp_path)

    for symbol in ("CASE30", "EGX70_EWI", "EGX100_EWI"):
        insert_index_artifact(
            database,
            symbol,
            snapshot=THURSDAY,
            newest=THURSDAY,
        )

    result = runtime.run_date(THURSDAY)

    assert result.verification_status == MarketSessionStatus.VERIFIED

    session = TradingRepository(database).get_market_session(THURSDAY)
    assert session is not None
    assert session.status == MarketSessionStatus.VERIFIED


def test_run_date_does_not_fabricate_verified_from_stale_snapshot(
    tmp_path,
):
    """A later snapshot whose newest bar has not advanced must not
    verify the snapshot's own date as a traded session."""
    database, runtime = build(tmp_path)

    for symbol in ("CASE30", "EGX70_EWI", "EGX100_EWI"):
        insert_index_artifact(
            database,
            symbol,
            snapshot=SATURDAY,
            newest=THURSDAY,
        )

    result = runtime.run_date(SATURDAY)

    # SATURDAY is deterministic WEEKEND, but the index snapshot must
    # never independently mark it VERIFIED (a traded session).
    assert result.base_status == MarketSessionStatus.WEEKEND
    assert result.verification_status == MarketSessionStatus.UNKNOWN

    session = TradingRepository(database).get_market_session(SATURDAY)
    assert session is not None
    assert session.status == MarketSessionStatus.WEEKEND


def test_run_range_covers_every_date_inclusive(tmp_path):
    database, runtime = build(tmp_path)

    results = runtime.run_range(FRIDAY, SATURDAY)

    assert [r.market_date for r in results] == [FRIDAY, SATURDAY]
    assert all(
        r.base_status == MarketSessionStatus.WEEKEND
        for r in results
    )


def test_run_range_rejects_inverted_range(tmp_path):
    _, runtime = build(tmp_path)

    try:
        runtime.run_range(SATURDAY, FRIDAY)
    except ValueError as exc:
        assert "end_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_run_range_is_idempotent(tmp_path):
    database, runtime = build(tmp_path)

    first = runtime.run_range(FRIDAY, SATURDAY)
    second = runtime.run_range(FRIDAY, SATURDAY)

    assert [r.base_status for r in first] == [
        r.base_status for r in second
    ]

    trading_repository = TradingRepository(database)
    assert (
        trading_repository.get_market_session(FRIDAY).status
        == MarketSessionStatus.WEEKEND
    )
