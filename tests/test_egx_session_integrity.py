"""Phase 1 regression tests: EGX session alignment between primary bars and the official secondary source."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import egx_universe_scan
from app.data.daily_cross_check import (DISCREPANCY, MATCH, STALE_SECONDARY, UNVERIFIED, CrossCheckedProvider,
                                        load_session_evidence)
from app.data.official_calendar_maintenance import completion_cutoff_date, resolve_completed_session
from app.data.provider import ProviderResponse
from app.data.providers.egx_market_watch import row_observation
from tests.egx_capture_fixture import official_row, write_capture

CAIRO = ZoneInfo("Africa/Cairo")
SUNDAY, THURSDAY = date(2026, 10, 4), date(2026, 10, 1)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CAIRO)


def observe(row, day=SUNDAY, captured=None, closed=True):
    return row_observation(row, status_date=day, captured_at=captured or at(day, 16, 45), market_closed=closed)


# --- official row semantics ------------------------------------------------------------------------------------------

def test_valid_sunday_session_row_is_aligned_although_last_trade_date_is_thursday():
    result = observe(official_row("EG1", SUNDAY))
    assert result["status"] == "SESSION_ALIGNED" and result["observation_session_date"] == "2026-10-04"
    assert result["provider_last_trade_date"] == "2026-10-01"      # writeTime newer than lastTradeDate: normal
    assert result["provider_last_trade_date_meaning"].startswith("date of prevClose")
    assert result["capture_timestamp"].startswith("2026-10-04T16:45") and result["market_status_date"] == "2026-10-04"


def test_current_metadata_never_makes_an_old_write_current():
    stale = observe(official_row("EG1", SUNDAY, write_day=THURSDAY, previous_close_day=date(2026, 9, 30)))
    assert stale["status"] == "SECONDARY_STALE" and stale["observation_session_date"] is None


@pytest.mark.parametrize("captured,closed", [(at(SUNDAY, 15, 0), True), (at(SUNDAY, 16, 45), False),
                                             (at(date(2026, 10, 5), 9, 0), True)])
def test_open_market_early_capture_or_next_day_capture_is_ambiguous(captured, closed):
    assert observe(official_row("EG1", SUNDAY), captured=captured, closed=closed)["status"] == (
        "UNVERIFIED_AMBIGUOUS_SESSION")


def test_no_trades_and_inconsistent_previous_close_are_explicit():
    assert observe(official_row("EG1", SUNDAY, trades=0, volume=0))["status"] == "NOT_TRADED_IN_SESSION"
    assert observe(official_row("EG1", SUNDAY, previous_close_day=SUNDAY))["status"] == "UNVERIFIED_AMBIGUOUS_SESSION"


# --- cross-check: same-session only, stale secondary never poisons primary --------------------------------------------

class Primary:
    name = "primary"

    def __init__(self, rows):
        self.rows = rows

    def fetch_daily_bars(self, *, symbol, start_date, end_date):
        return ProviderResponse(payload=json.dumps(self.rows).encode(), filename="x.json", source_uri="u",
                                record_count=len(self.rows), metadata={})


def primary_bar(day, close="10.5"):
    return [{"date": day.isoformat(), "open": "10.0", "high": "11.0", "low": "9.0", "close": close, "volume": "1000"}]


def checked(tmp_path, evidence, rows):
    return CrossCheckedProvider(provider=Primary(rows), session=evidence.session, official_by_isin=evidence.rows or None,
                                isin_by_symbol={"AAA": "EG1"}, quarantine_dir=tmp_path / "q",
                                not_aligned_by_isin=evidence.not_aligned)


def test_stale_secondary_is_unavailable_and_never_compared_or_quarantined(tmp_path):
    # Official rows written Thursday but filed in Sunday's folder: not Sunday observations.
    write_capture(tmp_path / "ev", SUNDAY, [official_row("EG1", SUNDAY, write_day=THURSDAY, previous_close_day=date(2026, 9, 30),
                                                         closePrice=50.0, lastPrice=50.0)])
    evidence = load_session_evidence(tmp_path / "ev", SUNDAY)
    assert evidence.status == STALE_SECONDARY and evidence.rows == {}
    wrapper = checked(tmp_path, evidence, primary_bar(SUNDAY))
    response = wrapper.fetch_daily_bars(symbol="AAA", start_date=SUNDAY, end_date=SUNDAY)   # primary not rejected
    assert json.loads(response.payload)[0]["date"] == "2026-10-04"
    assert wrapper.results["AAA"]["verdict"] == STALE_SECONDARY and "differences" not in wrapper.results["AAA"]
    assert not (tmp_path / "q").exists()
    summary = evidence.summary()
    assert summary["secondary_status"] == STALE_SECONDARY and summary["not_aligned"] == {"SECONDARY_STALE": 1}
    assert summary["capture"]["source_write_dates"] == {"20261001": 1}


def test_same_session_rows_are_compared_and_discrepancies_still_quarantine(tmp_path):
    write_capture(tmp_path / "ev", SUNDAY, [official_row("EG1", SUNDAY, closePrice=10.5, lastPrice=10.5, volume=None)])
    evidence = load_session_evidence(tmp_path / "ev", SUNDAY)
    assert evidence.status == "AVAILABLE" and set(evidence.rows) == {"EG1"}
    wrapper = checked(tmp_path, evidence, primary_bar(SUNDAY))
    wrapper.fetch_daily_bars(symbol="AAA", start_date=SUNDAY, end_date=SUNDAY)
    assert wrapper.results["AAA"]["verdict"] == MATCH
    bad = checked(tmp_path, evidence, primary_bar(SUNDAY, close="13.0"))
    with pytest.raises(Exception):
        bad.fetch_daily_bars(symbol="AAA", start_date=SUNDAY, end_date=SUNDAY)
    assert bad.results["AAA"]["verdict"] == DISCREPANCY


def test_stale_primary_is_not_verified_by_a_current_secondary(tmp_path):
    write_capture(tmp_path / "ev", SUNDAY, [official_row("EG1", SUNDAY)])
    evidence = load_session_evidence(tmp_path / "ev", SUNDAY)
    wrapper = checked(tmp_path, evidence, primary_bar(THURSDAY))     # primary has no Sunday bar
    wrapper.fetch_daily_bars(symbol="AAA", start_date=THURSDAY, end_date=SUNDAY)
    assert wrapper.results["AAA"]["verdict"] == UNVERIFIED


def test_target_session_freshness_uses_the_capture_for_that_session_only(tmp_path):
    write_capture(tmp_path / "ev", SUNDAY, [official_row("EG1", SUNDAY)])
    assert load_session_evidence(tmp_path / "ev", THURSDAY).status == "UNAVAILABLE_NO_SECONDARY"
    assert load_session_evidence(None, SUNDAY).status == "UNAVAILABLE_NO_SECONDARY"
    # Capture filed under Monday but the official status still says Sunday: not a Monday observation.
    monday = date(2026, 10, 5)
    write_capture(tmp_path / "ev2", monday, [official_row("EG1", SUNDAY)], status_day=SUNDAY,
                  captured_at=at(monday, 16, 45))
    assert load_session_evidence(tmp_path / "ev2", monday).status == STALE_SECONDARY


# --- calendar: completed-session resolution ----------------------------------------------------------------------------

def calendar(tmp_path, rows):
    path = tmp_path / "cal.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE market_sessions (market_date TEXT, status TEXT)")
        con.executemany("INSERT INTO market_sessions VALUES (?, ?)", rows)
    return Path(path)


@pytest.mark.parametrize("now,expected,basis", [
    (at(SUNDAY, 8, 30), "2026-10-01", "VERIFIED_SESSION"),                 # cutoff Saturday -> Thursday
    (at(date(2026, 10, 2), 18, 0), "2026-10-01", "VERIFIED_SESSION"),       # Friday weekend
    (at(date(2026, 10, 3), 18, 0), "2026-10-01", "VERIFIED_SESSION"),       # Saturday weekend
    (at(SUNDAY, 17, 0), "2026-10-04", "EXPECTED_SESSION_UNVERIFIED"),       # Sunday traded, not yet verified
])
def test_completed_session_never_resolves_to_a_weekend(tmp_path, now, expected, basis):
    db = calendar(tmp_path, [("2026-09-30", "VERIFIED"), ("2026-10-01", "VERIFIED"), ("2026-10-02", "WEEKEND"),
                             ("2026-10-03", "WEEKEND")])
    result = resolve_completed_session(db, now)
    assert (result["last_completed_session"], result["basis"]) == (expected, basis)
    assert date.fromisoformat(result["last_completed_session"]).weekday() not in (4, 5)
    assert completion_cutoff_date(at(SUNDAY, 8, 30)) == date(2026, 10, 3)   # a bound, not a session


def test_holidays_are_skipped_and_missing_calendar_is_unknown(tmp_path):
    db = calendar(tmp_path, [("2026-09-30", "VERIFIED"), ("2026-10-01", "HOLIDAY")])
    result = resolve_completed_session(db, at(date(2026, 10, 2), 18, 0))
    assert (result["last_completed_session"], result["basis"]) == ("2026-09-30", "VERIFIED_SESSION")
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert resolve_completed_session(Path(empty), at(SUNDAY, 8, 30))["basis"] == "UNKNOWN"


# --- universe scan vs ranking scope ----------------------------------------------------------------------------------------

def test_universe_scan_reports_scope_semantics_and_reconciliation(tmp_path, monkeypatch):
    class Repo:
        def __init__(self, **kwargs):
            pass

        def latest_by_symbol(self, provider):
            return {"AAA": {}, "BBB": {}, "ZZZ": {}}
    monkeypatch.setattr("app.data.validated_daily_repository.ValidatedDailyArtifactRepository", Repo)
    result = egx_universe_scan.reconciliation(tmp_path / "db", ["AAA", "BBB", "CCC", "DDD"])
    assert result == {"status": "AVAILABLE", "security_master_equities": 4, "with_admitted_primary_series": 2,
                      "without_admitted_primary_series": 2, "primary_series_outside_security_master": 1}
    assert egx_universe_scan.SCOPE_SEMANTICS["EVIDENCE_BLOCKED"].startswith("LAUNCH_EVIDENCE_NOT_ATTACHED")


def test_zero_volume_without_trades_field_counts_as_not_traded():
    assert observe(official_row("EG1", SUNDAY, volume=0, trades=None))["status"] == "NOT_TRADED_IN_SESSION"
