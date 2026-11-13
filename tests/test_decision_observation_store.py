from datetime import timedelta
import fcntl
from hashlib import sha256
import json
import os

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_observation_store import (
    FIXED_VALUES, RECORD_FIELDS, RESULT_SCHEMA, STORE_REASONS, DecisionObservationStoreError,
    RetainedDecisionObservationError, encode_decision_observation,
    load_retained_decision_observations, read_retained_decision_observation,
    retain_decision_observation)
from app.strategies.trade_decision import trade_card
from tests.test_decision_observation import AT, context, decision, event

NOW = AT + timedelta(minutes=5)
SAFETY = {'outcome_status': 'NOT_OBSERVED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The store refuses group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def retain(directory, d, ctx=None, now=NOW):
    return retain_decision_observation(str(directory), d, ctx, now=now)


def load(directory, market='EGX'):
    return load_retained_decision_observations(str(directory), market=market)


def snapshot(directory):
    return sorted((p.name, None if p.is_dir() else p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in STORE_REASONS
    with pytest.raises(DecisionObservationStoreError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def write_record(directory, record):
    payload = canonical_bytes(record)
    digest = sha256(payload).hexdigest()
    (directory / f'{digest}.json').write_bytes(payload)
    return digest


def test_record_fields_match_the_observation_contract():
    rec = decision_observation(decision(), None, recorded_at=NOW)
    assert set(rec) == RECORD_FIELDS
    assert {k: rec[k] for k in FIXED_VALUES} == FIXED_VALUES


def test_retained_observation_is_content_addressed_and_loads_exactly(tmp_path):
    d = decision()
    out = retain(tmp_path, d)
    payload = canonical_bytes(decision_observation(d, None, recorded_at=NOW))
    digest = sha256(payload).hexdigest()
    assert out['schema_version'] == RESULT_SCHEMA and out['status'] == 'RETAINED'
    assert out['identity'] == digest and out['decision_id'] == d.identity
    assert out['recorded_at'] == '2026-11-05T12:05:00Z' and out['retained_count'] == 1
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert snapshot(tmp_path) == [(f'{digest}.json', payload)]
    assert os.stat(tmp_path / f'{digest}.json').st_mode & 0o222 == 0
    assert load(tmp_path) == ((digest, json.loads(payload)),)
    assert encode_decision_observation(json.loads(payload)) == payload


def test_first_observation_kept_on_retry_within_window(tmp_path):
    d, ctx = decision(), context([event('a')])
    first = retain(tmp_path, d, ctx)
    before = snapshot(tmp_path)
    exact = retain(tmp_path, d, ctx)
    assert exact['status'] == 'ALREADY_RETAINED' and exact['identity'] == first['identity']
    later = retain(tmp_path, d, ctx, now=NOW + timedelta(minutes=30))
    assert later['status'] == 'ALREADY_RETAINED' and later['identity'] == first['identity']
    assert later['recorded_at'] == first['recorded_at'] == '2026-11-05T12:05:00Z'
    assert snapshot(tmp_path) == before


def test_different_observation_of_the_same_decision_conflicts(tmp_path):
    d = decision()
    retain(tmp_path, d)
    before = snapshot(tmp_path)
    refused('OBSERVATION_CONFLICT', retain, tmp_path, d, context([event('a')]))
    assert snapshot(tmp_path) == before


def test_recording_window_enforced_at_store_clock_without_writes(tmp_path):
    d = decision()
    refused('RECORD_REJECTED', retain, tmp_path, d, now=AT - timedelta(seconds=1))
    refused('RECORD_REJECTED', retain, tmp_path, d, now=AT + timedelta(hours=1))
    flat = decision(flat_by=AT + timedelta(minutes=30), fresh=timedelta(hours=2))
    refused('RECORD_REJECTED', retain, tmp_path, flat, now=AT + timedelta(minutes=30))
    assert snapshot(tmp_path) == []
    # A retry after the window closes is refused even when the first record exists.
    retain(tmp_path, d)
    before = snapshot(tmp_path)
    refused('RECORD_REJECTED', retain, tmp_path, d, now=AT + timedelta(hours=2))
    assert snapshot(tmp_path) == before


def test_future_news_cannot_enter_a_retained_observation(tmp_path):
    late = event('late', published=AT - timedelta(hours=3), observed=AT + timedelta(seconds=1))
    retain(tmp_path, decision(), context([late]))
    (_, record), = load(tmp_path)
    assert record['event_context']['section']['event_context_status'] == 'NO_EVENT_FOUND'
    assert record['event_context']['section']['relevant_event_ids'] == []


def test_markets_and_strategy_versions_kept_separate(tmp_path):
    egx_v1, egx_v2 = decision(), decision(strategy_version='2')
    us = decision('US')
    for d in (egx_v1, egx_v2, us):
        retain(tmp_path, d)
    egx = load(tmp_path, 'EGX')
    assert sorted(r['strategy_version'] for _, r in egx) == ['1', '2']
    assert [i for i, _ in egx] == sorted(i for i, _ in egx)
    assert [r['decision_id'] for _, r in load(tmp_path, 'US')] == [us.identity]
    with pytest.raises(ValueError, match='unknown market'):
        load(tmp_path, 'ALL')


def _duplicate_decision(directory, name):
    write_record(directory, decision_observation(decision(), None,
                                                 recorded_at=NOW + timedelta(minutes=1)))


@pytest.mark.parametrize('damage', [
    lambda d, name: (d / name).write_bytes(b'{}'),
    lambda d, name: (d / name).write_bytes((d / name).read_bytes() + b'\n'),
    lambda d, name: (d / 'notes.txt').write_bytes(b'x'),
    lambda d, name: (d / ('0' * 64 + '.json')).symlink_to(d / name),
    lambda d, name: (d / ('1' * 64 + '.json')).mkdir(),
    _duplicate_decision,
])
def test_damaged_or_unexpected_entries_fail_closed(tmp_path, damage):
    out = retain(tmp_path, decision())
    name = f"{out['identity']}.json"
    os.chmod(tmp_path / name, 0o644)
    damage(tmp_path, name)
    refused('STORE_INVALID', load, tmp_path)
    before = snapshot(tmp_path)
    refused('STORE_INVALID', retain, tmp_path, decision(strategy_version='2'))
    assert snapshot(tmp_path) == before


def test_leftover_temporary_file_is_tolerated(tmp_path):
    (tmp_path / '.publish-1-0123456789abcdef.tmp').write_bytes(b'partial')
    assert retain(tmp_path, decision())['status'] == 'RETAINED'
    assert len(load(tmp_path)) == 1


def _tampered(change):
    record = decision_observation(decision(), context([event('a')]), recorded_at=NOW)
    change(record)
    return canonical_bytes(record)


@pytest.mark.parametrize('change', [
    lambda r: r.update(live_money=True),
    lambda r: r.update(execution_allowed=True),
    lambda r: r.update(outcome={'r_multiple': '2'}),
    lambda r: r.update(outcome_status='WIN'),
    lambda r: r.update(performance_claim='EDGE'),
    lambda r: r.update(promotion='AUTHORIZED'),
    lambda r: r.update(extra=1),
    lambda r: r.pop('provenance'),
    lambda r: r.update(market='ALL'),
    lambda r: r.update(risk_per_share='1.4'),
    lambda r: r.update(recorded_at='2026-11-05T13:00:00Z'),
    lambda r: r.update(recorded_at='2026-11-05T11:59:59Z'),
    lambda r: r.update(recorded_at='2026-11-05T12:05:00'),
    lambda r: r['event_context'].update(section_sha256='0' * 64),
    lambda r: r['event_context']['section'].update(decision_id='0' * 64),
    lambda r: r['event_context'].update(status='NOT_ATTACHED'),
])
def test_reader_rejects_tampered_records(change):
    payload = _tampered(change)
    with pytest.raises(RetainedDecisionObservationError):
        read_retained_decision_observation(payload, expected_sha256=sha256(payload).hexdigest())
    with pytest.raises(ValueError):
        encode_decision_observation(json.loads(payload))


def test_reader_rejects_wrong_digest_noncanonical_and_duplicate_keys():
    payload = _tampered(lambda r: None)
    digest = sha256(payload).hexdigest()
    assert read_retained_decision_observation(payload, expected_sha256=digest)['live_money'] is False
    with pytest.raises(RetainedDecisionObservationError, match='hash mismatch'):
        read_retained_decision_observation(payload, expected_sha256='0' * 64)
    pretty = json.dumps(json.loads(payload), indent=1, sort_keys=True).encode()
    with pytest.raises(RetainedDecisionObservationError, match='not canonical'):
        read_retained_decision_observation(pretty, expected_sha256=sha256(pretty).hexdigest())
    duplicated = b'{"mode":"SHADOW",' + payload[1:]
    with pytest.raises(RetainedDecisionObservationError):
        read_retained_decision_observation(duplicated,
                                           expected_sha256=sha256(duplicated).hexdigest())
    with pytest.raises(RetainedDecisionObservationError):
        read_retained_decision_observation(b'', expected_sha256=sha256(b'').hexdigest())


def test_invalid_inputs_refused(tmp_path):
    refused('RECORD_REJECTED', retain, tmp_path, decision().model_dump(mode='python'))
    with pytest.raises(TypeError, match='aware'):
        retain_decision_observation(str(tmp_path), decision(), now=NOW.replace(tzinfo=None))
    refused('CONFIG_INVALID', retain_decision_observation, 'relative/dir', decision(), now=NOW)
    refused('CONFIG_INVALID', load_retained_decision_observations, 'relative/dir', market='EGX')
    refused('DIRECTORY_UNAVAILABLE', load_retained_decision_observations,
            str(tmp_path / 'missing'), market='EGX')
    assert snapshot(tmp_path) == []


def test_unsafe_or_symlinked_directory_refused(tmp_path):
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o777)
    refused('DIRECTORY_UNSAFE', retain, shared, decision())
    refused('DIRECTORY_UNSAFE', load, shared)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(private)
    refused('DIRECTORY_UNAVAILABLE', retain, link, decision())
    assert list(private.iterdir()) == [] and list(shared.iterdir()) == []


def test_concurrent_retention_refused_while_locked(tmp_path):
    holder = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        refused('STORE_BUSY', retain, tmp_path, decision())
    finally:
        os.close(holder)
    assert snapshot(tmp_path) == []
    assert retain(tmp_path, decision())['status'] == 'RETAINED'


def test_decision_and_card_unchanged_by_retention(tmp_path):
    d = decision()
    before, card = d.model_dump_json(), trade_card(d, observed_at=AT)
    retain(tmp_path, d, context([event('a')]))
    assert d.model_dump_json() == before and trade_card(d, observed_at=AT) == card
