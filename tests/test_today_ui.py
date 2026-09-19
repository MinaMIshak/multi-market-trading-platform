from datetime import date
import sqlite3

import pytest

from app.domain import (
    MarketSession,
    MarketSessionStatus,
)
from app.ui.today import (
    load_today_state,
    render_today_dashboard,
)


def make_ui_db(
    tmp_path,
    session_status=MarketSessionStatus.VERIFIED,
):
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
        """
        CREATE TABLE market_sessions (
            market_date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
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

    session = MarketSession(
        market_date=date(2026, 9, 10),
        status=session_status,
    )

    con.execute(
        """
        INSERT INTO market_sessions (
            market_date,
            status,
            payload_json,
            updated_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            session.market_date.isoformat(),
            session.status.value,
            session.model_dump_json(),
            "2026-09-10T07:00:00+00:00",
        ),
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


def test_late_query_failure_discards_partial_symbols(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE daily_canonical_sources")
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    state = load_today_state()
    assert state == {
        "available": False, "symbols": [], "counts": {},
        "error": "OperationalError",
    }
    assert "COMI" not in render_today_dashboard(state)


def test_database_path_uri_characters_are_literal(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    renamed = path.with_name("paper?#%.db")
    path.rename(renamed)
    monkeypatch.setenv("EGX_DB_PATH", str(renamed))
    assert load_today_state()["available"] is True


def test_snapshot_is_consistent_during_concurrent_write(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    original_connect = sqlite3.connect
    with original_connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")

    class ConcurrentConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "SELECT COUNT(*) FROM data_ingestions":
                with original_connect(path) as writer:
                    writer.execute("INSERT INTO data_ingestions VALUES ('i2')")
                    writer.execute("DELETE FROM daily_canonical_artifacts")
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw:
                        original_connect(*a, **kw, factory=ConcurrentConnection))
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    state = load_today_state()
    assert state["available"] is True
    assert len(state["symbols"]) == 1
    assert state["counts"]["ingestions"] == 1
    with original_connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM data_ingestions").fetchone()[0] == 2


def test_integrity_failure_discards_snapshot_and_closes(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    original_connect = sqlite3.connect
    closed = []

    class BadIntegrityConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA quick_check":
                return super().execute("SELECT 'integrity failure'")
            return super().execute(sql, *args, **kwargs)

        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw:
                        original_connect(*a, **kw, factory=BadIntegrityConnection))
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    assert load_today_state() == {
        "available": False, "symbols": [], "counts": {}, "error": "ValueError",
    }
    assert closed == [True]



def test_today_state_surfaces_verified_market_session(
    tmp_path,
):
    state = load_today_state(
        make_ui_db(tmp_path),
        market_date=date(2026, 9, 10),
    )

    assert state["available"] is True
    assert state["market_session"] == {
        "market_date": "2026-09-10",
        "status": "VERIFIED",
        "calendar_truth":
            "VERIFIED_TRADING_DAY",
        "data_verified_at": None,
    }


@pytest.mark.parametrize(
    "status",
    (
        MarketSessionStatus.HOLIDAY,
        MarketSessionStatus.WEEKEND,
    ),
)
def test_today_state_surfaces_verified_non_trading_day(
    tmp_path,
    status,
):
    state = load_today_state(
        make_ui_db(
            tmp_path,
            session_status=status,
        ),
        market_date=date(2026, 9, 10),
    )

    assert state["available"] is True
    assert (
        state["market_session"]["status"]
        == status.value
    )
    assert (
        state["market_session"]["calendar_truth"]
        == "VERIFIED_NON_TRADING_DAY"
    )


def test_today_state_keeps_lifecycle_state_unverified(
    tmp_path,
):
    state = load_today_state(
        make_ui_db(
            tmp_path,
            session_status=(
                MarketSessionStatus.CLOSED
            ),
        ),
        market_date=date(2026, 9, 10),
    )

    assert state["available"] is True
    assert (
        state["market_session"]["status"]
        == "CLOSED"
    )
    assert (
        state["market_session"]["calendar_truth"]
        == "UNVERIFIED"
    )


def test_today_state_missing_market_session_is_unverified(
    tmp_path,
):
    path = make_ui_db(tmp_path)

    with sqlite3.connect(path) as con:
        con.execute(
            "DELETE FROM market_sessions"
        )

    state = load_today_state(
        path,
        market_date=date(2026, 9, 10),
    )

    assert state["available"] is True
    assert state["market_session"] == {
        "market_date": "2026-09-10",
        "status": None,
        "calendar_truth": "UNVERIFIED",
        "data_verified_at": None,
    }


def test_today_state_fails_closed_on_session_status_mismatch(
    tmp_path,
):
    path = make_ui_db(tmp_path)

    with sqlite3.connect(path) as con:
        con.execute(
            """
            UPDATE market_sessions
            SET status = 'HOLIDAY'
            WHERE market_date = '2026-09-10'
            """
        )

    state = load_today_state(
        path,
        market_date=date(2026, 9, 10),
    )

    assert state == {
        "available": False,
        "symbols": [],
        "counts": {},
        "error": "ValueError",
    }


def test_today_dashboard_surfaces_market_session_truth():
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {},
        "symbols": [],
        "market_session": {
            "market_date": "2026-09-10",
            "status": "VERIFIED",
            "calendar_truth":
                "VERIFIED_TRADING_DAY",
            "data_verified_at": None,
        },
    }

    page = render_today_dashboard(state)

    assert "Market Session" in page
    assert "VERIFIED" in page
    assert "Calendar Truth" in page
    assert "VERIFIED_TRADING_DAY" in page

    # Session truth must not accidentally enable
    # strategy recommendations.
    assert "NO SETUP ENGINE" not in page
    assert (
        "Trading signals are intentionally disabled"
        in page
    )
