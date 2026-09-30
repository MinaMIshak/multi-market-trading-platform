from datetime import date, datetime, timezone
from enum import Enum
import fcntl
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.data import official_calendar_maintenance as maintenance

INDICES = ("CASE30", "EGX70_EWI", "EGX100_EWI")


def make_db(tmp_path, newest="2026-09-24", indices=INDICES, snapshot="2026-09-24"):
    path = tmp_path / "platform.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE canonical_data_artifacts (provider, asset_type, status, "
                "symbol, newest_market_date, source_snapshot_date)")
    for table in (*maintenance.LIFECYCLE_TABLES, "canonical_artifact_sources",
                  "data_ingestions", "market_sessions"):
        con.execute(f"CREATE TABLE {table} (x)")
    for name in indices:
        con.execute("INSERT INTO canonical_data_artifacts VALUES "
                    "('egx_official_public','INDEX_BARS','VALIDATED',?,?,?)", (name, newest, snapshot))
    # A newer non-official artifact must not move the baseline.
    con.execute("INSERT INTO canonical_data_artifacts VALUES "
                "('other','INDEX_BARS','VALIDATED','CASE30','2026-12-31','2026-09-30')")
    con.commit()
    con.close()
    return path


class Provider:
    def __init__(self, counts=None, error=None):
        self.counts = counts or {}
        self.error = error
        self.calls = []

    def fetch_index_bars(self, *, index_name, start_date, end_date):
        self.calls.append((index_name, start_date, end_date))
        if self.error:
            raise self.error
        return SimpleNamespace(record_count=self.counts.get(index_name, 0))


class Status(Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


def runtimes(db_path, *, refresh_error=None, mutate_lifecycle=False):
    calls = {"refresh": [], "backfill": []}

    def run(**kwargs):
        calls["refresh"].append(kwargs)
        if refresh_error:
            raise refresh_error
        with sqlite3.connect(db_path) as con:
            con.execute("INSERT INTO data_ingestions VALUES (1)")
            if mutate_lifecycle:
                con.execute("INSERT INTO signals VALUES (1)")

    def refresh_factory(*, database, root):
        return SimpleNamespace(job=SimpleNamespace(index_names=INDICES, run=run))

    def backfill_factory(*, database, data_root):
        def run_range(start, end):
            calls["backfill"].append((start, end))
            return [SimpleNamespace(verification_status=Status.VERIFIED),
                    SimpleNamespace(verification_status=Status.UNKNOWN)]
        return SimpleNamespace(run_range=run_range)

    return calls, refresh_factory, backfill_factory


def cairo(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=maintenance.CAIRO).astimezone(timezone.utc)


def call(tmp_path, db, provider, now, **kw):
    calls, refresh, backfill = runtimes(db, **kw)
    result = maintenance.run(db_path=db, data_root=tmp_path, now=now, provider=provider,
                             refresh_runtime_factory=refresh, backfill_runtime_factory=backfill)
    return result, calls


@pytest.mark.parametrize("now,expected", [
    (cairo(2026, 9, 30, 10, 33), date(2026, 9, 29)),   # session open
    (cairo(2026, 9, 30, 15, 59), date(2026, 9, 29)),   # before completion cutoff
    (cairo(2026, 9, 30, 16, 0), date(2026, 9, 30)),
    (cairo(2026, 9, 30, 23, 59), date(2026, 9, 30)),
    (datetime(2026, 9, 30, 13, 30, tzinfo=timezone.utc), date(2026, 9, 30)),  # Cairo 16:30
    (datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc), date(2026, 9, 30)),  # Cairo 10-01 00:30
])
def test_last_completed_session_uses_cairo_time(now, expected):
    assert maintenance.last_completed_session_date(now) == expected


def test_open_session_is_never_fetched(tmp_path):
    db = make_db(tmp_path, newest="2026-09-29")
    provider = Provider({name: 1 for name in INDICES})
    result, calls = call(tmp_path, db, provider, cairo(2026, 9, 30, 11, 0))
    assert result["outcome"] == "UP_TO_DATE" and provider.calls == []
    assert calls["refresh"] == []
    assert calls["backfill"] == [(date(2026, 9, 15), date(2026, 9, 29))]


