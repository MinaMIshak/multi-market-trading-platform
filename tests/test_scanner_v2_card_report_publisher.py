"""Artificial retained reports published into temporary directories only.

No scheduler, runtime producer, market coverage, admission or edge evidence is
implied by these tests.
"""
from datetime import datetime, timedelta
import fcntl
from hashlib import sha256
import json
import os
import stat

import pytest

from app.scanner_v2_card_report_loader import (
    ENV, POINTER_NAME, POINTER_SCHEMA, load_retained_egx_card_report)
from app.scanner_v2_card_report_publisher import (
    PUBLICATION_REASONS, RESULT_SCHEMA, CardReportPublicationError,
    encode_card_report, publish_egx_card_report)
from tests.test_scanner_v2_card_report import VIEW, evidence, project
from tests.test_scanner_v2_egx_rank import report

SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}
EARLIER_GENERATED = '2026-11-04T06:00:00+00:00'


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The publisher refuses group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def publish(directory, now=VIEW, artifact=None, expected=None):
    artifact = artifact if artifact is not None else report()
    return publish_egx_card_report(
        str(directory), artifact, expected_sha256=expected or sha256(artifact).hexdigest(),
        evidence=evidence(), now=now)


def load(directory, now=VIEW):
    return load_retained_egx_card_report(environ={ENV: str(directory)}, now=now)


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def refused(reason, *args, **kwargs):
    assert reason in PUBLICATION_REASONS
    with pytest.raises(CardReportPublicationError) as info:
        publish(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def test_published_report_is_served_by_the_loader(tmp_path):
    out = publish(tmp_path)
    assert out['schema_version'] == RESULT_SCHEMA and out['status'] == 'PUBLISHED'
    assert out['previous_report_sha256'] is None
    assert {k: out[k] for k in SAFETY} == SAFETY
    payload = encode_card_report(project())
    assert out['report_sha256'] == sha256(payload).hexdigest()
    assert out['card_count'] == len(project()['cards']) > 0
    served = load(tmp_path)
    assert served['status'] == 'AVAILABLE' and served['report_sha256'] == out['report_sha256']
    assert served['report'] == json.loads(payload)
    # Only the content-addressed report and the pointer remain; no temporary files.
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [f"{out['report_sha256']}.json", POINTER_NAME])
    assert (tmp_path / f"{out['report_sha256']}.json").read_bytes() == payload
    pointer = json.loads((tmp_path / POINTER_NAME).read_bytes())
    assert pointer == {'schema_version': POINTER_SCHEMA, 'report_sha256': out['report_sha256']}
    mode = (tmp_path / f"{out['report_sha256']}.json").stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_observed_at_is_the_producer_clock(tmp_path):
    later = VIEW + timedelta(minutes=5)
    out = publish(tmp_path, now=later)
    assert datetime.fromisoformat(out['observed_at']) == later
    assert load(tmp_path, now=later)['report_observed_at'] == out['observed_at']


def test_identical_republication_is_idempotent(tmp_path):
    first = publish(tmp_path)
    before = snapshot(tmp_path)
    again = publish(tmp_path)
    assert again['status'] == 'ALREADY_CURRENT'
    assert again['report_sha256'] == again['previous_report_sha256'] == first['report_sha256']
    assert snapshot(tmp_path) == before


def test_later_observation_replaces_pointer_and_retains_old_bytes(tmp_path):
    first = publish(tmp_path)
    old_bytes = (tmp_path / f"{first['report_sha256']}.json").read_bytes()
    later = VIEW + timedelta(minutes=10)
    second = publish(tmp_path, now=later)
    assert second['status'] == 'PUBLISHED'
    assert second['previous_report_sha256'] == first['report_sha256'] != second['report_sha256']
    assert (tmp_path / f"{first['report_sha256']}.json").read_bytes() == old_bytes
    assert load(tmp_path, now=later)['report_sha256'] == second['report_sha256']


def test_clock_regression_is_refused_without_changes(tmp_path):
    publish(tmp_path, now=VIEW + timedelta(hours=1))
    before = snapshot(tmp_path)
    refused('CLOCK_REGRESSION', tmp_path, now=VIEW)
    assert snapshot(tmp_path) == before


