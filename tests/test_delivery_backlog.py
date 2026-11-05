"""Receiver inventory fixtures only; no host health or settlement certification."""
import json
from unittest.mock import patch

import pytest

from app.delivery_status import project_delivery, public_receipt
from app.ui.system import render_delivery
from tests.test_delivery_publication import setup
from tests.test_delivery_completion import event
from tools.delivery_observations import retain_completion, recover_completions


def test_inventory_tracks_retention_archive_and_ui(setup):
    publisher, journal, destination = setup
    before = journal.read_bytes()
    path, _ = retain_completion(publisher, **event())
    exact = path.read_bytes()
    result = publisher.publish()
    view = project_delivery(result)
    snapshot = view['settlement_backlog']
    assert snapshot['pending_count'] == 1
    assert snapshot['pending_bytes'] == len(exact)
    assert snapshot['evidence_scope'] == 'RECEIVER_PENDING_INVENTORY_ONLY'
    assert path.read_bytes() == exact and journal.read_bytes() == before
    assert 'settlement backlog' in render_delivery(dict(delivery=view))
    assert 'artificial host completion' not in destination.read_text()
    assert public_receipt(result) == result
    recover_completions(publisher)
    assert publisher.publish()['settlement_backlog']['pending_count'] == 0
    assert (publisher.state / 'completion-archive' / path.name).read_bytes() == exact


@pytest.mark.parametrize('damage', ['symlink', 'permissions', 'count', 'bytes'])
def test_unreadable_or_over_capacity_is_unknown_preserves_evidence(setup, damage):
    publisher, _, destination = setup
    path, _ = retain_completion(publisher, **event())
    if damage == 'symlink':
        path.unlink()
        path.symlink_to(destination)
    elif damage == 'permissions':
        path.chmod(0o666)
    limit = 'MAX_PENDING_EVENTS' if damage == 'count' else 'MAX_PENDING_BYTES'
    with patch('tools.delivery_observations.' + limit, 0 if damage in ('count', 'bytes') else 8 * 1024 * 1024):
        result = publisher.publish()['settlement_backlog']
    assert result['status'] == 'UNAVAILABLE'
    assert 'pending_count' not in result and 'pending_bytes' not in result
    assert path.is_symlink() if damage == 'symlink' else path.exists()


def test_missing_or_damaged_journal_keeps_backlog(setup):
    publisher, journal, destination = setup
    path, _ = retain_completion(publisher, **event())
    journal.unlink()
    result = publisher.publish()
    assert project_delivery(result)['status'] == 'UNAVAILABLE'
    assert result['settlement_backlog']['pending_count'] == 1
    journal.write_text('{')
    from tools.delivery_runner import DeliveryError
    with pytest.raises(DeliveryError):
        publisher.publish()
    assert json.loads(destination.read_text())['settlement_backlog']['pending_count'] == 1
    assert path.exists()


def test_journal_cannot_inject_inventory(setup):
    publisher, journal, _ = setup
    raw = json.loads(journal.read_text())
    raw['settlement_backlog'] = dict(status='INVENTORIED', pending_count=99, private='secret')
    journal.write_text(json.dumps(raw))
    snapshot = publisher.publish()['settlement_backlog']
    assert snapshot['pending_count'] == 0 and 'private' not in snapshot


@pytest.mark.parametrize('value', [True, -1, 129, '1', None])
def test_invalid_counts_not_projected(value):
    raw = dict(schema='delivery-observation-publication-v1', settlement_backlog=dict(
        status='INVENTORIED', observed_at='2026-11-05T00:00:00+00:00',
        pending_count=value, pending_bytes=0))
    assert project_delivery(raw)['settlement_backlog'] is None


def test_inventory_does_not_certify_malformed_envelope(setup):
    publisher, _, _ = setup
    path = publisher.state / 'completion-damaged.json'
    path.write_bytes(b'{')
    path.chmod(0o400)
    result = publisher.publish()['settlement_backlog']
    assert result['status'] == 'INVENTORIED' and result['pending_bytes'] == 1
    from tools.delivery_runner import DeliveryError
    with pytest.raises(DeliveryError):
        recover_completions(publisher)
    assert path.read_bytes() == b'{'


def test_busy_spool_publishes_unknown_without_raw_error(setup):
    publisher, _, destination = setup
    from tools.delivery_runner import DeliveryError
    with patch('tools.delivery_observations._spool_lock', side_effect=DeliveryError('private path')):
        result = publisher.publish()['settlement_backlog']
    assert result['status'] == 'UNAVAILABLE'
    assert 'private path' not in destination.read_text()
