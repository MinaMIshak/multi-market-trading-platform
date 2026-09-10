import sqlite3

from app.storage import (
    Database,
    SCHEMA_VERSION,
)


def test_holiday_evidence_schema_exists(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    assert SCHEMA_VERSION == 7
    assert database.schema_version() == 7

    with database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    holiday_evidence
                )
                """
            ).fetchall()
        }

        indexes = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA index_list(
                    holiday_evidence
                )
                """
            ).fetchall()
        }

    assert {
        "evidence_id",
        "authority",
        "observed_date",
        "nominal_date",
        "market_closed",
        "source_uri",
        "source_published_at",
        "content_hash",
        "received_at",
        "metadata_json",
    }.issubset(columns)

    assert (
        "idx_holiday_evidence_observed_date"
        in indexes
    )

    assert (
        "idx_holiday_evidence_authority_date"
        in indexes
    )


def test_market_closed_is_boolean_like(
    tmp_path,
):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    with database.connect() as connection:
        try:
            connection.execute(
                """
                INSERT INTO holiday_evidence (
                    evidence_id,
                    authority,
                    observed_date,
                    market_closed,
                    source_uri,
                    content_hash,
                    received_at,
                    metadata_json
                )
                VALUES (
                    'bad',
                    'egx_official',
                    '2026-09-10',
                    2,
                    'https://example.test',
                    'abc',
                    '2026-09-10T08:00:00+00:00',
                    '{}'
                )
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError(
                "invalid market_closed accepted"
            )


def test_v6_style_database_upgrades_additively(
    tmp_path,
):
    path = tmp_path / "platform.db"

    connection = sqlite3.connect(path)

    try:
        connection.executescript(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT INTO schema_meta (
                key,
                value
            )
            VALUES (
                'schema_version',
                '6'
            );

            CREATE TABLE market_sessions (
                market_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            INSERT INTO market_sessions (
                market_date,
                status,
                payload_json,
                updated_at
            )
            VALUES (
                '2026-09-09',
                'UNKNOWN',
                '{}',
                '2026-09-09T00:00:00+00:00'
            );
            """
        )

        connection.commit()
    finally:
        connection.close()

    database = Database(path)
    database.initialize()

    assert database.schema_version() == 7

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT status
            FROM market_sessions
            WHERE market_date = '2026-09-09'
            """
        ).fetchone()

        holiday_table = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'holiday_evidence'
            """
        ).fetchone()

        quick = connection.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

    assert row is not None
    assert row["status"] == "UNKNOWN"
    assert holiday_table["count"] == 1
    assert quick == "ok"
