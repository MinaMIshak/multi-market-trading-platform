"""Temporary-database schema-13 rehearsal contracts; not host or market evidence."""
from contextlib import closing
import json
import os
import sqlite3

import pytest

from app.storage.database import SCHEMA_VERSION
from tests.test_security_master_admission_history import refresh
from tests.test_security_master_refresh_runtime import build, egid_records
from tools.rehearse_schema_v13 import (
    COPY_NAME, REPORT_NAME, RehearsalError, main, rehearse,
)


def _source(tmp_path, *, downgrade=True, duplicate_universe=False, records=3):
    """Model the host contract: runtime state -> independent snapshot -> COPY.

    The runtime database (WAL, possibly still referenced by runtime
    connections) is never mutated here; it is only read through SQLite's
    backup API into a standalone snapshot owned by the test. Fixture
    mutations (schema-12 downgrade, duplicate dated universes) are applied
    to that snapshot only, and the snapshot is the rehearsal input.
    """
    database, runtime = build(tmp_path / 'src')
    refresh(runtime, records=egid_records(records))
    snapshot_dir = tmp_path / 'snapshot'
    snapshot_dir.mkdir(parents=True)
    snapshot = snapshot_dir / 'platform.db'
    with closing(sqlite3.connect(database.path.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(snapshot)) as dst:
            src.backup(dst)
    with closing(sqlite3.connect(snapshot)) as con:
        # Standalone file with a single owner: rollback journal, no WAL sidecars.
        con.execute('PRAGMA journal_mode=DELETE')
        if downgrade:
            con.execute('DROP TABLE security_master_current_members')
            con.execute('DROP INDEX idx_reference_universe_effective_date_unique')
            con.execute("UPDATE schema_meta SET value = '12' WHERE key = 'schema_version'")
        if duplicate_universe:
            for ingestion_id in ('u1', 'u2'):
                con.execute(
                    "INSERT INTO reference_artifacts (ingestion_id, contract, effective_date)"
                    " VALUES (?, 'egx-universe-v1', '2026-11-03')", (ingestion_id,))
        con.commit()
    assert not (snapshot_dir / 'platform.db-wal').exists()
    return snapshot


def _version(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
        return int(con.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0])


def test_v12_copy_upgrade_passes_and_never_touches_source(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    out = tmp_path / 'rehearsal'
    report = rehearse(db=source, out=out)
    assert report['status'] == 'PASS', report['failed_checks']
    assert report['failed_checks'] == []
    assert source.read_bytes() == before and _version(source) == 12
    assert report['source']['sha256_before'] == report['source']['sha256_after']
    assert report['pre_upgrade']['schema_version'] == 12
    assert report['post_upgrade']['schema_version'] == SCHEMA_VERSION == 13
    assert report['backfill_reconciliation'] == {
        'applies': True, 'expected': 3, 'retained': 3, 'missing': 0, 'unexpected': 0}
    assert report['post_upgrade']['foreign_key_violations'] == 0
    assert report['post_upgrade']['integrity_check'] == 'ok'
    assert report['retry']['status'] == 'COMPLETED'
    assert report['retry']['state']['current_members'] == 3
    assert report['target'] == 'COPY_ONLY' and report['live_money'] is False
    assert _version(out / COPY_NAME) == 13
    assert sorted(item.name for item in out.iterdir()) == sorted([COPY_NAME, REPORT_NAME])
    assert all(oct(item.stat().st_mode & 0o777) == '0o444' for item in out.iterdir())
    assert json.loads((out / REPORT_NAME).read_text()) == report


def test_duplicate_dated_universes_fail_closed_on_copy(tmp_path):
    source = _source(tmp_path, duplicate_universe=True)
    report = rehearse(db=source, out=tmp_path / 'rehearsal')
    assert report['status'] == 'FAIL'
    assert report['upgrade']['status'] == 'FAILED'
    assert 'no_duplicate_dated_universes' in report['failed_checks']
    assert 'upgrade_completed' in report['failed_checks']
    assert report['pre_upgrade']['duplicate_universe_dates'] == [
        {'effective_date': '2026-11-03', 'count': 2}]
    assert _version(source) == 12


def test_current_schema_copy_reports_backfill_not_applicable(tmp_path):
    source = _source(tmp_path, downgrade=False)
    report = rehearse(db=source, out=tmp_path / 'rehearsal')
    assert report['status'] == 'PASS', report['failed_checks']
    assert report['backfill_reconciliation']['applies'] is False
    assert report['backfill_reconciliation']['retained'] == 3


def test_refusals_leave_no_partial_output(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(RehearsalError, match='absolute'):
        rehearse(db=os.path.relpath(source), out=tmp_path / 'r')
    with pytest.raises(RehearsalError, match='absolute'):
        rehearse(db=source, out='relative')
    link = tmp_path / 'link.db'
    link.symlink_to(source)
    with pytest.raises(RehearsalError, match='regular file'):
        rehearse(db=link, out=tmp_path / 'r')
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(RehearsalError, match='never overwritten'):
        rehearse(db=source, out=existing)
    assert not [item for item in tmp_path.iterdir() if '.partial-' in item.name]


def test_main_exit_codes(tmp_path, capsys):
    source = _source(tmp_path)
    assert main(['--db', str(source), '--out', str(tmp_path / 'ok')]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'
    assert main(['--db', str(source), '--out', str(tmp_path / 'ok')]) == 1
    bad = _source(tmp_path / 'dup', duplicate_universe=True)
    assert main(['--db', str(bad), '--out', str(tmp_path / 'bad')]) == 2
