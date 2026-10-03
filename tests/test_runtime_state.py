"""Central runtime-state resolution and snapshot-to-API integration (artificial data)."""
import json
import sqlite3

import pytest

from app import main, runtime_state
from app.runtime_state import INPUTS, resolve, runtime_state_report
from app.ui import system
from tools.runtime_state_snapshot import create_snapshot

VARIABLES = [variable for variable, _, _ in INPUTS.values()]


@pytest.fixture
def clean_env(monkeypatch):
    for key in VARIABLES + ['EGX_RUNTIME_STATE_DIR']:
        monkeypatch.delenv(key, raising=False)
    runtime_state._hash_cached.cache_clear()
    return monkeypatch


def test_legacy_resolution_is_unchanged(clean_env):
    assert resolve('database') == ('/app/data/platform.db', 'default')
    for name in ('receipts', 'heartbeat', 'scan_history', 'scan_ledger'):
        assert resolve(name) == (None, 'unset')
    clean_env.setenv('EGX_DB_PATH', '/x/platform.db')
    assert resolve('database') == ('/x/platform.db', 'explicit')
    report = runtime_state_report()
    assert report['mode'] == 'PER_VARIABLE' and report['snapshot'] is None
    assert report['warnings'] == []


def test_bundle_resolves_every_input_and_explicit_overrides_are_flagged(clean_env, tmp_path):
    clean_env.setenv('EGX_RUNTIME_STATE_DIR', str(tmp_path))
    assert resolve('database') == (str(tmp_path / 'platform.db'), 'bundle')
    assert resolve('receipts') == (str(tmp_path), 'bundle')
    assert resolve('scan_ledger') == (str(tmp_path / 'platform.db'), 'bundle')
    assert resolve('heartbeat') == (str(tmp_path / 'scheduler-heartbeat.json'), 'bundle')
    clean_env.setenv('EGX_SCHEDULER_HEARTBEAT_PATH', '/elsewhere/beat.json')
    report = runtime_state_report()
    assert report['inputs']['heartbeat']['origin'] == 'explicit'
    assert 'MIXED_STATE_SOURCES' in report['warnings']
    # No manifest in this directory: never reported VERIFIED.
    assert report['snapshot']['status'] == 'UNAVAILABLE'
    assert 'SNAPSHOT_UNVERIFIED' in report['warnings']


def test_split_explicit_databases_are_flagged(clean_env, tmp_path):
    clean_env.setenv('EGX_DB_PATH', str(tmp_path / 'a.db'))
    clean_env.setenv('EGX_PAPER_RUNTIME', str(tmp_path / 'other'))
    assert runtime_state_report()['warnings'] == ['MIXED_STATE_SOURCES']
    clean_env.setenv('EGX_PAPER_RUNTIME', str(tmp_path))
    clean_env.setenv('EGX_DB_PATH', str(tmp_path / 'platform.db'))
    assert runtime_state_report()['warnings'] == []


def test_startup_event_is_structured_and_path_free(clean_env, tmp_path):
    from app.runtime_state import startup_event
    clean_env.setenv('EGX_RUNTIME_STATE_DIR', str(tmp_path))
    clean_env.setenv('EGX_SCHEDULER_HEARTBEAT_PATH', '/secret-ish/location/beat.json')
    event = startup_event('b' * 40)
    assert event['event'] == 'api_startup' and event['live_money'] == 'DISABLED'
    assert event['runtime_state_mode'] == 'SNAPSHOT_BUNDLE'
    assert event['input_origins']['heartbeat'] == 'explicit'
    assert event['snapshot_status'] == 'UNAVAILABLE'
    assert 'MIXED_STATE_SOURCES' in event['warnings']
    text = json.dumps(event)
    assert str(tmp_path) not in text and '/secret-ish/' not in text


def test_relative_bundle_is_ignored_and_warned(clean_env):
    clean_env.setenv('EGX_RUNTIME_STATE_DIR', 'relative/bundle')
    assert resolve('database') == ('/app/data/platform.db', 'default')
    assert runtime_state_report()['warnings'] == ['RUNTIME_STATE_DIR_NOT_ABSOLUTE']


