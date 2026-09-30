import hashlib
import json

import pytest

from app import egx_universe_scan
from app.egx_scan_history import load_scan_history


def make_db(tmp_path, tickers=("COMI", "EAST", "FWRY")):
    from app.storage.database import Database
    db_path = tmp_path / "platform.db"
    database = Database(str(db_path))
    database.initialize()
    with database.connect() as con:
        for index, ticker in enumerate(tickers):
            con.execute(
                "INSERT INTO canonical_instruments (instrument_id, instrument_type, canonical_ticker, "
                "name_en, source_provider, source_symbol_code, source_sha256, normalization_notes_json, updated_at) "
                "VALUES (?, 'EQUITY', ?, ?, 'egid', ?, 'x', '[]', '2026-09-26T00:00:00+00:00')",
                (f"id-{index}", ticker, ticker, f"code-{index}"))
        con.execute(
            "INSERT INTO canonical_instruments (instrument_id, instrument_type, canonical_ticker, "
            "name_en, source_provider, source_symbol_code, source_sha256, normalization_notes_json, updated_at) "
            "VALUES ('idx', 'INDEX', 'EGX30', 'EGX30', 'egid', 'idx', 'x', '[]', '2026-09-26T00:00:00+00:00')")
    return db_path


def digest(path):
    """Logical content of every table; file bytes change on WAL checkpoint alone."""
    import sqlite3
    with sqlite3.connect(path) as con:
        return hashlib.sha256("\n".join(con.iterdump()).encode()).hexdigest()


def test_universe_scan_classifies_every_equity_as_blocked_without_db_writes(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    before = digest(db)
    history = tmp_path / "egx-scan-history.json"
    report = egx_universe_scan.run(db_path=db, history_path=history)
    assert report["requested"] == 3 and report["scanned"] == 0
    assert report["status_counts"]["EVIDENCE_BLOCKED"] == 3
    assert report["scope_kind"] == "EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE"
    assert report["scope_reference"] == "security-master-equity-universe"
    assert {row["symbol"] for row in report["symbols"]} == {"COMI", "EAST", "FWRY"}
    assert all(not row["scanned"] for row in report["symbols"])
    assert digest(db) == before
    saved = json.loads(history.read_text())
    assert saved["requested"] == 3 and saved["live_money"] is False
    monkeypatch.setenv("EGX_SCAN_HISTORY_PATH", str(history))
    loaded = load_scan_history()
    assert loaded["status"] == "HISTORICAL_RUN" and loaded["run"]["scanned"] == 0


def test_cli_reports_and_rejects_relative_or_missing_paths(tmp_path, capsys):
    db = make_db(tmp_path)
    history = tmp_path / "h.json"
    assert egx_universe_scan.main(["--db-path", str(db), "--history-path", str(history)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "CLASSIFIED" and out["scanned"] == 0 and out["live_money"] is False
    assert egx_universe_scan.main(["--db-path", "platform.db", "--history-path", str(history)]) == 1
    assert egx_universe_scan.main(["--db-path", str(tmp_path / "none.db"),
                                   "--history-path", str(history)]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["error"] == "ValueError"
