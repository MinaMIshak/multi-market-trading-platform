from datetime import (
    date,
    datetime,
    timezone,
)
from uuid import UUID, uuid4

from app.data.daily_canonical_store import (
    CanonicalDailyArtifactManifest,
)
from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
    IngestionStatus,
    RawArtifactManifest,
)
from app.storage import Database
from app.storage.daily_canonical_artifact_repository import (
    DailyCanonicalArtifactRepository,
)


INSTRUMENT_ID = UUID(
    "4c1f3369-f71c-5856-aa52-953040a2cbc0"
)
SNAPSHOT = date(2026, 9, 9)


def make_database(tmp_path):
    db = Database(tmp_path / "platform.db")
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

    return db


def seed_sources(db):
    repo = DataIngestionRepository(db)
    ids = []

    for index in (1, 2):
        ingestion_id = uuid4()
        ids.append(str(ingestion_id))

        manifest = RawArtifactManifest(
            ingestion_id=ingestion_id,
            provider="eodhd",
            asset_type=DataAssetType.DAILY_BARS,
            granularity=BarGranularity.D1,
            symbol="COMI",
            market_date=SNAPSHOT,
            raw_path=f"eodhd/daily/{index}.json",
            sha256=str(index) * 64,
            byte_size=100,
            record_count=1,
            received_at=datetime.now(timezone.utc),
        )

        repo.save_manifest(
            manifest,
            status=IngestionStatus.RECEIVED,
            metadata={
                "canonical_symbol": "COMI",
                "provider_symbol": "COMI.EGX",
                "instrument_id": str(INSTRUMENT_ID),
                "snapshot_date": SNAPSHOT.isoformat(),
            },
        )

    return ids


def artifact_manifest():
    return CanonicalDailyArtifactManifest(
        instrument_id=INSTRUMENT_ID,
        provider="eodhd",
        provider_symbol="COMI.EGX",
        canonical_symbol="COMI",
        asset_type=DataAssetType.DAILY_BARS,
        granularity=BarGranularity.D1,
        source_snapshot_date=SNAPSHOT,
        oldest_market_date=date(2026, 9, 7),
        newest_market_date=date(2026, 9, 8),
        relative_path=(
            "eodhd/daily_bars/2026/09/09/COMI/"
            "COMI-2026-09-07-2026-09-08-"
            "D1-canonical-v1.json"
        ),
        sha256="d" * 64,
        byte_size=500,
        record_count=2,
        valid_bar_count=2,
        quarantined_bar_count=0,
    )


def test_atomic_daily_promotion(tmp_path):
    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    repo = DailyCanonicalArtifactRepository(db)

    artifact_id = repo.promote_validated_artifact(
        manifest=artifact_manifest(),
        source_ingestion_ids=source_ids,
        metadata={"validation": "test"},
    )

    with db.connect() as con:
        artifact = con.execute(
            """
            SELECT *
            FROM daily_canonical_artifacts
            WHERE artifact_id=?
            """,
            (artifact_id,),
        ).fetchone()

        links = con.execute(
            """
            SELECT ingestion_id
            FROM daily_canonical_sources
            WHERE artifact_id=?
            ORDER BY source_ordinal
            """,
            (artifact_id,),
        ).fetchall()

        statuses = con.execute(
            """
            SELECT status
            FROM data_ingestions
            ORDER BY raw_path
            """
        ).fetchall()

        index_count = con.execute(
            "SELECT COUNT(*) FROM canonical_data_artifacts"
        ).fetchone()[0]

    assert artifact["status"] == "VALIDATED"
    assert [r["ingestion_id"] for r in links] == source_ids
    assert all(r["status"] == "VALIDATED" for r in statuses)
    assert index_count == 0


def test_exact_replay_is_idempotent(tmp_path):
    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    repo = DailyCanonicalArtifactRepository(db)

    first = repo.promote_validated_artifact(
        manifest=artifact_manifest(),
        source_ingestion_ids=source_ids,
    )

    second = repo.promote_validated_artifact(
        manifest=artifact_manifest(),
        source_ingestion_ids=source_ids,
    )

    assert first == second

    with db.connect() as con:
        artifacts = con.execute(
            "SELECT COUNT(*) FROM daily_canonical_artifacts"
        ).fetchone()[0]

        links = con.execute(
            "SELECT COUNT(*) FROM daily_canonical_sources"
        ).fetchone()[0]

    assert artifacts == 1
    assert links == 2


def test_rejected_source_rolls_back(tmp_path):
    import pytest

    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    with db.connect() as con:
        con.execute(
            """
            UPDATE data_ingestions
            SET status='REJECTED'
            WHERE ingestion_id=?
            """,
            (source_ids[-1],),
        )

    repo = DailyCanonicalArtifactRepository(db)

    with pytest.raises(
        ValueError,
        match="must be RECEIVED",
    ):
        repo.promote_validated_artifact(
            manifest=artifact_manifest(),
            source_ingestion_ids=source_ids,
        )

    with db.connect() as con:
        artifacts = con.execute(
            "SELECT COUNT(*) "
            "FROM daily_canonical_artifacts"
        ).fetchone()[0]

        links = con.execute(
            "SELECT COUNT(*) "
            "FROM daily_canonical_sources"
        ).fetchone()[0]

    assert artifacts == 0
    assert links == 0


def test_record_count_mismatch_rolls_back(tmp_path):
    import pytest

    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    with db.connect() as con:
        con.execute(
            """
            UPDATE data_ingestions
            SET record_count=99
            WHERE ingestion_id=?
            """,
            (source_ids[-1],),
        )

    repo = DailyCanonicalArtifactRepository(db)

    with pytest.raises(
        ValueError,
        match="record total",
    ):
        repo.promote_validated_artifact(
            manifest=artifact_manifest(),
            source_ingestion_ids=source_ids,
        )

    with db.connect() as con:
        artifacts = con.execute(
            "SELECT COUNT(*) "
            "FROM daily_canonical_artifacts"
        ).fetchone()[0]

    assert artifacts == 0


def test_provider_symbol_metadata_mismatch_rejected(
    tmp_path,
):
    import json
    import pytest

    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    with db.connect() as con:
        row = con.execute(
            """
            SELECT metadata_json
            FROM data_ingestions
            WHERE ingestion_id=?
            """,
            (source_ids[-1],),
        ).fetchone()

        metadata = json.loads(
            row["metadata_json"]
        )
        metadata["provider_symbol"] = "WRONG.EGX"

        con.execute(
            """
            UPDATE data_ingestions
            SET metadata_json=?
            WHERE ingestion_id=?
            """,
            (
                json.dumps(metadata),
                source_ids[-1],
            ),
        )

    repo = DailyCanonicalArtifactRepository(db)

    with pytest.raises(
        ValueError,
        match="provider_symbol",
    ):
        repo.promote_validated_artifact(
            manifest=artifact_manifest(),
            source_ingestion_ids=source_ids,
        )


def test_conflicting_replay_is_rejected(tmp_path):
    from dataclasses import replace
    import pytest

    db = make_database(tmp_path)
    source_ids = seed_sources(db)

    repo = DailyCanonicalArtifactRepository(db)

    repo.promote_validated_artifact(
        manifest=artifact_manifest(),
        source_ingestion_ids=source_ids,
    )

    conflicting = replace(
        artifact_manifest(),
        sha256="e" * 64,
    )

    with pytest.raises(
        ValueError,
        match="conflicts on sha256",
    ):
        repo.promote_validated_artifact(
            manifest=conflicting,
            source_ingestion_ids=source_ids,
        )
