"""Offline snapshot contracts on artificial databases; no runtime evidence."""
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

import pytest

from app.scheduler_heartbeat import load_heartbeat
from app.ui.operational import load_operational_state
from app.ui.today import load_security_master_summary
from app.runtime_state import INPUTS
from tools.runtime_state_snapshot import (
    SnapshotError, create_snapshot, main, runtime_environment,
)

INPUT_VARIABLES = [variable for variable, _, _ in INPUTS.values()]


def make_db(path):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE canonical_instruments (instrument_id TEXT PRIMARY KEY,'
                ' instrument_type TEXT, canonical_ticker TEXT, source_provider TEXT,'
                ' updated_at TEXT, source_market_date TEXT)')
    con.execute("INSERT INTO canonical_instruments VALUES ('i1','EQUITY','COMI','egid',"
                "'2026-09-26T07:00:00+00:00',NULL)")
    con.execute('CREATE TABLE daily_canonical_artifacts (canonical_symbol TEXT)')
    con.commit()
    return con


def test_snapshot_is_consistent_read_only_and_excludes_uncommitted(tmp_path):
    source = tmp_path / 'live.db'
    writer = make_db(source)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("INSERT INTO canonical_instruments VALUES ('i2','EQUITY','SWDY','egid',"
                   "'2026-09-26T07:00:00+00:00',NULL)")
    out = tmp_path / 'snap'
    manifest = create_snapshot(db=str(source), out=str(out))
    writer.commit()  # the live writer was never blocked or modified
    writer.close()
    assert manifest['database']['integrity_check'] == 'ok'
    assert manifest['database']['table_counts']['canonical_instruments'] == 1
    assert manifest['database']['table_counts']['audit_events'] is None
    assert manifest['database']['paper_signal_receipts'] is None
    assert manifest['files'] == {'heartbeat': None, 'scan_history': None, 'calendar_maintenance': None, 'ranking': None, 'macro': None, 'context': None, 'us_ranking': None, 'egx_experiment': None, 'learning': None}
    assert sorted(p.name for p in out.iterdir()) == ['SNAPSHOT_MANIFEST.json', 'platform.db']
    assert not os.access(out / 'platform.db', os.W_OK) or os.geteuid() == 0
    assert oct((out / 'platform.db').stat().st_mode & 0o777) == '0o444'
    assert load_security_master_summary(out / 'platform.db')['total_instruments'] == 1
    assert json.loads((out / 'SNAPSHOT_MANIFEST.json').read_text()) == manifest
    assert not [p for p in tmp_path.iterdir() if '.partial-' in p.name]


def test_snapshot_never_overwrites_and_rejects_bad_inputs(tmp_path):
    source = tmp_path / 'live.db'
    make_db(source).close()
    out = tmp_path / 'snap'
    out.mkdir()
    with pytest.raises(SnapshotError, match='never overwritten'):
        create_snapshot(db=str(source), out=str(out))
    with pytest.raises(SnapshotError, match='absolute'):
        create_snapshot(db='live.db', out=str(tmp_path / 'x'))
    link = tmp_path / 'link.db'
    link.symlink_to(source)
    with pytest.raises(SnapshotError, match='regular file'):
        create_snapshot(db=str(link), out=str(tmp_path / 'y'))
    with pytest.raises(SnapshotError, match='regular file'):
        create_snapshot(db=str(tmp_path / 'missing.db'), out=str(tmp_path / 'z'))
    with pytest.raises(SnapshotError, match='parent'):
        create_snapshot(db=str(source), out=str(tmp_path / 'no' / 'dir'))


def test_failed_snapshot_leaves_nothing(tmp_path):
    source = tmp_path / 'live.db'
    make_db(source).close()
    big = tmp_path / 'history.json'
    big.write_bytes(b' ' * 1_048_577)
    with pytest.raises(SnapshotError, match='too large'):
        create_snapshot(db=str(source), out=str(tmp_path / 'snap'), scan_history=str(big))
    assert not (tmp_path / 'snap').exists()
    assert not [p for p in tmp_path.iterdir() if '.partial-' in p.name]


def test_snapshot_runtime_readers_keep_original_evidence_times(tmp_path, monkeypatch):
    source = tmp_path / 'live.db'
    make_db(source).close()
    beat = tmp_path / 'beat.json'
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    beat.write_text(json.dumps({'schema_version': 1, 'market': 'EGX', 'mode': 'observe',
                                'observed_at': old.isoformat(),
                                'valid_until': (old + timedelta(minutes=2)).isoformat()}))
    out = tmp_path / 'snap'
    manifest = create_snapshot(db=str(source), out=str(out), heartbeat=str(beat))
    env = runtime_environment(out, manifest)
    assert env == {'EGX_RUNTIME_STATE_DIR': str(out)}
    assert manifest['missing'] == ['calendar_maintenance', 'context', 'egx_experiment', 'learning', 'macro', 'ranking', 'scan_history', 'us_ranking']
    for key in INPUT_VARIABLES:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('EGX_RUNTIME_STATE_DIR', str(out))
    # Copied heartbeat is historical: reported STALE, never refreshed.
    assert load_heartbeat()['status'] == 'STALE'
    # Receipt reader reads the snapshot; no receipts means none are fabricated.
    state = load_operational_state()
    assert (state['configured'], state['available'], state['symbols'], state['status']) == (
        True, True, [], 'NOT_READY')


def test_cli_reports_failure_without_traceback(tmp_path, capsys):
    assert main(['--db', str(tmp_path / 'missing.db'), '--out', str(tmp_path / 's')]) == 1
    assert 'snapshot failed' in capsys.readouterr().err
    source = tmp_path / 'live.db'
    make_db(source).close()
    assert main(['--db', str(source), '--out', str(tmp_path / 's')]) == 0
    assert json.loads(capsys.readouterr().out)['environment'] == {
        'EGX_RUNTIME_STATE_DIR': str(tmp_path / 's')}
