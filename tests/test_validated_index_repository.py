from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from app.data.index_canonical import (
    CanonicalIndexDailyBar,
    IndexBarSemanticClass,
)
from app.data.index_canonical_store import (
    CanonicalIndexStore,
    SEMANTIC_CONTRACT_VERSION,
    SERIALIZATION_FORMAT,
)
from app.data.validated_index_repository import (
    ValidatedCanonicalIndexError,
    ValidatedCanonicalIndexRepository,
)
from app.storage import Database


SNAPSHOT = date(2026, 9, 9)


def build_fixture(tmp_path: Path):
    db = Database(
        tmp_path / "platform.db"
    )
    db.initialize()

    canonical_root = (
        tmp_path / "canonical"
    )
    canonical_root.mkdir()

    source_sha = "a" * 64

    rows = [
        CanonicalIndexDailyBar(
            index_name="CASE30",
            market_date=date(2026, 9, 8),
            semantic_class=(
                IndexBarSemanticClass
                .FULL_OHLC_VALID
            ),
            open="100",
            high="110",
            low="95",
            close="105",
            reference_level=None,
            quality_flags=(),
            source_provider=(
                "egx_official_public"
            ),
            source_snapshot_date=SNAPSHOT,
            source_page_number=1,
            source_row_number=1,
            source_sha256=source_sha,
        )
    ]

    payload = (
        CanonicalIndexStore
        .serialize_rows(rows)
    )

    relative = (
        "egx_official_public/"
        "index_bars/2026/09/09/"
        "CASE30/test.json"
    )

    artifact_path = (
        canonical_root / relative
    )
    artifact_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    artifact_path.write_bytes(payload)

    artifact_id = str(uuid4())
    ingestion_id = str(uuid4())

    digest = hashlib.sha256(
        payload
    ).hexdigest()

    with db.connect() as con:
        con.execute(
            """
            INSERT INTO data_ingestions (
                ingestion_id,
                provider,
                asset_type,
                granularity,
                symbol,
                market_date,
                raw_path,
                sha256,
                byte_size,
                record_count,
                status,
                source_uri,
                received_at,
                completed_at,
                metadata_json
            )
            VALUES (
                ?, 'egx_official_public',
                'INDEX_BARS', 'D1',
                'CASE30', '2026-09-09',
                'raw-page.json',
                ?, 1, 1,
                'VALIDATED',
                NULL,
                '2026-09-09T00:00:00+00:00',
                '2026-09-09T00:00:01+00:00',
                '{}'
            )
            """,
            (
                ingestion_id,
                source_sha,
            ),
        )

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
                ?,
                'egx_official_public',
                'INDEX_BARS',
                'D1',
                'CASE30',
                '2026-09-09',
                ?, ?, ?, 1,
                '2026-09-08',
                '2026-09-08',
                ?, ?,
                1, 0, 0,
                1, 1,
                'VALIDATED',
                '2026-09-09T00:00:00+00:00',
                '2026-09-09T00:00:01+00:00',
                '{}'
            )
            """,
            (
                artifact_id,
                relative,
                digest,
                len(payload),
                SEMANTIC_CONTRACT_VERSION,
                SERIALIZATION_FORMAT,
            ),
        )

        con.execute(
            """
            INSERT INTO canonical_artifact_sources (
                artifact_id,
                ingestion_id,
                source_ordinal
            )
            VALUES (?, ?, 1)
            """,
            (
                artifact_id,
                ingestion_id,
            ),
        )

    repo = ValidatedCanonicalIndexRepository(
        database=db,
        canonical_root=canonical_root,
    )

    return (
        db,
        repo,
        artifact_path,
        artifact_id,
        ingestion_id,
    )


def test_validated_reader_happy_path(
    tmp_path,
):
    _, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    dataset = repo.load(
        index_name="CASE30",
        source_snapshot_date=SNAPSHOT,
    )

    assert dataset.artifact_id == artifact_id
    assert len(dataset.rows) == 1
    assert len(dataset.full_ohlc_rows) == 1
    assert len(dataset.close_history_rows) == 1


def test_rejects_tampered_canonical_file(
    tmp_path,
):
    _, repo, path, _, _ = (
        build_fixture(tmp_path)
    )

    path.write_bytes(
        path.read_bytes() + b" "
    )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="byte size|SHA256",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_nonvalidated_source(
    tmp_path,
):
    db, repo, _, _, ingestion_id = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE data_ingestions
            SET status='REJECTED'
            WHERE ingestion_id=?
            """,
            (ingestion_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="non-validated",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_path_escape(
    tmp_path,
):
    db, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE canonical_data_artifacts
            SET canonical_path='../outside.json'
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="escapes canonical root",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_missing_validated_artifact(
    tmp_path,
):
    db, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE canonical_data_artifacts
            SET status='REJECTED'
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="exactly one",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_missing_canonical_file(
    tmp_path,
):
    _, repo, path, _, _ = (
        build_fixture(tmp_path)
    )

    path.unlink()

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="does not exist",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_catalog_semantic_count_tamper(
    tmp_path,
):
    db, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE canonical_data_artifacts
            SET
                full_ohlc_valid_count=0,
                legacy_close_reference_count=1
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="semantic counts",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_source_sha_tamper(
    tmp_path,
):
    db, repo, _, _, ingestion_id = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE data_ingestions
            SET sha256=?
            WHERE ingestion_id=?
            """,
            (
                "b" * 64,
                ingestion_id,
            ),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="source SHA256",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_source_ordinal_gap(
    tmp_path,
):
    db, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE canonical_artifact_sources
            SET source_ordinal=2
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="contiguous",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )


def test_rejects_usable_count_tamper(
    tmp_path,
):
    db, repo, _, artifact_id, _ = (
        build_fixture(tmp_path)
    )

    with db.connect() as con:
        con.execute(
            """
            UPDATE canonical_data_artifacts
            SET full_ohlc_usable_count=0
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )

    with pytest.raises(
        ValidatedCanonicalIndexError,
        match="full-OHLC usable count",
    ):
        repo.load(
            index_name="CASE30",
            source_snapshot_date=SNAPSHOT,
        )
