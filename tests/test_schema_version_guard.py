import sqlite3

import pytest

from app.storage import (
    Database,
    SCHEMA_VERSION,
)


def seed_version(path, version):
    con = sqlite3.connect(path)

    try:
        con.executescript(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE market_sessions (
                market_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        con.execute(
            """
            INSERT INTO schema_meta (
                key,
                value
            )
            VALUES (
                'schema_version',
                ?
            )
            """,
            (str(version),),
        )

        con.commit()
    finally:
        con.close()


def table_exists(path, name):
    con = sqlite3.connect(path)

    try:
        return bool(
            con.execute(
                """
                SELECT COUNT(*)
                FROM sqlite_master
                WHERE type='table'
                  AND name=?
                """,
                (name,),
            ).fetchone()[0]
        )
    finally:
        con.close()


def test_older_schema_requires_explicit_upgrade(
    tmp_path,
):
    path = tmp_path / "platform.db"
    seed_version(path, 6)

    database = Database(path)

    with pytest.raises(
        RuntimeError,
        match="upgrade required",
    ):
        database.initialize()

    assert database.schema_version() == 6
    assert not table_exists(
        path,
        "holiday_evidence",
    )


def test_explicit_upgrade_is_additive(
    tmp_path,
):
    path = tmp_path / "platform.db"
    seed_version(path, 6)

    database = Database(path)

    database.initialize(
        allow_upgrade=True
    )

    assert (
        database.schema_version()
        == SCHEMA_VERSION
        == 7
    )

    assert table_exists(
        path,
        "holiday_evidence",
    )


def test_newer_schema_is_never_downgraded(
    tmp_path,
):
    path = tmp_path / "platform.db"
    seed_version(path, 8)

    database = Database(path)

    with pytest.raises(
        RuntimeError,
        match="newer than application",
    ):
        database.initialize(
            allow_upgrade=True
        )

    assert database.schema_version() == 8


def test_unknown_existing_database_fails_closed(
    tmp_path,
):
    path = tmp_path / "platform.db"

    con = sqlite3.connect(path)

    try:
        con.execute(
            """
            CREATE TABLE legacy_unknown (
                id INTEGER PRIMARY KEY
            )
            """
        )
        con.commit()
    finally:
        con.close()

    database = Database(path)

    with pytest.raises(
        RuntimeError,
        match="metadata missing",
    ):
        database.initialize()

    assert table_exists(
        path,
        "legacy_unknown",
    )
    assert not table_exists(
        path,
        "schema_meta",
    )
