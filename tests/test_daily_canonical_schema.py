import sqlite3

from app.storage import Database


def test_daily_canonical_tables_and_fks(tmp_path):
    db=Database(tmp_path/"test.db")
    db.initialize()

    with db.connect() as con:
        tables={
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        assert "daily_canonical_artifacts" in tables
        assert "daily_canonical_sources" in tables

        assert len(
            con.execute(
                "PRAGMA foreign_key_list("
                "daily_canonical_artifacts)"
            ).fetchall()
        ) == 1

        assert len(
            con.execute(
                "PRAGMA foreign_key_list("
                "daily_canonical_sources)"
            ).fetchall()
        ) == 2


def test_daily_canonical_count_constraint(tmp_path):
    db=Database(tmp_path/"test.db")
    db.initialize()

    with db.connect() as con:
        con.execute("""
        INSERT INTO canonical_instruments (
          instrument_id,instrument_type,canonical_ticker,
          source_provider,source_symbol_code,source_sha256,
          normalization_notes_json,updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,(
          "i1","EQUITY","COMI","egid","code",
          "a"*64,"[]","2026-09-09T00:00:00+00:00"
        ))

        with __import__("pytest").raises(sqlite3.IntegrityError):
            con.execute("""
            INSERT INTO daily_canonical_artifacts (
              artifact_id,instrument_id,canonical_symbol,
              provider,provider_symbol,source_snapshot_date,
              canonical_path,sha256,byte_size,record_count,
              oldest_market_date,newest_market_date,
              valid_bar_count,quarantined_bar_count,
              semantic_contract_version,serialization_format,
              status,created_at,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(
              "a1","i1","COMI","eodhd","COMI.EGX",
              "2026-09-09","a.json","b"*64,100,1,
              "2026-09-08","2026-09-08",0,0,
              "egx-daily-semantic-v1","canonical-json-v1",
              "VALIDATED","2026-09-09T00:00:00+00:00","{}"
            ))
