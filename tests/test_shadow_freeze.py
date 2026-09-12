"""Offline preservation fixtures; no authentic candidates or empirical results."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.paper import shadow_freeze as shadow


AT = datetime(2026, 9, 12, 20, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(shadow, '_now', lambda: AT)


def freeze(path, **changes):
    arguments = dict(record_id='EGX-20260913', market='EGX',
                     information_cutoff=AT - timedelta(hours=1),
                     decision_cutoff=AT + timedelta(hours=1),
                     document=b'original fixture bytes\x00\xff')
    return shadow.freeze_document(path, **(arguments | changes))


def test_exact_bytes_and_unadmitted_status(tmp_path, clock):
    path = freeze(tmp_path)
    result = shadow.audit_document(path)
    assert bytes.fromhex(result['document_hex']) == b'original fixture bytes\x00\xff'
    assert result['received_at'] == AT.isoformat()
    assert result['label'] == 'EXPERIMENTAL / PAPER ONLY'
    assert result['admission'] == 'UNADMITTED'
    assert result['scoring'] == 'NOT SCORED'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('changes', [
    {'decision_cutoff': AT},
    {'decision_cutoff': AT - timedelta(seconds=1)},
    {'information_cutoff': AT + timedelta(seconds=1)},
    {'information_cutoff': AT.replace(tzinfo=None)},
    {'market': 'UNKNOWN'}, {'record_id': '../escape'}, {'document': b''},
])
def test_rejects_invalid_or_late_freeze(tmp_path, clock, changes):
    with pytest.raises(ValueError):
        freeze(tmp_path, **changes)
    assert not list(tmp_path.iterdir())


def test_never_overwrites_existing_freeze(tmp_path, clock):
    path = freeze(tmp_path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        freeze(tmp_path, document=b'revised fixture')
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_competing_publishers_keep_one_document(tmp_path, clock):
    def publish(document):
        try:
            freeze(tmp_path, document=document)
            return document
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [b'first fixture', b'second fixture']))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    path, = tmp_path.iterdir()
    assert bytes.fromhex(shadow.audit_document(path)['document_hex']) == winners[0]


@pytest.mark.parametrize('times', [
    [AT, AT + timedelta(hours=1)],
    [AT, AT, AT + timedelta(hours=1)],
    [AT, AT - timedelta(seconds=1)],
    [AT, AT, AT - timedelta(seconds=1)],
])
def test_write_crossing_cutoff_or_rollback_is_not_frozen(tmp_path, monkeypatch, times):
    ticks = iter(times)
    monkeypatch.setattr(shadow, '_now', lambda: next(ticks))
    with pytest.raises(ValueError, match='clock rollback or missed'):
        freeze(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('changes', [
    {'document_hex': b'changed fixture'.hex()}, {'document_size': True},
    {'admission': 'APPROVED'}, {'scoring': 'SCORED'},
    {'received_at': (AT + timedelta(hours=2)).isoformat()},
    {'extra': 'unexpected'}, {'market': 'UNKNOWN'},
])
def test_audit_rejects_corruption_and_promotion(tmp_path, clock, changes):
    path = freeze(tmp_path)
    payload = json.loads(path.read_bytes())
    path.write_text(json.dumps(payload | changes))
    with pytest.raises(ValueError):
        shadow.audit_document(path)


def test_failed_fsync_does_not_publish(tmp_path, clock, monkeypatch):
    def fail(_):
        raise OSError('fixture disk error')
    monkeypatch.setattr(shadow.os, 'fsync', fail)
    with pytest.raises(OSError, match='fixture disk error'):
        freeze(tmp_path)
    assert not list(tmp_path.iterdir())
