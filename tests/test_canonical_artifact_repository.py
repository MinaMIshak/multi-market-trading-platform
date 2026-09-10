from __future__ import annotations

from dataclasses import replace
from datetime import (
    date,
    datetime,
    timezone,
)
from uuid import uuid4

import pytest

from app.data.index_canonical_store import (
    CanonicalIndexArtifactManifest,
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
from app.storage import (
    CanonicalArtifactRepository,
    Database,
)


SNAPSHOT_DATE = date(
    2026,
    9,
    9,
)


def make_database(
    tmp_path,
) -> Database:
    database = Database(
        tmp_path / "platform.db"
    )

    database.initialize()

    return database


def seed_sources(
    database: Database,
) -> list[str]:
    repository = (
        DataIngestionRepository(
            database
        )
    )

    source_ids: list[str] = []

    record_counts = (
        [1000] * 6
        + [989]
    )

    for index, record_count in enumerate(
        record_counts,
        start=1,
    ):
        ingestion_id = uuid4()

        source_ids.append(
            str(ingestion_id)
        )

        manifest = RawArtifactManifest(
            ingestion_id=ingestion_id,
            provider=(
                "egx_official_public"
            ),
            asset_type=(
                DataAssetType
                .INDEX_BARS
            ),
            granularity=(
                BarGranularity.D1
            ),
            symbol="CASE30",
            market_date=(
                SNAPSHOT_DATE
            ),
            raw_path=(
                "egx_official_public/"
                "index_bars/"
                "2026/09/09/"
                "CASE30/"
                f"page-{index:04d}.json"
            ),
            sha256=(
                str(index) * 64
            ),
            byte_size=100,
            record_count=(
                record_count
            ),
            received_at=(
                datetime.now(
                    timezone.utc
                )
            ),
        )

        repository.save_manifest(
            manifest,
            status=(
                IngestionStatus
                .RECEIVED
            ),
        )

    return source_ids


def artifact_manifest(
) -> CanonicalIndexArtifactManifest:
    return (
        CanonicalIndexArtifactManifest(
            provider=(
                "egx_official_public"
            ),
            asset_type=(
                DataAssetType
                .INDEX_BARS
            ),
            granularity=(
                BarGranularity.D1
            ),
            index_name="CASE30",
            source_snapshot_date=(
                SNAPSHOT_DATE
            ),
            oldest_market_date=date(
                1998,
                1,
                1,
            ),
            newest_market_date=date(
                2026,
                9,
                8,
            ),
            relative_path=(
                "egx_official_public/"
                "index_bars/"
                "2026/09/09/"
                "CASE30/"
                "CASE30-1998-01-01-"
                "2026-09-08-"
                "D1-canonical-v1.json"
            ),
            sha256="d" * 64,
            byte_size=3186679,
            record_count=6989,
            full_ohlc_valid_count=5161,
            legacy_close_reference_count=1823,
            quarantined_anomaly_count=5,
            full_ohlc_usable_count=5161,
            close_history_usable_count=6984,
            semantic_contract_version=(
                "egx-index-semantic-v1"
            ),
            serialization_format=(
                "canonical-json-v1"
            ),
        )
    )


def test_current_schema_version_and_tables_exist(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    assert (
        database.schema_version()
        == 9
    )

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            ).fetchall()
        }

    assert (
        "canonical_data_artifacts"
        in tables
    )

    assert (
        "canonical_artifact_sources"
        in tables
    )

    assert (
        "holiday_evidence"
        in tables
    )


