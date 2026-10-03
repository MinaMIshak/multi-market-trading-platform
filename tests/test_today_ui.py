from datetime import date
import re
import sqlite3

import pytest

from app.domain import (
    MarketSession,
    MarketSessionStatus,
)
from app.ui.today import (
    load_security_master_summary,
    load_validated_daily_observations,
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
        """
        CREATE TABLE canonical_instruments (
            instrument_id TEXT PRIMARY KEY,
            instrument_type TEXT NOT NULL,
            canonical_ticker TEXT NOT NULL,
            name_en TEXT,
            name_ar TEXT,
            short_name_en TEXT,
            short_name_ar TEXT,
            source_provider TEXT NOT NULL,
            source_symbol_code TEXT NOT NULL,
            reuters_raw TEXT,
            reuters_normalized TEXT,
            source_sha256 TEXT NOT NULL,
            source_market_date TEXT,
            normalization_notes_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    con.execute(
        """
        INSERT INTO canonical_instruments VALUES (
            'i-comi', 'EQUITY', 'COMI', 'Commercial International Bank', NULL,
            NULL, NULL, 'egid', '1', NULL, NULL, 'sha-comi', '2026-09-09',
            '{}', '2026-09-09T07:00:00+00:00'
        )
        """
    )

    con.execute(
        """
        INSERT INTO canonical_instruments VALUES (
            'i-swdy', 'EQUITY', 'SWDY', 'Elsewedy Electric', NULL,
            NULL, NULL, 'egid', '2', NULL, NULL, 'sha-swdy', '2026-09-09',
            '{}', '2026-09-09T07:00:00+00:00'
        )
        """
    )

    con.execute(
        """
        INSERT INTO canonical_instruments VALUES (
            'i-egx30', 'INDEX', 'EGX30', 'EGX 30 Index', NULL,
            NULL, NULL, 'egid', '3', NULL, NULL, 'sha-egx30', '2026-09-09',
            '{}', '2026-09-09T07:00:00+00:00'
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

    assert state["security_master"] == {
        "total_instruments": 3,
        "by_type": {"EQUITY": 2, "INDEX": 1},
        "source_providers": ["egid"],
        "latest_snapshot_updated_at": "2026-09-09T07:00:00+00:00",
        "latest_source_market_date": "2026-09-09",
    }


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
    assert "Validated Daily Data Inventory" in page
    assert "COMI" in page

    assert "TODAY" in page
    assert "LIVE" in page
    assert "PRE-SURGE" in page
    assert "SWING" in page
    assert "PERFORMANCE" in page
    assert "RESEARCH" in page
    assert "SYSTEM" in page

    assert "NO ADMITTED SETUP" in page
    assert "Current trading signals are unavailable" in page

    # No security_master key supplied: the page must say so, never imply zero.
    assert "NOT AVAILABLE" in page


def test_today_dashboard_renders_security_master_identities_with_disclaimer():
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {},
        "symbols": [],
        "security_master": {
            "total_instruments": 3,
            "by_type": {"EQUITY": 2, "INDEX": 1},
            "source_providers": ["egid"],
            "latest_snapshot_updated_at": "2026-09-09T07:00:00+00:00",
            "latest_source_market_date": "2026-09-09",
        },
    }

    page = render_today_dashboard(state)

    assert "Security master identities" in page
    assert "NOT price" in page and "NOT a trading signal" in page
    assert "dated exchange membership" in page
    assert "EQUITY: 2" in page
    assert "INDEX: 1" in page
    assert "egid" in page
    assert "2026-09-09T07:00:00+00:00" in page

    cards = dict(re.findall(
        r'<div class="label">([^<]+)</div>\s*<div class="value">([^<]+)</div>', page,
    ))
    assert cards["Security Master Identities"] == "3"


def test_today_dashboard_security_master_missing_dates_render_unknown():
    # Authentic EGID identity snapshots carry no source market date; the
    # page must say UNKNOWN, never leak a Python "None" literal.
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {},
        "symbols": [],
        "security_master": {
            "total_instruments": 1,
            "by_type": {"EQUITY": 1},
            "source_providers": ["egid"],
            "latest_snapshot_updated_at": None,
            "latest_source_market_date": None,
        },
    }

    page = render_today_dashboard(state)

    section = page[page.index("Security master identities"):]
    section = section[:section.index("</section>")]
    assert "None" not in section
    assert re.search(r"capture:\s*UNKNOWN", section)
    assert re.search(r"market date:\s*UNKNOWN", section)