def test_new_sessions_admitted_with_actual_acquisition_date(tmp_path):
    db = make_db(tmp_path)
    provider = Provider({name: 3 for name in INDICES})
    result, calls = call(tmp_path, db, provider, cairo(2026, 9, 30, 18, 0))
    assert result["outcome"] == "ADMITTED_NEW_SESSIONS"
    assert result["fetch_range"] == ["2026-09-25", "2026-09-30"]
    (refresh,) = calls["refresh"]
    assert refresh["start_date"] == date(2026, 9, 25)
    assert refresh["end_date"] == date(2026, 9, 30)
    assert refresh["snapshot_date"] == date(2026, 9, 30)
    assert result["snapshot_date"] == "2026-09-30"
    assert result["evidence_counts"]["data_ingestions"] == [0, 1]
    assert result["integrity_after"] == "ok"
    assert result["verified_sessions_in_window"] == 1


def test_snapshot_date_follows_cairo_date_not_utc(tmp_path):
    db = make_db(tmp_path, newest="2026-09-28")
    now = datetime(2026, 9, 29, 21, 30, tzinfo=timezone.utc)  # 2026-09-30 00:30 Cairo
    result, calls = call(tmp_path, db, Provider({n: 1 for n in INDICES}), now)
    assert calls["refresh"][0]["snapshot_date"] == date(2026, 9, 30)
    assert calls["refresh"][0]["end_date"] == date(2026, 9, 29)


def test_no_bars_in_range_is_clean_noop(tmp_path):
    db = make_db(tmp_path)
    result, calls = call(tmp_path, db, Provider({}), cairo(2026, 9, 26, 18, 0))
    assert result["outcome"] == "NO_NEW_SESSIONS" and calls["refresh"] == []
    assert calls["backfill"]


def test_disagreeing_indices_fail_closed_without_writes(tmp_path):
    db = make_db(tmp_path)
    provider = Provider({"CASE30": 3, "EGX70_EWI": 3, "EGX100_EWI": 2})
    with pytest.raises(maintenance.MaintenanceError, match="INDEX_EVIDENCE_DISAGREES"):
        call(tmp_path, db, provider, cairo(2026, 9, 30, 18, 0))
    assert maintenance.inspect_database(db)["counts"]["data_ingestions"] == 0