def test_older_ranking_generation_cannot_replace_newer(tmp_path):
    publish(tmp_path)
    before = snapshot(tmp_path)
    refused('RANKING_REGRESSION', tmp_path, now=VIEW + timedelta(minutes=1),
            artifact=report(generated_at=EARLIER_GENERATED))
    assert snapshot(tmp_path) == before


def test_newer_ranking_generation_replaces_older(tmp_path):
    early = datetime.fromisoformat(EARLIER_GENERATED)
    first = publish(tmp_path, now=early, artifact=report(generated_at=EARLIER_GENERATED))
    second = publish(tmp_path, now=VIEW)
    assert second['status'] == 'PUBLISHED'
    assert second['previous_report_sha256'] == first['report_sha256']


@pytest.mark.parametrize('pointer', [
    b'not json',
    json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': 'a' * 64}).encode(),
])
def test_damaged_existing_publication_is_preserved_and_refused(tmp_path, pointer):
    (tmp_path / POINTER_NAME).write_bytes(pointer)
    before = snapshot(tmp_path)
    error = refused('EXISTING_PUBLICATION_INVALID', tmp_path)
    assert error.detail in ('POINTER_INVALID', 'REPORT_UNREADABLE')
    assert snapshot(tmp_path) == before


def test_rejected_existing_report_is_preserved_and_refused(tmp_path):
    first = publish(tmp_path)
    path = tmp_path / f"{first['report_sha256']}.json"
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b' ')
    before = snapshot(tmp_path)
    error = refused('EXISTING_PUBLICATION_INVALID', tmp_path, now=VIEW + timedelta(minutes=1))
    assert error.detail == 'REPORT_REJECTED'
    assert snapshot(tmp_path) == before


def test_projection_failure_writes_nothing(tmp_path):
    refused('PROJECTION_REJECTED', tmp_path, expected='0' * 64)
    refused('PROJECTION_REJECTED', tmp_path, now=VIEW - timedelta(microseconds=1))
    assert snapshot(tmp_path) == []


def test_conflicting_bytes_at_content_address_are_never_overwritten(tmp_path):
    digest = sha256(encode_card_report(project())).hexdigest()
    (tmp_path / f'{digest}.json').write_bytes(b'conflict')
    refused('CONTENT_CONFLICT', tmp_path)
    assert snapshot(tmp_path) == [(f'{digest}.json', b'conflict')]


def test_symlink_at_content_address_is_a_conflict(tmp_path):
    payload = encode_card_report(project())
    digest = sha256(payload).hexdigest()
    elsewhere = tmp_path / 'elsewhere.bin'
    elsewhere.write_bytes(payload)
    (tmp_path / f'{digest}.json').symlink_to(elsewhere)
    refused('CONTENT_CONFLICT', tmp_path)
    assert not (tmp_path / POINTER_NAME).exists()


def test_identical_retained_bytes_complete_an_interrupted_publication(tmp_path):
    payload = encode_card_report(project())
    digest = sha256(payload).hexdigest()
    (tmp_path / f'{digest}.json').write_bytes(payload)  # crash before pointer replacement
    out = publish(tmp_path)
    assert out['status'] == 'PUBLISHED' and out['report_sha256'] == digest
    assert load(tmp_path)['status'] == 'AVAILABLE'


def test_directory_must_be_absolute_existing_non_symlink_and_protected(tmp_path):
    with pytest.raises(CardReportPublicationError) as info:
        publish_egx_card_report('relative/dir', report(), expected_sha256='0' * 64,
                                evidence=evidence(), now=VIEW)
    assert info.value.reason == 'CONFIG_INVALID'
    refused('DIRECTORY_UNAVAILABLE', tmp_path / 'absent')
    real = tmp_path / 'real'
    real.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    refused('DIRECTORY_UNAVAILABLE', link)
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o777)
    refused('DIRECTORY_UNSAFE', shared)
    assert list(real.iterdir()) == [] and list(shared.iterdir()) == []


def test_concurrent_publication_is_rejected(tmp_path):
    holder = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        refused('PUBLICATION_BUSY', tmp_path)
    finally:
        os.close(holder)
    assert snapshot(tmp_path) == []
    assert publish(tmp_path)['status'] == 'PUBLISHED'


def test_naive_or_non_datetime_clock_is_a_programming_error(tmp_path):
    for now in (datetime(2026, 11, 4, 7), '2026-11-04T07:00:00+00:00'):
        with pytest.raises(TypeError):
            publish(tmp_path, now=now)
    assert snapshot(tmp_path) == []
