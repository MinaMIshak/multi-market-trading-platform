"""Temporary publication fixtures; no installed-host acceptance claims."""
import json
import os
from unittest.mock import patch

import pytest

from app.delivery_status import public_receipt, project_delivery, load_delivery_status
from tests.test_delivery_status import receipt, acceptance, OLD, SECRET
from tools.delivery_publication import DeliveryPublication
from tools.delivery_runner import DeliveryError, delivery_lock, _delivery_lease
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
    assert load_delivery_status() == project_delivery(receipt() | {"settlement_backlog": result["settlement_backlog"]})


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


LATCHED = 'WRITER_RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('phase', ['STARTED', 'PREPARING', 'CUTOVER_PENDING', 'RECOVERING',
    'RECOVERY_REQUIRED', 'FAILED', 'SUCCEEDED', 'ROLLED_BACK'])
def test_writer_latch_survives_projection_and_outranks_phase_blocker(phase):
    raw = receipt() | dict(status=phase, writer_reconciliation_required=True)
    assert project_delivery(raw)['blocker'] == LATCHED
    assert project_delivery(public_receipt(raw)) == project_delivery(raw)


@pytest.mark.parametrize('value', [False, 1, 'true', [], None])
def test_only_boolean_true_is_a_writer_latch(value):
    raw = receipt() | dict(writer_reconciliation_required=value)
    assert project_delivery(raw)['blocker'] is None
    assert 'writer_reconciliation_required' not in public_receipt(raw)


def test_unrecognized_receipt_keeps_latch_without_other_claims():
    raw = dict(schema='bad', writer_reconciliation_required=True)
    assert project_delivery(raw)['status'] == 'UNAVAILABLE'
    assert project_delivery(public_receipt(raw)) == project_delivery(raw)


def test_writer_latch_is_published_and_retained(setup, monkeypatch):
    publisher, journal, destination = setup
    publisher.publish()
    marker = publisher.state / 'writer-active.json'
    marker.write_text('{"schema": "writer-active-v1"}')
    before = marker.read_bytes()
    result = publisher.publish()
    assert project_delivery(result)['blocker'] == LATCHED
    assert marker.read_bytes() == before
    assert SECRET not in destination.read_text()
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(destination))
    assert load_delivery_status()['blocker'] == LATCHED
    # A later reconciled state clears the blocker on the next publication.
    marker.unlink()
    assert project_delivery(publisher.publish())['blocker'] is None
    assert load_delivery_status()['blocker'] is None


def test_dangling_writer_latch_still_published(setup):
    publisher, _, _ = setup
    (publisher.state / 'writer-active.json').symlink_to(publisher.state / 'absent')
    assert project_delivery(publisher.publish())['blocker'] == LATCHED


def test_journal_cannot_forge_writer_latch(setup):
    publisher, journal, destination = setup
    journal.write_text(json.dumps(receipt() | dict(writer_reconciliation_required=True)))
    assert project_delivery(publisher.publish())['blocker'] is None
    assert 'writer_reconciliation_required' not in destination.read_text()


def test_writer_latch_published_without_journal(setup):
    publisher, journal, destination = setup
    journal.unlink()
    (publisher.state / 'writer-active.json').write_text('{}')
    view = project_delivery(publisher.publish())
    assert view['blocker'] == LATCHED and view['status'] == 'UNAVAILABLE'
    assert view['deployed_release'] is None
    assert project_delivery(json.loads(destination.read_text())) == view


@pytest.mark.parametrize('payload,unreadable', [(b'{', True), (b'[]', False), (b'x' * 1048577, True)],
                         ids=['malformed', 'array', 'oversized'])
def test_writer_latch_survives_invalid_journal(setup, payload, unreadable):
    publisher, journal, destination = setup
    publisher.publish()
    journal.write_bytes(payload)
    (publisher.state / 'writer-active.json').write_text('{}')
    if unreadable:
        with pytest.raises(DeliveryError, match='source unavailable'):
            publisher.publish()
    else:
        publisher.publish()
    view = project_delivery(json.loads(destination.read_text()))
    assert view['blocker'] == LATCHED and view['status'] == 'UNAVAILABLE'
    assert view['deployed_release'] is None


@pytest.mark.parametrize('lock', [supervisor_lock, _delivery_lease])
def test_writer_latch_never_bypasses_live_lease(setup, lock):
    publisher, _, destination = setup
    publisher.publish()
    before = destination.read_bytes()
    (publisher.state / 'writer-active.json').write_text('{}')
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
