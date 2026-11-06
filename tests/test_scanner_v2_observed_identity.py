"""Temporary committed snapshots and artificial clocks, no runtime evidence."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from app.scanner_v2_identity import bind_observed_egid_identities
from app.storage.security_master_repository import SecurityMasterRepository
from tests.test_security_master_admission_history import refresh, direct_inputs
from tests.test_security_master_refresh_runtime import build, DAY, FakeEGIDProvider, egid_record


def setup(tmp_path):
    database, runtime = build(tmp_path)
    result = refresh(runtime)
    manifest, _ = direct_inputs(database, result)
    payload = FakeEGIDProvider([egid_record()]).fetch_security_master().payload
    repo = SecurityMasterRepository(database)
    observation = repo.observe_admission(provider='egid', snapshot_date=DAY,
                                         artifact_sha256=result.artifact_sha256)
    args = dict(manifest=manifest, observation=observation, session=DAY,
                observed_at=datetime.now(timezone.utc), requested=('CCAP', 'MISSING'))
    return repo, payload, args


def rewrite(observation, mutate):
    receipt = json.loads(observation['receipt_json'])
    mutate(receipt)
    observation['receipt_json'] = json.dumps(receipt, sort_keys=True, separators=(',', ':'))
    observation['receipt_sha256'] = sha256(observation['receipt_json'].encode()).hexdigest()


def test_committed_binding_survives_master_replacement_and_is_immutable(tmp_path):
    repo, payload, args = setup(tmp_path)
    first = bind_observed_egid_identities(payload, **args)
    repo.replace_provider_snapshot(provider='egid', instruments=[])
    assert bind_observed_egid_identities(payload, **args) == first
    assert first.identity.instrument_ids[0][0] == 'CCAP'
    assert first.identity.exclusions == (('MISSING', 'IDENTITY_MISSING'),)
    assert first.admission == 'INTERNAL_SNAPSHOT_VERIFIED'
    assert first.identity.admission == 'NOT_ESTABLISHED'
    assert first.live_money is False
    with pytest.raises(ValueError):
        first.available_at = args['observed_at']
    assert json.loads(first.model_dump_json())['live_money'] is False


@pytest.mark.parametrize('key,value', [
    ('provider', 'us'), ('snapshot_date', '2020-01-01'), ('artifact_sha256', '0'*64),
    ('policy', 'BYPASS'), ('live_money', True), ('record_count', True),
    ('byte_size', 0), ('instrument_count', 0), ('previous_instrument_count', 100),
    ('received_at', '2099-01-01T00:00:00+00:00'),
    ('recorded_at', '2099-01-01T00:00:00+00:00'),
    ('recorded_at', '2026-01-01T00:00:00'),
])
def test_receipt_semantics_rechecked_even_with_matching_hash(tmp_path, key, value):
    _, payload, args = setup(tmp_path)
    rewrite(args['observation'], lambda r: r.update({key: value}))
    with pytest.raises(ValueError):
        bind_observed_egid_identities(payload, **args)


def test_full_canonical_snapshot_checked_even_for_empty_selection(tmp_path):
    _, payload, args = setup(tmp_path)
    args['requested'] = ()
    rewrite(args['observation'], lambda r: r['instruments'][0].update(canonical_ticker='OTHER'))
    with pytest.raises(ValueError, match='disagree'):
        bind_observed_egid_identities(payload, **args)


def test_receipt_hash_and_envelope_fail_closed(tmp_path):
    _, payload, args = setup(tmp_path)
    args['observation']['receipt_json'] += ' '
    with pytest.raises(ValueError, match='integrity'):
        bind_observed_egid_identities(payload, **args)
    args['observation']['live_money'] = 0
    with pytest.raises(ValueError, match='observation'):
        bind_observed_egid_identities(payload, **args)


def test_recording_time_cannot_stand_in_for_post_commit_availability(tmp_path):
    _, payload, args = setup(tmp_path)
    recorded = datetime.fromisoformat(json.loads(args['observation']['receipt_json'])['recorded_at'])
    args['observed_at'] = recorded
    with pytest.raises(ValueError, match='unavailable'):
        bind_observed_egid_identities(payload, **args)


def test_producer_samples_clock_after_read_and_rejects_clock_regression(tmp_path, monkeypatch):
    repo, _, args = setup(tmp_path)
    original = repo.get_admission
    events = []
    def read(**kw):
        events.append('read')
        return original(**kw)
    def clock():
        assert events == ['read']
        return '2000-01-01T00:00:00+00:00'
    monkeypatch.setattr(repo, 'get_admission', read)
    monkeypatch.setattr('app.storage.security_master_repository._utc_now', clock)
    with pytest.raises(ValueError, match='clock'):
        repo.observe_admission(provider='egid', snapshot_date=DAY,
                               artifact_sha256=args['manifest'].sha256)


def test_new_read_does_not_backdate_old_receipt(tmp_path):
    repo, payload, args = setup(tmp_path)
    later = repo.observe_admission(provider='egid', snapshot_date=DAY,
                                  artifact_sha256=args['manifest'].sha256)
    assert later['receipt_sha256'] == args['observation']['receipt_sha256']
    args['observed_at'] = datetime.fromisoformat(later['observed_at']) - timedelta(microseconds=1)
    args['observation'] = later
    with pytest.raises(ValueError, match='unavailable'):
        bind_observed_egid_identities(payload, **args)


def test_missing_admission_never_generates_observation(tmp_path):
    repo, _, _ = setup(tmp_path)
    with pytest.raises(KeyError):
        repo.observe_admission(provider='egid', snapshot_date=DAY, artifact_sha256='0'*64)


def test_uncommitted_receipt_is_not_visible_to_observer(tmp_path):
    repo, _, args = setup(tmp_path)
    with repo.database.connect() as writer:
        writer.execute('DROP TRIGGER security_master_admissions_no_update')
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE security_master_admissions SET artifact_sha256 = ?", ('b'*64,))
        with pytest.raises(KeyError):
            repo.observe_admission(provider='egid', snapshot_date=DAY, artifact_sha256='b'*64)
        visible = repo.observe_admission(provider='egid', snapshot_date=DAY,
                                        artifact_sha256=args['manifest'].sha256)
        assert visible['receipt_sha256'] == args['observation']['receipt_sha256']
        writer.rollback()


@pytest.mark.parametrize('payload', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'])
def test_receipt_json_is_strict(tmp_path, payload):
    _, artifact, args = setup(tmp_path)
    args['observation']['receipt_json'] = payload
    args['observation']['receipt_sha256'] = sha256(payload.encode()).hexdigest()
    with pytest.raises(ValueError):
        bind_observed_egid_identities(artifact, **args)
