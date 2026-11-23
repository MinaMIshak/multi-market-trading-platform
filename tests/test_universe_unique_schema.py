import sqlite3
from uuid import uuid4

import pytest

from app.storage.database import (
    Database,
    SCHEMA_VERSION,
)


def insert_ingestion(
    connection,
    ingestion_id,
):
    connection.execute(
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
            received_at,
            status,
            completed_at,
            source_uri,
            metadata_json
        )
        VALUES (
            ?, 'fixture', 'SECURITY_MASTER',
            NULL, NULL, '2026-11-23',
            ?, ?, 10, 1,
            '2026-11-23T20:00:00+00:00',
            'VALIDATED',
            '2026-11-23T20:00:01+00:00',
            'fixture://universe',
            '{"contract":"egx-universe-v1"}'
        )
        """,
        (
            ingestion_id,
            f"fixture/{ingestion_id}.json",
            "a" * 64,
        ),
    )


def test_schema_13_enforces_one_universe_per_effective_date(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )

    database.initialize()

    assert database.schema_version() == 13
    assert SCHEMA_VERSION == 13

    with database.connect() as connection:
        first = str(uuid4())
        second = str(uuid4())

        insert_ingestion(
            connection,
            first,
        )
        insert_ingestion(
            connection,
            second,
        )

        connection.execute(
            """
            INSERT INTO reference_artifacts (
                ingestion_id,
                contract,
                effective_date
            )
            VALUES (
                ?,
                'egx-universe-v1',
                '2026-11-23'
            )
            """,
            (first,),
        )

        with pytest.raises(
            sqlite3.IntegrityError
        ):
            connection.execute(
                """
                INSERT INTO reference_artifacts (
                    ingestion_id,
                    contract,
                    effective_date
                )
                VALUES (
                    ?,
                    'egx-universe-v1',
                    '2026-11-23'
                )
                """,
                (second,),
            )


def test_schema_13_allows_universes_for_different_dates(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )

    database.initialize()

    with database.connect() as connection:
        first = str(uuid4())
        second = str(uuid4())

        insert_ingestion(
            connection,
            first,
        )
        insert_ingestion(
            connection,
            second,
        )

        connection.execute(
            """
            INSERT INTO reference_artifacts (
                ingestion_id,
                contract,
                effective_date
            )
            VALUES (
                ?,
                'egx-universe-v1',
                '2026-11-22'
            )
            """,
            (first,),
        )

        connection.execute(
            """
            INSERT INTO reference_artifacts (
                ingestion_id,
                contract,
                effective_date
            )
            VALUES (
                ?,
                'egx-universe-v1',
                '2026-11-23'
            )
            """,
            (second,),
        )


def test_upgrade_rejects_legacy_duplicate_dated_universes(
    tmp_path,
):
    path = tmp_path / "legacy.db"

    database = Database(path)
    database.initialize()

    # Simulate a legacy v12 database before the unique
    # index existed.
    with database.connect() as connection:
        connection.execute(
            """
            DROP INDEX
            idx_reference_universe_effective_date_unique
            """
        )

        connection.execute(
            """
            UPDATE schema_meta
            SET value = '12'
            WHERE key = 'schema_version'
            """
        )

        first = str(uuid4())
        second = str(uuid4())

        insert_ingestion(
            connection,
            first,
        )
        insert_ingestion(
            connection,
            second,
        )

        for ingestion_id in (
            first,
            second,
        ):
            connection.execute(
                """
                INSERT INTO reference_artifacts (
                    ingestion_id,
                    contract,
                    effective_date
                )
                VALUES (
                    ?,
                    'egx-universe-v1',
                    '2026-11-23'
                )
                """,
                (ingestion_id,),
            )

    with pytest.raises(
        sqlite3.IntegrityError
    ):
        database.initialize(
            allow_upgrade=True
        )

    # Failed upgrade must not falsely advertise schema 13.
    assert database.schema_version() == 12


def test_reference_repository_exact_universe_replay_remains_idempotent(
    tmp_path,
):
    import json
    from datetime import datetime, timezone
    from uuid import UUID

    from app.data.raw_store import ImmutableRawStore
    from app.storage.reference_repository import ReferenceRepository

    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = ReferenceRepository(
        database,
        ImmutableRawStore(
            tmp_path / "raw"
        ),
    )

    document = {
        "contract": "egx-universe-v1",
        "market": "EGX",
        "effective_date": "2026-11-23",
        "published_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "complete": True,
        "members": [
            {
                "instrument_id": str(
                    UUID(
                        "00000000-0000-0000-0000-000000000001"
                    )
                ),
                "symbol": "COMI",
                "eligible": True,
            }
        ],
    }

    payload = json.dumps(
        document
    ).encode()

    first = repository.ingest(
        provider="fixture",
        payload=payload,
        source_uri="fixture://universe",
        contract="egx-universe-v1",
    )

    second = repository.ingest(
        provider="fixture",
        payload=payload,
        source_uri="fixture://universe",
        contract="egx-universe-v1",
    )

    assert first == second

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM reference_artifacts
            WHERE contract='egx-universe-v1'
              AND effective_date='2026-11-23'
            """
        ).fetchone()[0] == 1