def _operational_db(path):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''
        CREATE TABLE canonical_instruments (instrument_id TEXT PRIMARY KEY,
            instrument_type TEXT NOT NULL, canonical_ticker TEXT NOT NULL,
            source_provider TEXT NOT NULL, source_market_date TEXT, updated_at TEXT NOT NULL);
        INSERT INTO canonical_instruments VALUES
            ('i1','EQUITY','COMI','egid',NULL,'2026-09-26T07:00:00+00:00'),
            ('i2','EQUITY','SWDY','egid',NULL,'2026-09-26T07:00:00+00:00'),
            ('i3','INDEX','EGX30','egid',NULL,'2026-09-26T07:00:00+00:00');
        CREATE TABLE daily_canonical_artifacts (canonical_symbol TEXT, provider TEXT,
            provider_symbol TEXT, source_snapshot_date TEXT, oldest_market_date TEXT,
            newest_market_date TEXT, valid_bar_count INTEGER, quarantined_bar_count INTEGER,
            status TEXT);
        INSERT INTO daily_canonical_artifacts VALUES
            ('COMI','tradingview_tvdatafeed','COMI','2026-09-24','2025-01-27',
             '2026-09-24',400,0,'VALIDATED');
        CREATE TABLE market_sessions (market_date TEXT PRIMARY KEY, status TEXT NOT NULL,
            payload_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE audit_events (event_id TEXT, event_type TEXT, entity_id TEXT,
            created_at TEXT, payload_json TEXT);
    ''')
    con.commit()
    return con


@pytest.mark.parametrize('tamper', [False, True])
def test_snapshot_bundle_to_api_readiness_end_to_end(clean_env, tmp_path, tamper):
    live = tmp_path / 'live'
    live.mkdir()
    writer = _operational_db(live / 'platform.db')
    bundle = tmp_path / 'bundle'
    manifest = create_snapshot(db=str(live / 'platform.db'), out=str(bundle),
                               build_revision='a' * 40)
    writer.close()
    assert manifest['missing'] == ['calendar_maintenance', 'context', 'heartbeat', 'macro', 'ranking', 'scan_history']
    if tamper:
        (bundle / 'platform.db').chmod(0o644)
        with sqlite3.connect(bundle / 'platform.db') as con:
            con.execute("DELETE FROM canonical_instruments WHERE canonical_ticker='SWDY'")
    clean_env.setenv('EGX_RUNTIME_STATE_DIR', str(bundle))
    before = (bundle / 'platform.db').read_bytes()

    product = main.product('ALL', 'TODAY')
    state = system.load_system_state()
    readiness = product['readiness']['EGX']

    # Identities, bars and receipts all come from the same bundle database.
    assert readiness['security_master']['instruments'] == (2 if tamper else 3)
    assert readiness['daily_observations']['artifacts'] == 1
    assert product['markets']['EGX']['configured'] is True
    assert product['markets']['EGX']['available'] is True
    assert product['markets']['EGX']['status'] == 'NOT_READY'  # no receipts exist
    assert readiness['operational_receipts'] == {
        'configured': True, 'available': True, 'status': 'NOT_READY'}
    assert readiness['source_admission'] == 'NO_ADMITTED_SOURCE'
    assert readiness['scheduler_heartbeat'] == 'UNKNOWN'
    assert readiness['scan_history'] == 'UNKNOWN'
    assert readiness['scan_readiness'] == 'EVIDENCE_BLOCKED'
    assert readiness['blockers'] == ['NO_ADMITTED_DAILY_SOURCE',
                                     'SCHEDULER_HEARTBEAT_UNAVAILABLE',
                                     'SCAN_HISTORY_UNAVAILABLE']
    # API and SYSTEM agree on readiness.
    assert state['readiness'] == readiness
    report = state['runtime_state']
    assert report['mode'] == 'SNAPSHOT_BUNDLE'
    assert {item['origin'] for item in report['inputs'].values()} == {'bundle'}
    snapshot = report['snapshot']
    assert snapshot['build_revision'] == 'a' * 40
    assert snapshot['missing'] == ['calendar_maintenance', 'context', 'heartbeat', 'macro', 'ranking', 'scan_history']
    if tamper:
        assert snapshot['status'] == 'INVALID'
        assert snapshot['mismatched'] == ['platform.db']
        assert 'SNAPSHOT_UNVERIFIED' in report['warnings']
    else:
        assert snapshot['status'] == 'VERIFIED' and report['warnings'] == []
        assert (bundle / 'platform.db').read_bytes() == before
    html = system.render_system(state)
    assert 'Runtime state inputs: SNAPSHOT_BUNDLE' in html
    assert ('Snapshot: INVALID' if tamper else 'Snapshot: VERIFIED') in html
    page = main.root('EGX', 'TODAY').body.decode()
    assert '<th scope="row">Scan readiness</th><td>EVIDENCE_BLOCKED</td>' in page
    json.dumps(product)
    json.dumps(state)
