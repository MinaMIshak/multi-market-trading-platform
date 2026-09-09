import sqlite3

from app.ui.today import (
    load_today_state,
    render_today_dashboard,
)


def make_ui_db(tmp_path):
    path = tmp_path / "platform.db"

    con = sqlite3.connect(path)

    con.execute(
        """
        CREATE TABLE data_ingestions (
            ingestion_id TEXT PRIMARY KEY
        )
        """
    )

    con.execute(
        """
        CREATE TABLE canonical_data_artifacts (
            artifact_id TEXT PRIMARY KEY
        )
        """
    )

    con.execute(
        """
        CREATE TABLE daily_canonical_sources (
            artifact_id TEXT,
            ingestion_id TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE daily_canonical_artifacts (
            canonical_symbol TEXT,
            provider TEXT,
            provider_symbol TEXT,
            source_snapshot_date TEXT,
            oldest_market_date TEXT,
            newest_market_date TEXT,
            valid_bar_count INTEGER,
            quarantined_bar_count INTEGER,
            status TEXT
        )
        """
    )

    con.execute(
        "INSERT INTO data_ingestions VALUES ('i1')"
    )

    con.execute(
        "INSERT INTO canonical_data_artifacts VALUES ('x1')"
    )

    con.execute(
        """
        INSERT INTO daily_canonical_sources
        VALUES ('a1', 'i1')
        """
    )

    con.execute(
        """
        INSERT INTO daily_canonical_artifacts
        VALUES (
            'COMI',
            'eodhd',
            'COMI.EGX',
            '2026-09-09',
            '2026-09-01',
            '2026-09-09',
            7,
            0,
            'VALIDATED'
        )
        """
    )

    con.commit()
    con.close()

    return path


def test_today_state_reads_validated_data(
    tmp_path,
    monkeypatch,
):
    path = make_ui_db(tmp_path)

    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(path),
    )

    state = load_today_state()

    assert state["available"] is True
    assert state["integrity"] == "ok"

    assert state["counts"] == {
        "ingestions": 1,
        "daily_artifacts": 1,
        "daily_sources": 1,
        "index_artifacts": 1,
        "quarantined": 0,
    }

    assert len(state["symbols"]) == 1
    assert (
        state["symbols"][0]["canonical_symbol"]
        == "COMI"
    )


def test_today_dashboard_is_trader_safe():
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {
            "ingestions": 1,
            "daily_artifacts": 1,
            "daily_sources": 1,
            "index_artifacts": 3,
            "quarantined": 0,
        },
        "symbols": [
            {
                "canonical_symbol": "COMI",
                "provider": "eodhd",
                "provider_symbol": "COMI.EGX",
                "source_snapshot_date": "2026-09-09",
                "oldest_market_date": "2026-09-01",
                "newest_market_date": "2026-09-09",
                "valid_bar_count": 7,
                "quarantined_bar_count": 0,
                "status": "VALIDATED",
            }
        ],
    }

    page = render_today_dashboard(state)

    assert "EGX Trading Platform" in page
    assert "Validated Daily Universe" in page
    assert "COMI" in page

    assert "TODAY" in page
    assert "LIVE" in page
    assert "PRE-SURGE" in page
    assert "SWING" in page
    assert "PERFORMANCE" in page
    assert "RESEARCH" in page
    assert "SYSTEM" in page

    assert "NO SETUP ENGINE" in page
    assert "Trading signals are intentionally disabled" in page


def test_today_state_fails_closed(
    tmp_path,
    monkeypatch,
):
    missing = (
        tmp_path
        / "does-not-exist.db"
    )

    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(missing),
    )

    state = load_today_state()

    assert state["available"] is False
    assert state["symbols"] == []
    assert state["counts"] == {}
    assert state["error"] is not None