def test_today_dashboard_security_master_identities_are_escaped():
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {},
        "symbols": [],
        "security_master": {
            "total_instruments": 1,
            "by_type": {"<b>EQUITY</b>": 1},
            "source_providers": ["<i>egid</i>"],
            "latest_snapshot_updated_at": "2026-09-09T07:00:00+00:00",
            "latest_source_market_date": "2026-09-09",
        },
    }

    page = render_today_dashboard(state)

    assert "<b>EQUITY</b>" not in page and "&lt;b&gt;EQUITY&lt;/b&gt;" in page
    assert "<i>egid</i>" not in page and "&lt;i&gt;egid&lt;/i&gt;" in page


def test_security_master_summary_reads_identities_read_only(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    before = path.read_bytes()

    assert load_security_master_summary() == {
        "total_instruments": 3,
        "by_type": {"EQUITY": 2, "INDEX": 1},
        "source_providers": ["egid"],
        "latest_snapshot_updated_at": "2026-09-09T07:00:00+00:00",
        "latest_source_market_date": "2026-09-09",
    }
    assert path.read_bytes() == before


def test_validated_daily_observations_read_only(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO daily_canonical_artifacts VALUES ("
        "'SWDY', 'eodhd', 'SWDY.EGX', '2026-09-09', '2026-09-01',"
        " '2026-09-09', 0, 7, 'QUARANTINED')"
    )
    con.commit()
    con.close()
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    before = path.read_bytes()

    rows = load_validated_daily_observations()

    assert [r["canonical_symbol"] for r in rows] == ["COMI"]
    assert set(rows[0]) == {
        "canonical_symbol", "provider", "source_snapshot_date",
        "oldest_market_date", "newest_market_date",
        "valid_bar_count", "quarantined_bar_count", "freshness",
    }
    assert path.read_bytes() == before


def _add_session(path, market_date, status, payload_date=None):
    session = MarketSession(
        market_date=payload_date or market_date, status=status,
    )
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO market_sessions (market_date, status, payload_json,"
        " updated_at) VALUES (?, ?, ?, ?)",
        (market_date.isoformat(), status.value, session.model_dump_json(),
         "2026-09-10T07:00:00+00:00"),
    )
    con.commit()
    con.close()


def _freshness(path, as_of):
    rows = load_validated_daily_observations(path, market_date=as_of)
    return None if rows is None else rows[0]["freshness"]


# Fixture: COMI newest bar 2026-09-09; 2026-09-10 session VERIFIED.
def test_daily_freshness_stale_when_verified_session_is_missing(tmp_path):
    path = make_ui_db(tmp_path)
    assert _freshness(path, date(2026, 9, 12)) == "STALE"


def test_daily_freshness_current_when_gap_is_empty(tmp_path):
    path = make_ui_db(tmp_path)
    assert _freshness(path, date(2026, 9, 10)) == "CURRENT"
    assert _freshness(path, date(2026, 9, 9)) == "CURRENT"


def test_daily_freshness_current_only_across_verified_non_trading_days(tmp_path):
    path = make_ui_db(tmp_path, session_status=MarketSessionStatus.HOLIDAY)
    _add_session(path, date(2026, 9, 11), MarketSessionStatus.WEEKEND)
    assert _freshness(path, date(2026, 9, 12)) == "CURRENT"


def test_daily_freshness_unknown_without_complete_calendar(tmp_path):
    path = make_ui_db(tmp_path, session_status=MarketSessionStatus.HOLIDAY)
    # 2026-09-11 (Friday) has no session record. Friday/Saturday are the fixed
    # EGX weekend by operator decision, so only they may be filled by rule.
    assert _freshness(path, date(2026, 9, 12)) == "CURRENT"
    # Sunday 2026-09-13 has no record: a Sunday-Thursday date is never inferred.
    assert _freshness(path, date(2026, 9, 14)) == "UNKNOWN"
    # Unverified lifecycle states are not calendar truth either.
    other = tmp_path / "other"
    other.mkdir()
    path = make_ui_db(other, session_status=MarketSessionStatus.CLOSED)
    assert _freshness(path, date(2026, 9, 11)) == "UNKNOWN"


def test_daily_freshness_unknown_for_future_dated_bars(tmp_path):
    path = make_ui_db(tmp_path)
    assert _freshness(path, date(2026, 9, 8)) == "UNKNOWN"


def test_daily_freshness_fails_closed_on_session_payload_mismatch(tmp_path):
    path = make_ui_db(tmp_path, session_status=MarketSessionStatus.HOLIDAY)
    _add_session(path, date(2026, 9, 11), MarketSessionStatus.WEEKEND,
                 payload_date=date(2026, 9, 5))
    assert load_validated_daily_observations(
        path, market_date=date(2026, 9, 12)) is None


def test_validated_daily_observations_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EGX_DB_PATH", str(tmp_path / "missing.db"))
    assert load_validated_daily_observations() is None
    assert not (tmp_path / "missing.db").exists()


def test_security_master_summary_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EGX_DB_PATH", str(tmp_path / "missing.db"))
    assert load_security_master_summary() is None
    assert not (tmp_path / "missing.db").exists()

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    monkeypatch.setenv("EGX_DB_PATH", str(empty))
    assert load_security_master_summary() is None


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
    assert state["security_master"] is None
    assert state["error"] is not None


def test_late_query_failure_discards_partial_symbols(tmp_path, monkeypatch):
    path = make_ui_db(tmp_path)
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE daily_canonical_sources")
    monkeypatch.setenv("EGX_DB_PATH", str(path))
    state = load_today_state()
    assert state == {
        "available": False, "symbols": [], "counts": {},
        "security_master": None, "error": "OperationalError",
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
        "available": False, "symbols": [], "counts": {},
        "security_master": None, "error": "ValueError",
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
        "security_master": None,
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
    assert "NO ADMITTED SETUP" not in page
    assert (
        "Current trading signals are unavailable"
        in page
    )


def test_validated_symbol_card_counts_distinct_symbols_not_artifacts():
    # The schema admits one VALIDATED artifact per source snapshot, so a symbol
    # with two snapshot artifacts must not be counted as two validated symbols.
    def artifact(snapshot, bars, quarantined):
        return {
            "canonical_symbol": "COMI",
            "provider": "eodhd",
            "provider_symbol": "COMI.EGX",
            "source_snapshot_date": snapshot,
            "oldest_market_date": "2026-09-01",
            "newest_market_date": snapshot,
            "valid_bar_count": bars,
            "quarantined_bar_count": quarantined,
            "status": "VALIDATED",
        }

    state = {
        "available": True,
        "integrity": "ok",
        "counts": {"daily_artifacts": 2},
        "symbols": [artifact("2026-09-08", 6, 0), artifact("2026-09-09", 7, 0)],
    }

    page = render_today_dashboard(state)
    cards = dict(re.findall(
        r'<div class="label">([^<]+)</div>\s*<div class="value">([^<]+)</div>', page,
    ))

    assert cards["Validated Symbols"] == "1"
    assert cards["Daily Artifacts"] == "2"


def test_inventory_counts_are_escaped():
    # SQLite INTEGER affinity keeps non-numeric text, which also satisfies the
    # table's >= 0 CHECKs; stored values must never reach the page as markup.
    state = {
        "available": True,
        "integrity": "ok",
        "counts": {},
        "symbols": [{
            "canonical_symbol": "COMI",
            "provider": "eodhd",
            "newest_market_date": "2026-09-09",
            "valid_bar_count": "<b>7</b>",
            "quarantined_bar_count": "<i>0</i>",
        }],
    }

    page = render_today_dashboard(state)

    assert "<b>7</b>" not in page and "&lt;b&gt;7&lt;/b&gt;" in page
    assert "<i>0</i>" not in page and "&lt;i&gt;0&lt;/i&gt;" in page
