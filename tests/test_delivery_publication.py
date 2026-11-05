"""Temporary publication fixtures; no installed-host acceptance claims."""
import json
import os
from unittest.mock import patch

import pytest

from app.delivery_status import public_receipt, project_delivery, load_delivery_status
from tests.test_delivery_status import receipt, acceptance, OLD, SECRET
from tools.delivery_publication import DeliveryPublication
from tools.delivery_runner import DeliveryError, delivery_lock
from tools.delivery_supervisor import supervisor_lock


@pytest.fixture
def setup(tmp_path):
    state, public = tmp_path / 'private', tmp_path / 'public'
    state.mkdir()
    public.mkdir()
    journal, destination = state / 'delivery.json', public / 'delivery.json'
    journal.write_text(json.dumps(receipt()))
    journal.chmod(0o600)
    with patch('tools.delivery_publication._protected'), patch('tools.delivery_entrypoint._protected'):
        yield DeliveryPublication(state, destination, owner=os.getuid()), journal, destination


@pytest.mark.parametrize('phase', ['STARTED', 'PREPARING', 'CUTOVER_PENDING', 'RECOVERING',
    'RECOVERY_REQUIRED', 'FAILED', 'SUCCEEDED', 'ROLLED_BACK'])
def test_public_projection_preserves_all_phases(phase):
    raw = receipt() | dict(status=phase)
    if phase == 'ROLLED_BACK':
        raw.update(deployed_release=OLD, scheduler_target=OLD, rollback_validation=acceptance(OLD))
    result = public_receipt(raw)
    assert SECRET not in json.dumps(result)
    assert project_delivery(result) == project_delivery(raw)


@pytest.mark.parametrize('field', ['tests', 'candidate_validation', 'production_validation',
    'origin_sync', 'scheduler_target', 'deployed_release', 'source_head', 'live_money'])
def test_missing_gates_never_upgrade(field):
    raw = receipt()
    raw.pop(field)
    assert project_delivery(public_receipt(raw)) == project_delivery(raw)


def test_publish_to_api_preserves_private_source(setup, monkeypatch):
    publisher, journal, destination = setup
    before = journal.read_bytes()
    result = publisher.publish()
    assert journal.read_bytes() == before
    assert SECRET not in destination.read_text()
    assert destination.stat().st_mode & 0o777 == 0o644
    assert json.loads(destination.read_text()) == result
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(destination))
    assert load_delivery_status() == project_delivery(receipt())


@pytest.mark.parametrize('payload', [b'{', b'[]', b'{"schema":1,"schema":2}', b'x' * 1048577], ids=['malformed', 'array', 'duplicate', 'oversized'])
def test_invalid_source_clears_old_success(setup, payload):
    publisher, journal, destination = setup
    publisher.publish()
    journal.write_bytes(payload)
    try:
        publisher.publish()
    except DeliveryError:
        pass
    assert project_delivery(json.loads(destination.read_text()))['status'] == 'UNAVAILABLE'


def test_missing_source_clears_old_success(setup):
    publisher, journal, destination = setup
    publisher.publish()
    journal.unlink()
    with pytest.raises(DeliveryError):
        publisher.publish()
    assert json.loads(destination.read_text()) == {}


@pytest.mark.parametrize('lock', [supervisor_lock, delivery_lock])
def test_concurrent_operation_rejected(setup, lock):
    publisher, journal, destination = setup
    publisher.publish()
    before = destination.read_bytes()
    with lock(publisher.state), pytest.raises(DeliveryError):
        publisher.publish()
    assert destination.read_bytes() == before


def test_disjoint_roots_required(setup):
    publisher, _, _ = setup
    with pytest.raises(DeliveryError, match='separate'):
        DeliveryPublication(publisher.state, publisher.state / 'public.json', owner=os.getuid())


def test_identity_required(setup):
    publisher, _, _ = setup
    publisher.owner += 1
    with pytest.raises(DeliveryError, match='identity'):
        publisher.publish()


def test_duplicate_public_keys_rejected(setup, monkeypatch):
    _, _, destination = setup
    destination.write_text('{"schema":1,"schema":2}')
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(destination))
    assert load_delivery_status()['status'] == 'UNAVAILABLE'


def test_atomic_replace_failure_preserves_previous_complete_document(setup):
    publisher, _, destination = setup
    publisher.publish()
    before = destination.read_bytes()
    with patch('tools.delivery_runner.os.replace', side_effect=OSError('write failed')):
        with pytest.raises(OSError):
            publisher.publish()
    assert destination.read_bytes() == before
    assert list(destination.parent.iterdir()) == [destination]


def test_unsafe_journal_permissions_clear_success(setup):
    publisher, journal, destination = setup
    publisher.publish()
    journal.chmod(0o666)
    with pytest.raises(DeliveryError):
        publisher.publish()
    assert json.loads(destination.read_text()) == {}


def test_symlink_journal_is_not_followed(setup):
    publisher, journal, destination = setup
    publisher.publish()
    journal.unlink()
    journal.symlink_to(destination)
    with pytest.raises(DeliveryError):
        publisher.publish()
    assert json.loads(destination.read_text()) == {}


def test_destination_protection_failure_prevents_write(setup):
    publisher, _, destination = setup
    publisher.publish()
    before = destination.read_bytes()
    with patch('tools.delivery_publication._protected', side_effect=DeliveryError('unsafe')):
        with pytest.raises(DeliveryError):
            publisher.publish()
    assert destination.read_bytes() == before
