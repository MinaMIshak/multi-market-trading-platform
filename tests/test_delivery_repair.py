"""Artificial protected files only; no host repair or semantic attestation."""
import hashlib
import json
from unittest.mock import patch

import pytest

from tests.test_delivery_publication import setup
from tools.delivery_repair import plan_repair
from tools.delivery_runner import DeliveryError, _delivery_lease
from tools.delivery_supervisor import supervisor_lock


def pin(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(publisher, tmp_path):
    damaged = publisher.state / 'completion-damaged.json'
    damaged.write_bytes(b'{malformed original')
    damaged.chmod(0o400)
    origin = tmp_path / 'origin-job.json'
    origin.write_bytes(b'independent job record fixture')
    origin.chmod(0o400)
    path = tmp_path / 'repair-request.json'
    raw = dict(schema='delivery-repair-request-v1', live_money=False,
               references={damaged.name: pin(damaged)}, originating_records={str(origin): pin(origin)})
    path.write_text(json.dumps(raw))
    path.chmod(0o400)
    return path, damaged, origin


def test_deterministic_read_only_plan_preserves_damage(setup, tmp_path):
    publisher, journal, public = setup
    path, damaged, origin = request(publisher, tmp_path)
    before = {p: p.read_bytes() for p in (path, damaged, origin, journal)}
    first = plan_repair(publisher, path, pin(path))
    assert first == plan_repair(publisher, path, pin(path))
    plan, digest = first
    assert len(digest) == 64 and plan['mutation_authorized'] is False
    assert plan['pending'] == [damaged.name]
    assert plan['records']['observations.json'] is None
    assert plan['live_money'] is False
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not public.exists()
    origin.chmod(0o600)
    origin.write_bytes(b'changed job record')
    with pytest.raises(DeliveryError, match='drift'):
        plan_repair(publisher, path, pin(path))


@pytest.mark.parametrize('failure', ['request_pin', 'evidence_pin', 'live', 'no_origin',
    'traversal', 'receiver_origin', 'symlink', 'unsafe', 'capacity', 'duplicate'])
def test_invalid_plans_fail_closed(setup, tmp_path, failure):
    publisher, _, _ = setup
    path, damaged, origin = request(publisher, tmp_path)
    raw = json.loads(path.read_text())
    if failure == 'evidence_pin': raw['references'][damaged.name] = '0' * 64
    if failure == 'live': raw['live_money'] = True
    if failure == 'no_origin': raw['originating_records'] = {}
    if failure == 'traversal': raw['references'] = {'../origin-job.json': pin(origin)}
    if failure == 'receiver_origin': raw['originating_records'] = {str(damaged): pin(damaged)}
    if failure == 'symlink':
        damaged.unlink()
        damaged.symlink_to(origin)
    if failure == 'unsafe': damaged.chmod(0o666)
    path.chmod(0o600)
    path.write_text(json.dumps(raw))
    if failure == 'duplicate': path.write_text('{"schema":1,"schema":2}')
    digest = '0' * 64 if failure == 'request_pin' else pin(path)
    with patch('tools.delivery_repair.MAX_PENDING_BYTES', 0 if failure == 'capacity' else 8*1024*1024):
        with pytest.raises((DeliveryError, OSError)):
            plan_repair(publisher, path, digest)
    assert damaged.exists() and origin.exists()


@pytest.mark.parametrize('lease', [supervisor_lock, _delivery_lease])
def test_concurrent_operation_rejected(setup, tmp_path, lease):
    publisher, _, _ = setup
    path, damaged, _ = request(publisher, tmp_path)
    with lease(publisher.state), pytest.raises(DeliveryError):
        plan_repair(publisher, path, pin(path))
    assert damaged.read_bytes() == b'{malformed original'


def test_inventory_change_changes_plan_without_resolving_conflict(setup, tmp_path):
    publisher, _, _ = setup
    path, _, _ = request(publisher, tmp_path)
    _, before = plan_repair(publisher, path, pin(path))
    extra = publisher.state / 'completion-other.json'
    extra.write_bytes(b'conflicting legacy evidence')
    extra.chmod(0o400)
    plan, after = plan_repair(publisher, path, pin(path))
    assert after != before and extra.name in plan['pending']
    assert plan['disposition'] == 'UNRESOLVED_REQUIRES_ORIGIN_RECONCILIATION'


def test_archive_receipt_and_damaged_sidecar_are_preserved(setup, tmp_path):
    publisher, _, _ = setup
    path, damaged, _ = request(publisher, tmp_path)
    archive = publisher.state / 'completion-archive'
    archive.mkdir(mode=0o700)
    archived = archive / damaged.name
    archived.write_bytes(b'conflicting archive bytes')
    receipt = publisher.state / 'observation-old.json'
    receipt.write_bytes(b'damaged receipt')
    sidecar = publisher.state / 'observations.json'
    sidecar.write_bytes(b'damaged sidecar')
    for protected in (archived, receipt, sidecar):
        protected.chmod(0o600)
    raw = json.loads(path.read_text())
    raw['references'].update({str(p.relative_to(publisher.state)): pin(p)
                              for p in (archived, receipt)})
    path.chmod(0o600)
    path.write_text(json.dumps(raw))
    before = {p: p.read_bytes() for p in (archived, receipt, sidecar, damaged)}
    plan, _ = plan_repair(publisher, path, pin(path))
    assert all(p.read_bytes() == value for p, value in before.items())
    assert plan['records']['observations.json']['sha256'] == pin(sidecar)
    assert 'completion-archive/' + damaged.name in plan['records']


def test_drift_during_planning_rejected(setup, tmp_path):
    publisher, _, _ = setup
    path, damaged, _ = request(publisher, tmp_path)
    from tools.delivery_entrypoint import protected_bytes
    calls = 0

    def drifting(target, owner):
        nonlocal calls
        data = protected_bytes(target, owner)
        if target == damaged:
            calls += 1
            if calls == 2:
                damaged.chmod(0o600)
                damaged.write_bytes(b'changed during snapshot')
        return data

    with patch('tools.delivery_repair.protected_bytes', side_effect=drifting):
        with pytest.raises(DeliveryError, match='drift'):
            plan_repair(publisher, path, pin(path))
