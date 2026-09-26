"""Offline engineering fixtures, never real market evidence."""
from datetime import datetime, timedelta
import sqlite3

import pytest

from app import main
from app.paper.swing_launch import run_signal
from app.ui import operational
from tests.test_paper_shadow_launch import launch


@pytest.mark.parametrize('scenario', ['watch', 'no_signal', 'expired', 'tampered', 'other_symbol'])
def test_verified_runtime_dashboard(launch, monkeypatch, scenario):
    db, root, source, directory, _, _, at = launch
    if scenario == 'no_signal':
        from app.paper import swing_launch
        original = swing_launch.prepare_signal
        def prepare(*args):
            s, candidate, plan, data = original(*args)
            return s, candidate.model_copy(update={'state': 'NO_CONFIRMATION'}), plan, data
        monkeypatch.setattr(swing_launch, 'prepare_signal', prepare)
    result = run_signal(db, root, source, directory, publish=False)
    assert result['signal_status'] == ('READY_NO_SIGNAL' if scenario == 'no_signal' else 'WATCH')
    # Isolated runtime layout matches production contract without production access.
    runtime = db.path.parent
    with db.connect() as original, sqlite3.connect(runtime / 'platform.db') as copy:
        original.backup(copy)
    monkeypatch.setenv('EGX_PAPER_RUNTIME', str(runtime))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(days=5) if scenario == 'expired' else at
    monkeypatch.setattr(operational, 'datetime', Clock)
    if scenario in ('tampered', 'other_symbol'):
        with sqlite3.connect(runtime / 'platform.db') as con:
            if scenario == 'tampered':
                con.execute("UPDATE audit_events SET payload_json='{}' WHERE event_type='PAPER_SIGNAL_VERIFIED'")
            else:
                con.execute("UPDATE daily_canonical_artifacts SET canonical_symbol='OTHER'")
    before = (runtime / 'platform.db').read_bytes()
    state = main.paper_operational()
    body = main.root().body.decode()
    assert 'LIVE MONEY DISABLED' in body and 'Candidate != fill' in body
    assert main.today() == state
    assert before == (runtime / 'platform.db').read_bytes()
    if scenario == 'tampered':
        assert state['status'] == 'EVIDENCE_BLOCKED' and not state['symbols']
    elif scenario == 'other_symbol':
        assert state['symbols'][0]['symbol'] == 'OTHER'
        assert state['symbols'][0]['status'] == 'NOT_READY'
    else:
        expected = {'watch': 'WATCH', 'no_signal': 'READY_NO_SIGNAL', 'expired': 'DATA_STALE'}[scenario]
        assert state['symbols'][0]['status'] == expected
        assert ('Entry band:' in body) == (scenario == 'watch')
        assert 'fixture' in body


def test_missing_runtime_never_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv('EGX_PAPER_RUNTIME', str(tmp_path / 'absent'))
    monkeypatch.setenv('EGX_DB_PATH', str(tmp_path / 'legacy.db'))
    assert main.today()['status'] == 'EVIDENCE_BLOCKED'
    assert 'EVIDENCE_BLOCKED' in main.root().body.decode()
    assert not list(tmp_path.iterdir())
