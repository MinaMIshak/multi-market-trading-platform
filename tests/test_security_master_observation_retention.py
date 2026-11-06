"""Temporary databases only; no production observation or availability claims."""
import json
import sqlite3
from hashlib import sha256
from datetime import datetime, timezone

import pytest

from app.storage.security_master_repository import SecurityMasterRepository
from app.scanner_v2_identity import bind_observed_egid_identities
from tests.test_scanner_v2_observed_identity import setup


def retained(tmp_path):
    repo, payload, args = setup(tmp_path)
    keys = dict(provider='egid', snapshot_date=args['session'],
                artifact_sha256=args['manifest'].sha256)
    return repo, payload, args, keys


def test_restart_retry_replacement_and_binding(tmp_path):
    repo, payload, args, keys = retained(tmp_path)
    first = repo.retain_admission_observation(**keys)
    repo.replace_provider_snapshot(provider='egid', instruments=[])
    repo = SecurityMasterRepository(repo.database)
    assert repo.retain_admission_observation(**keys) == first
    assert repo.get_admission_observation(**keys) == first
    args.update(observation=first, observed_at=datetime.now(timezone.utc))
    assert bind_observed_egid_identities(payload, **args).live_money is False
    with repo.database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM security_master_observations').fetchone()[0] == 1


def test_missing_never_backfills_and_exact_lookup(tmp_path):
    repo, _, _, keys = retained(tmp_path)
    with pytest.raises(KeyError):
        repo.get_admission_observation(**keys)
    repo.retain_admission_observation(**keys)
    with pytest.raises(KeyError):
        repo.get_admission_observation(**dict(keys, artifact_sha256='0'*64))


@pytest.mark.parametrize('sql', [
    "UPDATE security_master_observations SET envelope_json = '{}'",
    'DELETE FROM security_master_observations',
    'INSERT OR REPLACE INTO security_master_observations SELECT * FROM security_master_observations',
])
def test_immutable_envelope(tmp_path, sql):
    repo, _, _, keys = retained(tmp_path)
    first = repo.retain_admission_observation(**keys)
    with repo.database.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    assert repo.get_admission_observation(**keys) == first


@pytest.mark.parametrize('change', ['digest', 'receipt', 'future', 'backdate', 'live', 'naive'])
def test_damaged_evidence_is_rejected_not_replaced(tmp_path, change):
    repo, _, _, keys = retained(tmp_path)
    envelope = repo.retain_admission_observation(**keys)
    if change == 'receipt':
        envelope['receipt_sha256'] = '0'*64
    elif change == 'future':
        envelope['observed_at'] = '2099-01-01T00:00:00+00:00'
    elif change == 'backdate':
        envelope['observed_at'] = '2000-01-01T00:00:00+00:00'
    elif change == 'naive':
        envelope['observed_at'] = '2026-11-06T00:00:00'
    elif change == 'live':
        envelope['live_money'] = True
    payload = json.dumps(envelope, sort_keys=True, separators=(',', ':'))
    digest = '0'*64 if change == 'digest' else sha256(payload.encode()).hexdigest()
    with repo.database.connect() as conn:
        conn.execute('DROP TRIGGER security_master_observations_no_update')
        conn.execute('UPDATE security_master_observations SET envelope_json=?, envelope_sha256=?',
                     (payload, digest))
    for method in [repo.get_admission_observation, repo.retain_admission_observation]:
        with pytest.raises(ValueError):
            method(**keys)
    with repo.database.connect() as conn:
        assert conn.execute('SELECT envelope_json FROM security_master_observations').fetchone()[0] == payload


def test_write_failure_leaves_no_observation(tmp_path):
    repo, _, _, keys = retained(tmp_path)
    with repo.database.connect() as conn:
        conn.execute("CREATE TRIGGER fail_observation BEFORE INSERT ON security_master_observations "
                     "BEGIN SELECT RAISE(ABORT, 'disk fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.retain_admission_observation(**keys)
    with pytest.raises(KeyError):
        repo.get_admission_observation(**keys)
    assert repo.get_admission(**keys)['live_money'] is False


def test_explicit_upgrade_preserves_admission_without_backfill(tmp_path):
    repo, _, _, keys = retained(tmp_path)
    before = repo.get_admission(**keys)
    with repo.database.connect() as conn:
        conn.execute('DROP TABLE security_master_observations')
        conn.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match='upgrade required'):
        repo.database.initialize()
    repo.database.initialize(allow_upgrade=True)
    assert repo.database.schema_version() == 12
    assert repo.get_admission(**keys) == before
    with pytest.raises(KeyError):
        repo.get_admission_observation(**keys)


def test_concurrent_producers_retain_one_original_envelope(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repo, _, _, keys = retained(tmp_path)
    barrier = Barrier(2)
    producers = [SecurityMasterRepository(repo.database) for _ in range(2)]
    for producer in producers:
        original = producer.observe_admission
        calls = [0]
        def observe(original=original, calls=calls, **kw):
            result = original(**kw)
            calls[0] += 1
            if calls[0] == 1:
                barrier.wait(timeout=5)
            return result
        monkeypatch.setattr(producer, 'observe_admission', observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: p.retain_admission_observation(**keys), producers))
    assert results[0] == results[1] == repo.get_admission_observation(**keys)


def test_failure_after_commit_recovers_original_time(tmp_path, monkeypatch):
    repo, _, _, keys = retained(tmp_path)
    original = repo.get_admission_observation
    def fail(**kw):
        raise RuntimeError('simulated lost acknowledgement')
    monkeypatch.setattr(repo, 'get_admission_observation', fail)
    with pytest.raises(RuntimeError):
        repo.retain_admission_observation(**keys)
    monkeypatch.setattr(repo, 'get_admission_observation', original)
    first = original(**keys)
    assert repo.retain_admission_observation(**keys) == first