def test_provider_failure_fails_closed_without_writes(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(maintenance.MaintenanceError, match="PROBE_FAILED:CASE30:URLError"):
        call(tmp_path, db, Provider(error=type("URLError", (OSError,), {})()),
             cairo(2026, 9, 30, 18, 0))
    assert maintenance.inspect_database(db)["counts"]["data_ingestions"] == 0


def test_refresh_failure_is_reported(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(maintenance.MaintenanceError, match="REFRESH_FAILED:ValueError"):
        call(tmp_path, db, Provider({n: 3 for n in INDICES}), cairo(2026, 9, 30, 18, 0),
             refresh_error=ValueError("invalid bar"))


def test_same_day_snapshot_date_already_used_defers_without_fetch(tmp_path):
    # Observed 2026-09-30: a morning catch-up used snapshot 09-30 for 09-27..29;
    # the evening run must not collide with that immutable artifact identity.
    db = make_db(tmp_path, newest="2026-09-29", snapshot="2026-09-30")
    provider = Provider({name: 1 for name in INDICES})
    result, calls = call(tmp_path, db, provider, cairo(2026, 9, 30, 18, 17))
    assert result["outcome"] == "DEFERRED_SNAPSHOT_DATE_ALREADY_USED"
    assert result["fetch_range"] == ["2026-09-30", "2026-09-30"]
    assert provider.calls == [] and calls["refresh"] == []
    assert calls["backfill"] == [(date(2026, 9, 16), date(2026, 9, 30))]
    # Next day the snapshot date is free and the deferred session is admitted.
    result, calls = call(tmp_path, db, Provider({n: 2 for n in INDICES}), cairo(2026, 10, 1, 18, 17))
    assert result["outcome"] == "ADMITTED_NEW_SESSIONS"
    assert calls["refresh"][0]["start_date"] == date(2026, 9, 30)
    assert calls["refresh"][0]["snapshot_date"] == date(2026, 10, 1)


def test_refresh_failure_records_secret_free_cause(tmp_path):
    db = make_db(tmp_path)
    class JobError(RuntimeError):
        pass
    def failing():
        try:
            raise ValueError("existing canonical artifact conflicts on canonical_path")
        except ValueError as cause:
            raise JobError("official index refresh failed:CASE30:ValueError") from cause
    try:
        failing()
    except JobError as exc:
        error_value = exc
    with pytest.raises(maintenance.MaintenanceError) as caught:
        call(tmp_path, db, Provider({n: 3 for n in INDICES}), cairo(2026, 9, 30, 18, 0),
             refresh_error=error_value)
    assert caught.value.code == ("REFRESH_FAILED:JobError:official index refresh failed:CASE30:"
                                 "ValueError:ValueError:existing canonical artifact conflicts on canonical_path")


def test_missing_baseline_index_fails_closed(tmp_path):
    db = make_db(tmp_path, indices=("CASE30", "EGX70_EWI"))
    provider = Provider({n: 3 for n in INDICES})
    with pytest.raises(maintenance.MaintenanceError, match="NO_ADMITTED_OFFICIAL_INDEX_BASELINE"):
        call(tmp_path, db, provider, cairo(2026, 9, 30, 18, 0))
    assert provider.calls == []


def test_lifecycle_change_is_detected(tmp_path):
    db = make_db(tmp_path)
    with pytest.raises(maintenance.MaintenanceError, match="LIFECYCLE_TABLES_CHANGED:signals"):
        call(tmp_path, db, Provider({n: 3 for n in INDICES}), cairo(2026, 9, 30, 18, 0),
             mutate_lifecycle=True)


def test_missing_database_fails(tmp_path):
    with pytest.raises(maintenance.MaintenanceError, match="DATABASE_MISSING"):
        call(tmp_path, tmp_path / "absent.db", Provider(), cairo(2026, 9, 30, 18, 0))


def test_main_writes_log_and_status_and_honours_lock(tmp_path, monkeypatch, capsys):
    db = make_db(tmp_path, newest="2026-09-29")
    state = tmp_path / "state"
    outcomes = iter([{"outcome": "UP_TO_DATE"}, maintenance.MaintenanceError("PROBE_FAILED:CASE30:OSError")])

    def fake_run(**kwargs):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(maintenance, "run", fake_run)
    argv = ["--db-path", str(db), "--data-root", str(tmp_path), "--state-dir", str(state)]
    assert maintenance.main(argv) == maintenance.EXIT_OK
    assert json.loads((state / "last-success.json").read_text())["status"] == "SUCCESS"
    assert maintenance.main(argv) == maintenance.EXIT_FAILED
    last = json.loads((state / "last-run.json").read_text())
    assert last["status"] == "FAILED" and last["error"] == "PROBE_FAILED:CASE30:OSError"
    assert last["live_money"] is False
    assert json.loads((state / "last-success.json").read_text())["status"] == "SUCCESS"
    with (state / "egx-calendar-maintenance.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert maintenance.main(argv) == maintenance.EXIT_LOCKED
    lines = list((state / "logs").glob("*.jsonl"))[0].read_text().splitlines()
    assert [json.loads(line)["status"] for line in lines] == ["SUCCESS", "FAILED", "LOCKED"]
    assert json.loads((state / "last-run.json").read_text())["status"] == "FAILED"


def test_main_rejects_relative_paths(tmp_path):
    with pytest.raises(SystemExit):
        maintenance.main(["--db-path", "platform.db", "--data-root", str(tmp_path),
                          "--state-dir", str(tmp_path)])
