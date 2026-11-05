"""Artificial host events only; no runtime evidence."""
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.delivery_status import project_delivery, public_receipt
from tests.test_delivery_publication import setup
from tests.test_delivery_observations import NOW
from tests.test_delivery_status import HEAD
from tools.delivery_observations import ObservationProducer
from tools.delivery_runner import DeliveryError, delivery_lock
from tools.delivery_supervisor import supervisor_lock


def ingest(setup, **overrides):
    publisher, _, _ = setup
    evidence = publisher.state / 'event.log'
    if not evidence.exists():
        evidence.write_bytes(b'private artificial event\n')
        evidence.chmod(0o600)
    args = dict(event_id='cycle-1', source_head=HEAD, status='SUCCEEDED',
                observed_at=NOW.isoformat(), evidence_path=evidence, live_money=False, now=NOW)
    args.update(overrides)
    return ObservationProducer(publisher).record('latest_codex_cycle', **args)


def test_archive_identity_and_pre_delivery_publication(setup):
    publisher, journal, destination = setup
    journal.unlink()
    result = ingest(setup)
    view = project_delivery(result, now=NOW)
    assert view['status'] == 'UNAVAILABLE'
    assert view['deployed_release'] is None and view['tests'] is None
    assert view['latest_codex_cycle']['freshness'] == 'RECENT'
    archive = json.loads(next(publisher.state.glob('observation-*.json')).read_text())
    evidence = bytes.fromhex(archive['evidence_hex'])
    assert archive['record']['evidence_sha256'] == hashlib.sha256(evidence).hexdigest()
    assert archive['event_id'] == 'cycle-1'
    assert 'private artificial' not in destination.read_text()
    assert public_receipt(result) == result
    assert ingest(setup) == result


@pytest.mark.parametrize('overrides', [dict(live_money=True), dict(live_money=0),
    dict(source_head='bad'), dict(status='GREEN'), dict(event_id='../bad'),
    dict(observed_at=(NOW + timedelta(seconds=1)).isoformat())])
def test_invalid_event_never_advances(setup, overrides):
    with pytest.raises(DeliveryError):
        ingest(setup, **overrides)
    assert not (setup[0].state / 'observations.json').exists()


def test_replay_conflict_and_stale_event(setup):
    ingest(setup)
    sidecar = setup[0].state / 'observations.json'
    before = sidecar.read_bytes()
    with pytest.raises(DeliveryError, match='identity conflict'):
        ingest(setup, status='FAILED')
    with pytest.raises(DeliveryError, match='stale'):
        ingest(setup, event_id='older', observed_at=(NOW-timedelta(seconds=1)).isoformat())
    assert sidecar.read_bytes() == before


@pytest.mark.parametrize('lock', [delivery_lock, supervisor_lock])
def test_concurrent_producer_rejected(setup, lock):
    with lock(setup[0].state), pytest.raises(DeliveryError):
        ingest(setup)
    assert not (setup[0].state / 'observations.json').exists()


def test_archive_survives_sidecar_failure_and_retry(setup):
    from tools.delivery_runner import atomic_json
    def fail_sidecar(path, *args, **kwargs):
        if path.name == 'observations.json':
            raise OSError('simulated failure')
        return atomic_json(path, *args, **kwargs)
    with patch('tools.delivery_observations.atomic_json', side_effect=fail_sidecar), pytest.raises(OSError):
        ingest(setup)
    assert len(list(setup[0].state.glob('observation-*.json'))) == 1
    assert ingest(setup)['observations']['latest_codex_cycle']['status'] == 'SUCCEEDED'


def test_unsafe_evidence_and_corrupt_sidecar_rejected(setup):
    ingest(setup)
    publisher = setup[0]
    evidence = publisher.state / 'event.log'
    evidence.chmod(0o666)
    with pytest.raises(DeliveryError):
        ingest(setup)
    evidence.chmod(0o600)
    (publisher.state / 'observations.json').write_text('{}')
    with pytest.raises(DeliveryError, match='existing'):
        ingest(setup)


def test_independent_events_preserved_and_equal_time_conflict(setup):
    ingest(setup)
    publisher = setup[0]
    result = ObservationProducer(publisher).record('us_chain_status', event_id='chain-1',
        source_head=HEAD, status='COMPLETED', observed_at=NOW.isoformat(),
        evidence_path=publisher.state / 'event.log', live_money=False, now=NOW)
    assert set(result['observations']) == {'schema', 'latest_codex_cycle', 'us_chain_status'}
    with pytest.raises(DeliveryError, match='time conflict'):
        ingest(setup, event_id='cycle-2', status='FAILED')


def test_evidence_mutation_conflicts_with_preserved_event(setup):
    ingest(setup)
    (setup[0].state / 'event.log').write_text('changed evidence')
    with pytest.raises(DeliveryError, match='identity conflict'):
        ingest(setup)
