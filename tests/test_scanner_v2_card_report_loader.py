"""Artificial retained reports in temporary directories only.

No producer, route, market coverage, admission or edge evidence is implied.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os

import pytest

from app.scanner_v2_card_report_loader import (
    ENV, POINTER_NAME, POINTER_SCHEMA, REASONS, RESPONSE_SCHEMA,
    load_retained_egx_card_report)
from tests.test_scanner_v2_card_report import VIEW, project
from tests.test_scanner_v2_egx_rank import NEXT_CLOSE, record, report
from tests.test_scanner_v2_retained_binding import OPEN

SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


def encode(doc):
    return json.dumps(doc, sort_keys=True, allow_nan=False).encode()


def publish(directory, payload, *, digest=None):
    digest = digest or sha256(payload).hexdigest()
    (directory / f'{digest}.json').write_bytes(payload)
    pointer = {'schema_version': POINTER_SCHEMA, 'report_sha256': digest}
    (directory / POINTER_NAME).write_text(json.dumps(pointer))
    return digest


def load(directory, now=VIEW):
    return load_retained_egx_card_report(environ={ENV: str(directory)}, now=now)


def assert_unavailable(out, reason):
    assert out['status'] == 'UNAVAILABLE' and out['reason'] == reason and reason in REASONS
    assert out['report'] is None and out['report_sha256'] is None
    assert out['load_freshness'] is None and out['cards_current_at_load'] is None
    assert {k: out[k] for k in SAFETY} == SAFETY


def test_published_report_loads_only_reader_accepted_bytes(tmp_path):
    payload = encode(project())
    digest = publish(tmp_path, payload)
    out = load(tmp_path)
    assert out['schema_version'] == RESPONSE_SCHEMA
    assert out['status'] == 'AVAILABLE' and out['reason'] is None and out['detail'] is None
    assert out['report'] == json.loads(payload) and out['report_sha256'] == digest
    assert out['report_observed_at'] == out['report']['observed_at']
    assert out['load_freshness'] == 'WITHIN_RETAINED_WINDOW'
    assert out['cards_current_at_load'] == len(out['report']['cards']) > 0
    assert {k: out[k] for k in SAFETY} == SAFETY


def test_old_report_is_reported_stale_without_relabelling_cards(tmp_path):
    payload = encode(project())
    publish(tmp_path, payload)
    for now in (OPEN, NEXT_CLOSE, NEXT_CLOSE + timedelta(days=30)):
        out = load(tmp_path, now=now)
        assert out['status'] == 'AVAILABLE' and out['load_freshness'] == 'STALE_AT_LOAD'
        assert out['cards_current_at_load'] == 0
        # Producer-time card labels are preserved exactly; the loader never edits them.
        assert out['report'] == json.loads(payload)


def test_report_without_cards_loads(tmp_path):
    artifact = report([record('AAAA', 'WATCHLIST'), record('BBBB', 'NO_TRADE')])
    publish(tmp_path, encode(project(artifact=artifact)))
    out = load(tmp_path)
    assert out['status'] == 'AVAILABLE' and out['cards_current_at_load'] == 0


def test_load_before_report_observation_fails_closed(tmp_path):
    publish(tmp_path, encode(project()))
    assert_unavailable(load(tmp_path, now=VIEW - timedelta(microseconds=1)),
                       'REPORT_FROM_FUTURE')


def test_not_configured_and_invalid_configuration(tmp_path):
    for environ in ({}, {ENV: ''}):
        out = load_retained_egx_card_report(environ=environ, now=VIEW)
        assert out['status'] == 'NOT_CONFIGURED' and out['reason'] == 'NOT_CONFIGURED'
        assert out['report'] is None and {k: out[k] for k in SAFETY} == SAFETY
    for configured in ('relative/dir', f'{tmp_path}\x00x'):
        out = load_retained_egx_card_report(environ={ENV: configured}, now=VIEW)
        assert_unavailable(out, 'CONFIG_INVALID')


def test_missing_or_symlinked_directory_unavailable(tmp_path):
    assert_unavailable(load(tmp_path / 'absent'), 'DIRECTORY_UNAVAILABLE')
    real = tmp_path / 'real'
    real.mkdir()
    publish(real, encode(project()))
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    assert_unavailable(load(link), 'DIRECTORY_UNAVAILABLE')
    regular = tmp_path / 'file'
    regular.write_bytes(b'')
    assert_unavailable(load(regular), 'DIRECTORY_UNAVAILABLE')


def test_unpublished_directory_reports_not_published(tmp_path):
    assert_unavailable(load(tmp_path), 'NOT_PUBLISHED')


@pytest.mark.parametrize('pointer', [
    b'',
    b'not json',
    b'\xff\xfe',
    b'[]',
    b'{"schema_version":"scanner-v2-card-report-pointer-v1","report_sha256":"' + b'a' * 64
    + b'","report_sha256":"' + b'b' * 64 + b'"}',
    json.dumps({'schema_version': 'other', 'report_sha256': 'a' * 64}).encode(),
    json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': 'A' * 64}).encode(),
    json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': '../' + 'a' * 61}).encode(),
    json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': 1}).encode(),
    json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': 'a' * 64,
                'extra': True}).encode(),
    b'{"schema_version":"scanner-v2-card-report-pointer-v1","report_sha256":NaN}',
    b'[' * 100000,
    b' ' * 4097,
])
def test_invalid_pointer_rejected(tmp_path, pointer):
    publish(tmp_path, encode(project()))
    (tmp_path / POINTER_NAME).write_bytes(pointer)
    assert_unavailable(load(tmp_path), 'POINTER_INVALID')


def test_symlinked_or_non_regular_pointer_rejected(tmp_path):
    payload = encode(project())
    digest = publish(tmp_path, payload)
    target = tmp_path / 'elsewhere.json'
    os.replace(tmp_path / POINTER_NAME, target)
    (tmp_path / POINTER_NAME).symlink_to(target)
    assert_unavailable(load(tmp_path), 'POINTER_INVALID')
    (tmp_path / POINTER_NAME).unlink()
    (tmp_path / POINTER_NAME).mkdir()
    assert_unavailable(load(tmp_path), 'POINTER_INVALID')
    assert (tmp_path / f'{digest}.json').read_bytes() == payload


def test_missing_symlinked_or_non_regular_report_unreadable(tmp_path):
    payload = encode(project())
    digest = publish(tmp_path, payload)
    report_path = tmp_path / f'{digest}.json'
    report_path.unlink()
    assert_unavailable(load(tmp_path), 'REPORT_UNREADABLE')
    elsewhere = tmp_path / 'elsewhere.bin'
    elsewhere.write_bytes(payload)
    report_path.symlink_to(elsewhere)
    assert_unavailable(load(tmp_path), 'REPORT_UNREADABLE')
    report_path.unlink()
    report_path.mkdir()
    assert_unavailable(load(tmp_path), 'REPORT_UNREADABLE')


def test_report_bytes_must_match_pointer_hash(tmp_path):
    payload = encode(project())
    digest = publish(tmp_path, payload)
    (tmp_path / f'{digest}.json').write_bytes(payload + b' ')
    out = load(tmp_path)
    assert_unavailable(out, 'REPORT_REJECTED')
    assert 'hash mismatch' in out['detail']


def test_reader_rejection_surfaces_code_defined_detail_only(tmp_path):
    doc = project()
    doc['live_money'] = True
    publish(tmp_path, encode(doc))
    out = load(tmp_path)
    assert_unavailable(out, 'REPORT_REJECTED')
    assert out['detail'] == 'report safety invariant violated'


def test_oversized_and_deeply_nested_reports_rejected(tmp_path):
    from app.scanner_v2_card_report_reader import MAX_BYTES
    publish(tmp_path, b' ' * (MAX_BYTES + 1))
    assert_unavailable(load(tmp_path), 'REPORT_REJECTED')
    publish(tmp_path, b'[' * 100000)
    out = load(tmp_path)
    assert_unavailable(out, 'REPORT_REJECTED')


def test_naive_or_non_datetime_clock_is_a_programming_error(tmp_path):
    for now in (datetime(2026, 11, 4, 7), '2026-11-04T07:00:00+00:00'):
        with pytest.raises(TypeError):
            load_retained_egx_card_report(environ={ENV: str(tmp_path)}, now=now)


def test_loader_never_writes(tmp_path):
    payload = encode(project())
    publish(tmp_path, payload)
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    for now in (VIEW, OPEN, NEXT_CLOSE):
        load(tmp_path, now=now)
    assert sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir()) == before
