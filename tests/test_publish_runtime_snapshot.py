from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.runtime_state import resolve, runtime_state_report
from tools import publish_runtime_snapshot as publisher

NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)


def make_db(path, instruments=1):
    con = sqlite3.connect(path)
    con.execute("create table canonical_instruments (x)")
    for index in range(instruments):
        con.execute("insert into canonical_instruments values (?)", (index,))
    con.commit()
    con.close()
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("EGX_RUNTIME_STATE_DIR", "EGX_DB_PATH", "EGX_PAPER_RUNTIME",
                 "EGX_SCHEDULER_HEARTBEAT_PATH", "EGX_SCAN_HISTORY_PATH", "EGX_SCAN_LEDGER_PATH",
                 "EGX_CALENDAR_MAINTENANCE_STATUS_PATH"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "snapshots"
    root.mkdir()
    pointer = tmp_path / "current-bundle.json"
    monkeypatch.setenv("EGX_RUNTIME_STATE_POINTER", str(pointer))
    return tmp_path, root, pointer


def test_publish_switches_pointer_and_runtime_follows_without_restart(env):
    tmp_path, root, pointer = env
    db = make_db(tmp_path / "platform.db")
    first = publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer),
                              build_revision="abc", now=NOW)
    assert json.loads(pointer.read_text())["bundle"] == first["bundle"]
    assert resolve("database") == (first["bundle"] + "/platform.db", "bundle")
    report = runtime_state_report()
    assert report["mode"] == "SNAPSHOT_POINTER" and report["warnings"] == []
    assert report["snapshot"]["status"] == "VERIFIED"
    assert report["pointer"]["status"] == "OK" and report["pointer"]["build_revision"] == "abc"
    second = publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer),
                               now=NOW + timedelta(hours=1))
    assert second["bundle"] != first["bundle"]
    assert resolve("database")[0] == second["bundle"] + "/platform.db"
    lines = (tmp_path / "current-bundle.json.history.jsonl").read_text().splitlines()
    assert [json.loads(line)["bundle"] for line in lines] == [first["bundle"], second["bundle"]]


def test_rollback_returns_to_previous_verified_bundle(env):
    tmp_path, root, pointer = env
    db = make_db(tmp_path / "platform.db")
    first = publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer), now=NOW)
    publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer),
                      now=NOW + timedelta(hours=1))
    back = publisher.rollback(pointer=str(pointer))
    assert back["bundle"] == first["bundle"] and back["action"] == "rollback"
    assert resolve("database")[0] == first["bundle"] + "/platform.db"


def test_rollback_without_earlier_bundle_fails(env):
    tmp_path, root, pointer = env
    db = make_db(tmp_path / "platform.db")
    publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer), now=NOW)
    with pytest.raises(publisher.PublishError, match="no earlier bundle"):
        publisher.rollback(pointer=str(pointer))


def test_empty_database_is_not_published_and_pointer_is_unchanged(env):
    tmp_path, root, pointer = env
    good = publisher.publish(db=str(make_db(tmp_path / "good.db")), snapshots_root=str(root),
                             pointer=str(pointer), now=NOW)
    with pytest.raises(publisher.PublishError, match="no canonical instruments"):
        publisher.publish(db=str(make_db(tmp_path / "empty.db", instruments=0)),
                          snapshots_root=str(root), pointer=str(pointer),
                          now=NOW + timedelta(hours=1))
    assert json.loads(pointer.read_text())["bundle"] == good["bundle"]


def test_tampered_snapshot_is_not_published(env, monkeypatch):
    tmp_path, root, pointer = env
    monkeypatch.setattr(publisher, "_verify_bundle",
                        lambda bundle: {"status": "INVALID", "missing": []})
    with pytest.raises(publisher.PublishError, match="not VERIFIED: INVALID"):
        publisher.publish(db=str(make_db(tmp_path / "platform.db")), snapshots_root=str(root),
                          pointer=str(pointer), now=NOW)
    assert not pointer.exists()


def test_missing_optional_inputs_are_listed_not_invented(env):
    tmp_path, root, pointer = env
    result = publisher.publish(db=str(make_db(tmp_path / "platform.db")),
                               snapshots_root=str(root), pointer=str(pointer),
                               heartbeat=str(tmp_path / "absent.json"), now=NOW)
    assert set(result["missing"]) == {"heartbeat", "scan_history", "calendar_maintenance", "ranking", "macro", "context"}


def test_unreadable_pointer_warns_and_serves_no_bundle(env):
    tmp_path, root, pointer = env
    pointer.write_text("not json")
    report = runtime_state_report()
    assert report["mode"] == "PER_VARIABLE"
    assert "RUNTIME_STATE_POINTER_UNREADABLE" in report["warnings"]
    assert report["pointer"]["status"] == "UNREADABLE"


def test_symlinked_pointer_is_rejected(env):
    tmp_path, root, pointer = env
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"bundle": str(tmp_path)}))
    pointer.symlink_to(real)
    assert runtime_state_report()["pointer"]["status"] == "UNREADABLE"


def test_explicit_bundle_wins_over_pointer_with_warning(env, monkeypatch):
    tmp_path, root, pointer = env
    db = make_db(tmp_path / "platform.db")
    published = publisher.publish(db=str(db), snapshots_root=str(root), pointer=str(pointer), now=NOW)
    other = tmp_path / "other-bundle"
    monkeypatch.setenv("EGX_RUNTIME_STATE_DIR", str(other))
    assert resolve("database")[0] == str(other / "platform.db")
    report = runtime_state_report()
    assert report["mode"] == "SNAPSHOT_BUNDLE"
    assert "BUNDLE_AND_POINTER_BOTH_SET" in report["warnings"]
    assert published["bundle"] != str(other)


def test_cli_publish_and_rollback_exit_codes(env, capsys):
    tmp_path, root, pointer = env
    db = make_db(tmp_path / "platform.db")
    assert publisher.main(["publish", "--db", str(db), "--snapshots-root", str(root),
                           "--pointer", str(pointer)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PUBLISHED"
    assert publisher.main(["rollback", "--pointer", str(pointer)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_PUBLISHED"
