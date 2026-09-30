from datetime import datetime, timedelta, timezone
import json

import pytest

from app.calendar_maintenance_status import load_calendar_maintenance_status
from app.runtime_state import _verify_bundle, resolve
from app.ui.system import render_calendar_maintenance

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)


def record(**overrides):
    value = {"job": "egx_official_calendar_maintenance", "status": "SUCCESS",
             "outcome": "ADMITTED_NEW_SESSIONS", "finished_at": (NOW - timedelta(hours=1)).isoformat(),
             "last_completed_session_date": "2026-09-30", "verified_sessions_in_window": 11,
             "live_money": False, "build_revision": "abc"}
    value.update(overrides)
    return value


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "last-run.json"
    monkeypatch.setenv("EGX_CALENDAR_MAINTENANCE_STATUS_PATH", str(path))
    monkeypatch.delenv("EGX_RUNTIME_STATE_DIR", raising=False)
    return path


def test_recent_success_is_current(status_file):
    status_file.write_text(json.dumps(record()))
    result = load_calendar_maintenance_status(now=NOW)
    assert (result["status"], result["freshness"], result["outcome"]) == (
        "SUCCESS", "CURRENT", "ADMITTED_NEW_SESSIONS")


def test_old_success_is_stale_and_failure_is_failed(status_file):
    status_file.write_text(json.dumps(record(finished_at=(NOW - timedelta(hours=31)).isoformat())))
    assert load_calendar_maintenance_status(now=NOW)["freshness"] == "STALE"
    status_file.write_text(json.dumps(record(status="FAILED", error="PROBE_FAILED:CASE30:URLError")))
    result = load_calendar_maintenance_status(now=NOW)
    assert (result["freshness"], result["error"]) == ("FAILED", "PROBE_FAILED:CASE30:URLError")


@pytest.mark.parametrize("content", [
    "not json", json.dumps([1]), json.dumps(record(job="other")),
    json.dumps(record(status="GREAT")), json.dumps(record(live_money=True)),
    json.dumps(record(finished_at="2026-09-30T10:00:00")),
    json.dumps(record(finished_at=(NOW + timedelta(hours=1)).isoformat())),
    "x" * (70 * 1024),
])
def test_malformed_or_implausible_records_are_unknown(status_file, content):
    status_file.write_text(content)
    assert load_calendar_maintenance_status(now=NOW)["status"] == "UNKNOWN"


def test_unconfigured_is_unknown(monkeypatch):
    monkeypatch.delenv("EGX_CALENDAR_MAINTENANCE_STATUS_PATH", raising=False)
    monkeypatch.delenv("EGX_RUNTIME_STATE_DIR", raising=False)
    assert load_calendar_maintenance_status(now=NOW)["status"] == "UNKNOWN"


def test_snapshot_bundle_carries_and_verifies_the_record(tmp_path, monkeypatch):
    from tools.runtime_state_snapshot import create_snapshot
    import sqlite3
    db = tmp_path / "platform.db"
    sqlite3.connect(db).execute("create table t (x)").connection.close()
    source = tmp_path / "last-run.json"
    source.write_text(json.dumps(record()))
    out = tmp_path / "bundle"
    manifest = create_snapshot(db=str(db), out=str(out), calendar_maintenance=str(source), now=NOW)
    assert manifest["files"]["calendar_maintenance"]["name"] == "calendar-maintenance-last-run.json"
    assert "calendar_maintenance" not in manifest["missing"]
    monkeypatch.delenv("EGX_CALENDAR_MAINTENANCE_STATUS_PATH", raising=False)
    monkeypatch.setenv("EGX_RUNTIME_STATE_DIR", str(out))
    assert resolve("calendar_maintenance") == (str(out / "calendar-maintenance-last-run.json"), "bundle")
    assert load_calendar_maintenance_status(now=NOW)["status"] == "SUCCESS"
    verified = _verify_bundle(out)
    assert verified["status"] == "VERIFIED"
    assert "calendar-maintenance-last-run.json" in verified["included"]


def test_render_escapes_and_labels_scope():
    html = render_calendar_maintenance({"status": "FAILED", "freshness": "FAILED",
                                        "error": "<b>x</b>", "outcome": None})
    assert "&lt;b&gt;" in html and "<b>x</b>" not in html
    assert "not a scan, a candidate or source admission" in html
    assert "<td>UNKNOWN</td>" in html
