from datetime import date
from uuid import UUID

import pytest

from app.data.daily_canonical import (
    DailyBarSemanticClass,
)
from app.data.daily_canonical_pipeline import (
    DailyCanonicalPipeline,
)
from app.data.daily_ingestion import (
    DailyBarIngestionResult,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
)
from app.data.raw_store import ImmutableRawStore


INSTRUMENT_ID = UUID(
    "4c1f3369-f71c-5856-aa52-953040a2cbc0"
)


def make_ingestion(tmp_path):
    store = ImmutableRawStore(
        tmp_path / "raw"
    )

    payload = (
        b'[{"date":"2026-09-07",'
        b'"open":140,"high":141,"low":138,'
        b'"close":139,"adjusted_close":139,'
        b'"volume":100},'
        b'{"date":"2026-09-08",'
        b'"open":150,"high":141,"low":138,'
        b'"close":140,"adjusted_close":140,'
        b'"volume":200}]'
    )

    manifest = store.store_bytes(
        provider="eodhd",
        asset_type=DataAssetType.DAILY_BARS,
        payload=payload,
        filename="COMI.EGX-D1.json",
        market_date=date(2026, 9, 9),
        symbol="COMI",
        granularity=BarGranularity.D1,
        record_count=2,
    )

    ingestion = DailyBarIngestionResult(
        provider="eodhd",
        canonical_symbol="COMI",
        provider_symbol="COMI.EGX",
        instrument_id=str(INSTRUMENT_ID),
        snapshot_date=date(2026, 9, 9),
        requested_start_date=date(2026, 9, 7),
        requested_end_date=date(2026, 9, 8),
        manifest=manifest,
        record_count=2,
        response_metadata={},
    )

    return store, ingestion


def test_pipeline_preserves_provenance_and_quarantines(
    tmp_path,
):
    store, ingestion = make_ingestion(
        tmp_path
    )

    result = DailyCanonicalPipeline(
        raw_store=store
    ).canonicalize_ingestion(
        ingestion
    )

    first, second = result.rows

    assert first.semantic_class == (
        DailyBarSemanticClass.VALID_EXECUTABLE
    )

    assert second.semantic_class == (
        DailyBarSemanticClass.QUARANTINED_ANOMALY
    )

    assert second.open is None
    assert second.close is None

    assert first.source_row_number == 1
    assert second.source_row_number == 2

    assert (
        first.source_sha256
        == ingestion.manifest.sha256
        == second.source_sha256
    )

    assert first.instrument_id == INSTRUMENT_ID
    assert first.provider_symbol == "COMI.EGX"


def test_pipeline_rejects_tampered_raw_bytes(
    tmp_path,
):
    store, ingestion = make_ingestion(
        tmp_path
    )

    target = (
        store.root
        / ingestion.manifest.raw_path
    )

    payload = target.read_bytes()
    target.write_bytes(
        b"X" + payload[1:]
    )

    with pytest.raises(
        RuntimeError,
        match="sha256 mismatch",
    ):
        DailyCanonicalPipeline(
            raw_store=store
        ).canonicalize_ingestion(
            ingestion
        )


def test_finalize_promotes_and_replays_atomically(
    tmp_path,
):
    from app.data.daily_canonical_store import (
        DailyCanonicalStore,
    )
    from app.data.ingestion_repository import (
        DataIngestionRepository,
    )
    from app.data.models import IngestionStatus
    from app.storage import Database
    from app.storage.daily_canonical_artifact_repository import (
        DailyCanonicalArtifactRepository,
    )

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
                str(INSTRUMENT_ID),
                "EQUITY",
                "COMI",
                "egid",
                "COMI",
                "a" * 64,
                "[]",
                "2026-09-09T00:00:00+00:00",
            ),
        )

    raw_store, ingestion = make_ingestion(
        tmp_path
    )

    ingestion_repo = DataIngestionRepository(
        db
    )

    persisted = ingestion_repo.save_manifest(
        ingestion.manifest,
        status=IngestionStatus.RECEIVED,
        metadata={
            "canonical_symbol": "COMI",
            "provider_symbol": "COMI.EGX",
            "instrument_id": str(
                INSTRUMENT_ID
            ),
            "snapshot_date": (
                date(2026, 9, 9).isoformat()
            ),
        },
    )

    ingestion = DailyBarIngestionResult(
        provider=ingestion.provider,
        canonical_symbol=(
            ingestion.canonical_symbol
        ),
        provider_symbol=(
            ingestion.provider_symbol
        ),
        instrument_id=(
            ingestion.instrument_id
        ),
        snapshot_date=(
            ingestion.snapshot_date
        ),
        requested_start_date=(
            ingestion.requested_start_date
        ),
        requested_end_date=(
            ingestion.requested_end_date
        ),
        manifest=persisted,
        record_count=(
            ingestion.record_count
        ),
        response_metadata=(
            ingestion.response_metadata
        ),
    )

    pipeline = DailyCanonicalPipeline(
        raw_store=raw_store
    )

    canonical_store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    artifact_repo = (
        DailyCanonicalArtifactRepository(
            db
        )
    )

    first = pipeline.finalize_ingestion(
        ingestion,
        canonical_store=canonical_store,
        repository=artifact_repo,
    )

    second = pipeline.finalize_ingestion(
        ingestion,
        canonical_store=canonical_store,
        repository=artifact_repo,
    )

    assert first.artifact_id == second.artifact_id

    assert (
        first.canonical_manifest.sha256
        == second.canonical_manifest.sha256
    )

    with db.connect() as con:
        artifact_count = con.execute(
            """
            SELECT COUNT(*)
            FROM daily_canonical_artifacts
            """
        ).fetchone()[0]

        link_count = con.execute(
            """
            SELECT COUNT(*)
            FROM daily_canonical_sources
            """
        ).fetchone()[0]

        source_status = con.execute(
            """
            SELECT status
            FROM data_ingestions
            WHERE ingestion_id=?
            """,
            (
                str(
                    persisted.ingestion_id
                ),
            ),
        ).fetchone()["status"]

        artifact_status = con.execute(
            """
            SELECT status
            FROM daily_canonical_artifacts
            WHERE artifact_id=?
            """,
            (
                first.artifact_id,
            ),
        ).fetchone()["status"]

    assert artifact_count == 1
    assert link_count == 1
    assert source_status == "VALIDATED"
    assert artifact_status == "VALIDATED"