def test_atomic_promotion_validates_all_sources(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    source_ids = seed_sources(
        database
    )

    repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    artifact_id = (
        repository
        .promote_validated_artifact(
            manifest=(
                artifact_manifest()
            ),
            source_ingestion_ids=(
                source_ids
            ),
            metadata={
                "validation": "test",
            },
        )
    )

    with database.connect() as connection:
        artifact = (
            connection.execute(
                """
                SELECT *
                FROM canonical_data_artifacts
                WHERE artifact_id = ?
                """,
                (artifact_id,),
            )
            .fetchone()
        )

        links = (
            connection.execute(
                """
                SELECT
                    ingestion_id,
                    source_ordinal
                FROM canonical_artifact_sources
                WHERE artifact_id = ?
                ORDER BY source_ordinal
                """,
                (artifact_id,),
            )
            .fetchall()
        )

        validated = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM data_ingestions
                WHERE status='VALIDATED'
                """
            )
            .fetchone()
        )

    assert artifact is not None

    assert (
        artifact["status"]
        == "VALIDATED"
    )

    assert (
        artifact["record_count"]
        == 6989
    )

    assert (
        artifact[
            "full_ohlc_valid_count"
        ]
        == 5161
    )

    assert [
        row["ingestion_id"]
        for row in links
    ] == source_ids

    assert [
        row["source_ordinal"]
        for row in links
    ] == list(
        range(
            1,
            8,
        )
    )

    assert (
        validated["count"]
        == 7
    )


def test_rejected_source_rolls_back_everything(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    source_ids = seed_sources(
        database
    )

    with database.connect() as connection:
        connection.execute(
            """
            UPDATE data_ingestions
            SET status='REJECTED'
            WHERE ingestion_id = ?
            """,
            (
                source_ids[-1],
            ),
        )

    repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    with pytest.raises(
        ValueError,
        match="RECEIVED",
    ):
        repository.promote_validated_artifact(
            manifest=(
                artifact_manifest()
            ),
            source_ingestion_ids=(
                source_ids
            ),
        )

    with database.connect() as connection:
        artifact_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_data_artifacts
                """
            )
            .fetchone()["count"]
        )

        link_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_artifact_sources
                """
            )
            .fetchone()["count"]
        )

        validated_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM data_ingestions
                WHERE status='VALIDATED'
                """
            )
            .fetchone()["count"]
        )

    assert artifact_count == 0
    assert link_count == 0
    assert validated_count == 0


def test_source_record_mismatch_rolls_back(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    source_ids = seed_sources(
        database
    )

    with database.connect() as connection:
        connection.execute(
            """
            UPDATE data_ingestions
            SET record_count=988
            WHERE ingestion_id = ?
            """,
            (
                source_ids[-1],
            ),
        )

    repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    with pytest.raises(
        ValueError,
        match="record total",
    ):
        repository.promote_validated_artifact(
            manifest=(
                artifact_manifest()
            ),
            source_ingestion_ids=(
                source_ids
            ),
        )

    with database.connect() as connection:
        artifact_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_data_artifacts
                """
            )
            .fetchone()["count"]
        )

        validated_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM data_ingestions
                WHERE status='VALIDATED'
                """
            )
            .fetchone()["count"]
        )

    assert artifact_count == 0
    assert validated_count == 0


def test_exact_replay_is_idempotent(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    source_ids = seed_sources(
        database
    )

    repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    first = (
        repository
        .promote_validated_artifact(
            manifest=(
                artifact_manifest()
            ),
            source_ingestion_ids=(
                source_ids
            ),
        )
    )

    second = (
        repository
        .promote_validated_artifact(
            manifest=(
                artifact_manifest()
            ),
            source_ingestion_ids=(
                source_ids
            ),
        )
    )

    assert first == second

    with database.connect() as connection:
        artifact_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_data_artifacts
                """
            )
            .fetchone()["count"]
        )

        link_count = (
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_artifact_sources
                """
            )
            .fetchone()["count"]
        )

    assert artifact_count == 1
    assert link_count == 7


def test_conflicting_artifact_is_rejected(
    tmp_path,
):
    database = make_database(
        tmp_path
    )

    source_ids = seed_sources(
        database
    )

    repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    original = artifact_manifest()

    repository.promote_validated_artifact(
        manifest=original,
        source_ingestion_ids=(
            source_ids
        ),
    )

    conflicting = replace(
        original,
        sha256="e" * 64,
    )

    with pytest.raises(
        ValueError,
        match="conflicts",
    ):
        repository.promote_validated_artifact(
            manifest=conflicting,
            source_ingestion_ids=(
                source_ids
            ),
        )
