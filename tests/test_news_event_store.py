from datetime import timedelta
import fcntl
from hashlib import sha256
import os

import pytest

from app.news_event import select_point_in_time
from app.news_event_record import encode_news_event
from app.news_event_store import (
    MAX_OBSERVATION_LAG, RESULT_SCHEMA, STORE_REASONS, NewsEventStoreError,
    load_retained_news_events, retain_news_event)
from tests.test_news_event_record import AT, event

SAFETY = {'trading_effect': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The store refuses group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def at_observation(record, lag=timedelta(seconds=30)):
    return record.source.observed_at + lag


def retain(directory, record, now=None):
    return retain_news_event(str(directory), record,
                             now=at_observation(record) if now is None else now)


def load(directory, market='EGX'):
    return load_retained_news_events(str(directory), market=market)


def snapshot(directory):
    return sorted((p.name, None if p.is_dir() else p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in STORE_REASONS
    with pytest.raises(NewsEventStoreError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def test_retained_record_is_content_addressed_and_loads_exactly(tmp_path):
    record = event()
    out = retain(tmp_path, record)
    assert out['schema_version'] == RESULT_SCHEMA and out['status'] == 'RETAINED'
    assert out['identity'] == record.identity and out['dedup_key'] == record.dedup_key
    assert out['retained_count'] == 1 and out['market'] == 'EGX'
    assert {k: out[k] for k in SAFETY} == SAFETY
    payload = encode_news_event(record)
    # Only the content-addressed record remains; no temporary files.
    assert snapshot(tmp_path) == [(f'{record.identity}.json', payload)]
    assert sha256(payload).hexdigest() == record.identity
    assert os.stat(tmp_path / f'{record.identity}.json').st_mode & 0o222 == 0
    loaded = load(tmp_path)
    assert [e.identity for e in loaded] == [record.identity]
    assert loaded[0].model_dump(mode='json') == record.model_dump(mode='json')


def test_exact_retry_is_idempotent_even_after_the_observation_window(tmp_path):
    record = event()
    retain(tmp_path, record)
    before = snapshot(tmp_path)
    late = record.source.observed_at + MAX_OBSERVATION_LAG + timedelta(days=3)
    assert retain(tmp_path, record, now=late)['status'] == 'ALREADY_RETAINED'
    assert snapshot(tmp_path) == before


def test_future_and_backdated_observations_refused_without_writes(tmp_path):
    record = event()
    observed = record.source.observed_at
    refused('OBSERVED_IN_FUTURE', retain, tmp_path, record, now=observed - timedelta(microseconds=1))
    refused('OBSERVATION_BACKDATED', retain, tmp_path, record,
            now=observed + MAX_OBSERVATION_LAG + timedelta(microseconds=1))
    assert snapshot(tmp_path) == []
    assert retain(tmp_path, record, now=observed)['status'] == 'RETAINED'


def test_same_revision_conflict_refused_and_later_revision_kept_beside_original(tmp_path):
    original = event('a')
    retain(tmp_path, original)
    before = snapshot(tmp_path)
    conflict = event('a', observed=original.source.observed_at + timedelta(minutes=1))
    assert conflict.dedup_key == original.dedup_key and conflict.identity != original.identity
    refused('REVISION_CONFLICT', retain, tmp_path, conflict)
    assert snapshot(tmp_path) == before

    revised = event('a', revision=1, observed=AT + timedelta(minutes=1))
    assert retain(tmp_path, revised)['retained_count'] == 2
    # The earlier revision is never replaced; earlier decisions still see it.
    loaded = load(tmp_path)
    assert {e.identity for e in loaded} == {original.identity, revised.identity}
    assert [e.identity for e in select_point_in_time(loaded, market='EGX', decision_time=AT)] == [
        original.identity]
    later = select_point_in_time(loaded, market='EGX', decision_time=AT + timedelta(minutes=1))
    assert [e.identity for e in later] == [revised.identity]


def test_records_retained_after_a_decision_cannot_leak_into_it(tmp_path):
    early = event('early')
    retain(tmp_path, early)
    future = event('future', published=AT + timedelta(minutes=10))
    retain(tmp_path, future)
    picked = select_point_in_time(load(tmp_path), market='EGX', decision_time=AT)
    assert [e.identity for e in picked] == [early.identity]


def test_markets_are_loaded_separately(tmp_path):
    egx, us = event('x', market='EGX'), event('x', market='US')
    retain(tmp_path, egx)
    retain(tmp_path, us)
    assert [e.identity for e in load(tmp_path, 'EGX')] == [egx.identity]
    assert [e.identity for e in load(tmp_path, 'US')] == [us.identity]
    with pytest.raises(ValueError, match='unknown market'):
        load(tmp_path, 'ALL')


def test_load_order_is_deterministic(tmp_path):
    records = [event(f'e{i}') for i in range(4)]
    for record in reversed(records):
        retain(tmp_path, record)
    assert [e.identity for e in load(tmp_path)] == sorted(r.identity for r in records)


@pytest.mark.parametrize('damage', [
    lambda d, name: (d / name).write_bytes(b'{}'),
    lambda d, name: (d / name).write_bytes((d / name).read_bytes() + b'\n'),
    lambda d, name: (d / 'notes.txt').write_bytes(b'x'),
    lambda d, name: (d / ('0' * 64 + '.json')).symlink_to(d / name),
    lambda d, name: (d / ('1' * 64 + '.json')).mkdir(),
])
def test_damaged_or_unexpected_entries_fail_closed_for_load_and_retention(tmp_path, damage):
    record = event()
    retain(tmp_path, record)
    name = f'{record.identity}.json'
    os.chmod(tmp_path / name, 0o644)
    damage(tmp_path, name)
    refused('STORE_INVALID', load, tmp_path)
    before = snapshot(tmp_path)
    refused('STORE_INVALID', retain, tmp_path, event('other'))
    assert snapshot(tmp_path) == before


def test_leftover_temporary_file_is_tolerated(tmp_path):
    (tmp_path / '.publish-1-0123456789abcdef.tmp').write_bytes(b'partial')
    record = event()
    assert retain(tmp_path, record)['status'] == 'RETAINED'
    assert [e.identity for e in load(tmp_path)] == [record.identity]


def test_invalid_records_and_inputs_refused(tmp_path):
    refused('RECORD_REJECTED', retain, tmp_path, event().model_dump(mode='python'),
            now=AT)
    with pytest.raises(TypeError, match='aware'):
        retain_news_event(str(tmp_path), event(), now=AT.replace(tzinfo=None))
    refused('CONFIG_INVALID', retain_news_event, 'relative/dir', event(), now=AT)
    refused('CONFIG_INVALID', load_retained_news_events, 'relative/dir', market='EGX')
    refused('DIRECTORY_UNAVAILABLE', load_retained_news_events, str(tmp_path / 'missing'),
            market='EGX')
    assert snapshot(tmp_path) == []


def test_unsafe_or_symlinked_directory_refused(tmp_path):
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o777)
    refused('DIRECTORY_UNSAFE', retain, shared, event())
    refused('DIRECTORY_UNSAFE', load, shared)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(private)
    refused('DIRECTORY_UNAVAILABLE', retain, link, event())
    assert list(private.iterdir()) == [] and list(shared.iterdir()) == []


def test_concurrent_retention_refused_while_locked(tmp_path):
    holder = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        refused('STORE_BUSY', retain, tmp_path, event())
    finally:
        os.close(holder)
    assert snapshot(tmp_path) == []
    assert retain(tmp_path, event())['status'] == 'RETAINED'
