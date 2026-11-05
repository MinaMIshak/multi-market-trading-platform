"""Artificial completion callbacks; no installed-host acceptance."""
import hashlib
import json
from unittest.mock import patch

import pytest

from tests.test_delivery_publication import setup
from tests.test_delivery_observations import NOW
from tests.test_delivery_status import HEAD
from tools.delivery_observations import record_completion
from tools.delivery_runner import DeliveryError, delivery_lock


def envelope(setup, **changes):
    event = dict(schema='delivery-completion-v1', kind='latest_codex_cycle',
                 event_id='cycle-1', source_head=HEAD, status='SUCCEEDED',
                 observed_at=NOW.isoformat(), live_money=False, evidence='artificial exit=0')
    event.update(changes)
    path = setup[0].state / 'completion.json'
    path.write_text(json.dumps(event))
    path.chmod(0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('kind,status', [('latest_codex_cycle', 'INTERRUPTED'),
    ('latest_claude_review', 'COMPLETED'), ('egx_chain_status', 'FAILED'),
    ('us_chain_status', 'COMPLETED')])
def test_callback_retains_exact_envelope_and_replays(setup, kind, status):
    path, digest = envelope(setup, kind=kind, status=status)
    result = record_completion(setup[0], path, digest)
    assert result['observations'][kind]['evidence_sha256'] == digest
    assert record_completion(setup[0], path, digest) == result
    receipt = json.loads(next(setup[0].state.glob('observation-*.json')).read_bytes())
    assert bytes.fromhex(receipt['evidence_hex']) == path.read_bytes()


@pytest.mark.parametrize('changes', [dict(schema='bad'), dict(command=['sh']),
    dict(evidence=' '), dict(evidence={}), dict(live_money=True),
    dict(kind='tests'), dict(kind=[]), dict(source_head='bad'), dict(status='GREEN')])
def test_invalid_callback_cannot_advance(setup, changes):
    with pytest.raises(DeliveryError):
        record_completion(setup[0], *envelope(setup, **changes))
    assert not (setup[0].state / 'observations.json').exists()


@pytest.mark.parametrize('raw', [b'{', b'[]', b'{"schema":1,"schema":2}', b'\xff'])
def test_malformed_envelope(setup, raw):
    path, _ = envelope(setup)
    path.write_bytes(raw)
    with pytest.raises(DeliveryError):
        record_completion(setup[0], path, hashlib.sha256(raw).hexdigest())


def test_pin_checked_again_under_lease(setup):
    path, digest = envelope(setup)
    from tools.delivery_observations import protected_bytes
    def mutate_after_read(*args):
        raw = protected_bytes(*args)
        path.write_bytes(raw + b' ')
        return raw
    with patch('tools.delivery_observations.protected_bytes', side_effect=mutate_after_read):
        with pytest.raises(DeliveryError, match='pin mismatch'):
            record_completion(setup[0], path, digest)
    assert not list(setup[0].state.glob('observation-*.json'))


def test_concurrent_callback_and_wrong_pin(setup):
    path, digest = envelope(setup)
    with pytest.raises(DeliveryError, match='pin mismatch'):
        record_completion(setup[0], path, '0' * 64)
    with delivery_lock(setup[0].state), pytest.raises(DeliveryError):
        record_completion(setup[0], path, digest)


def event(**changes):
    return dict(kind='latest_codex_cycle', event_id='retained-1', source_head=HEAD,
                status='SUCCEEDED', observed_at=NOW.isoformat(), live_money=False,
                evidence='artificial host completion') | changes


def test_retention_survives_callback_failure_and_restarts(setup):
    from tools.delivery_observations import retain_completion
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    raw = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o400
    with patch.object(publisher, 'publish', side_effect=OSError('publication unavailable')):
        with pytest.raises(OSError):
            record_completion(publisher, path, digest)
    assert path.read_bytes() == raw
    assert retain_completion(publisher, **event()) == (path, digest)
    result = record_completion(publisher, path, digest)
    assert result['observations']['latest_codex_cycle']['evidence_sha256'] == digest
    assert b'artificial host completion' not in setup[2].read_bytes()


@pytest.mark.parametrize('changes', [dict(evidence='changed'), dict(status='FAILED'),
    dict(source_head='b' * 40), dict(observed_at='2026-01-01T00:00:00+00:00')])
def test_retained_identity_cannot_be_rewritten(setup, changes):
    from tools.delivery_observations import retain_completion
    path, _ = retain_completion(setup[0], **event())
    raw = path.read_bytes()
    with pytest.raises(DeliveryError, match='identity conflict'):
        retain_completion(setup[0], **event(**changes))
    assert path.read_bytes() == raw


@pytest.mark.parametrize('changes', [dict(live_money=True), dict(kind='../bad'),
    dict(event_id='../bad'), dict(evidence=''), dict(evidence='x' * 500000),
    dict(source_head='bad'), dict(status='GREEN'),
    dict(observed_at='2999-01-01T00:00:00+00:00')])
def test_invalid_retention_leaves_no_envelope(setup, changes):
    from tools.delivery_observations import retain_completion
    with pytest.raises(DeliveryError):
        retain_completion(setup[0], **event(**changes))
    assert not list(setup[0].state.glob('completion-*.json'))


def test_retention_lock_and_symlink_rejection(setup):
    from tools.delivery_observations import retain_completion
    with delivery_lock(setup[0].state), pytest.raises(DeliveryError):
        retain_completion(setup[0], **event())
    path, _ = retain_completion(setup[0], **event())
    target = path.with_suffix('.saved')
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises((DeliveryError, OSError)):
        retain_completion(setup[0], **event())


def test_recovery_retains_history_selects_latest_and_retries_publication(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    old = event(event_id='z-old', observed_at='2026-11-04T00:00:00+00:00')
    new = event(event_id='a-new')
    for item in (old, new, event(kind='us_chain_status', status='COMPLETED')):
        retain_completion(publisher, **item)
    with patch.object(publisher, 'publish', side_effect=OSError('unavailable')):
        with pytest.raises(OSError):
            recover_completions(publisher)
    assert len(list(publisher.state.glob('observation-*.json'))) == 3
    result = recover_completions(publisher)
    assert result['observations']['latest_codex_cycle']['observed_at'] == new['observed_at']
    assert recover_completions(publisher) == result
    assert 'artificial host completion' not in setup[2].read_text()


@pytest.mark.parametrize('damage', ['malformed', 'symlink', 'unsafe', 'filename', 'live', 'future', 'conflict'])
def test_recovery_validates_whole_batch_before_writing(setup, damage):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    retain_completion(publisher, **event(event_id='a-valid'))
    path, _ = retain_completion(publisher, **event(event_id='z-other', kind='us_chain_status', status='COMPLETED'))
    path.chmod(0o600)
    if damage == 'malformed':
        path.write_text('{')
    elif damage == 'symlink':
        target = path.with_suffix('.saved')
        path.rename(target)
        path.symlink_to(target)
    elif damage == 'unsafe':
        path.chmod(0o666)
    elif damage == 'filename':
        path.rename(path.with_name('completion-wrong.json'))
    else:
        raw = json.loads(path.read_bytes())
        if damage == 'live':
            raw['live_money'] = True
        elif damage == 'future':
            raw['observed_at'] = '2999-01-01T00:00:00+00:00'
        else:
            raw['kind'] = 'latest_codex_cycle'
            path.unlink()
            path = publisher.state / 'completion-latest_codex_cycle-z-other.json'
        path.write_text(json.dumps(raw))
    with pytest.raises((DeliveryError, ValueError, OSError)):
        recover_completions(publisher)
    assert not list(publisher.state.glob('observation-*.json'))
    assert not (publisher.state / 'observations.json').exists()


def test_recovery_rejects_receipt_conflict_and_concurrency(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    retain_completion(publisher, **event())
    with delivery_lock(publisher.state), pytest.raises(DeliveryError):
        recover_completions(publisher)
    recover_completions(publisher)
    archive = next(publisher.state.glob('observation-*.json'))
    archive.chmod(0o600)
    archive.write_text('{}')
    before = (publisher.state / 'observations.json').read_bytes()
    with pytest.raises(DeliveryError, match='identity conflict'):
        recover_completions(publisher)
    assert (publisher.state / 'observations.json').read_bytes() == before


def test_recovery_capacity_and_partial_archive_restart(setup):
    from tools.delivery_observations import retain_completion, recover_completions, atomic_json
    publisher = setup[0]
    retain_completion(publisher, **event())
    def fail_sidecar(path, *args, **kwargs):
        if path.name == 'observations.json':
            raise OSError('interrupted')
        return atomic_json(path, *args, **kwargs)
    with patch('tools.delivery_observations.atomic_json', side_effect=fail_sidecar):
        with pytest.raises(OSError):
            recover_completions(publisher)
    assert len(list(publisher.state.glob('observation-*.json'))) == 1
    recover_completions(publisher)
    for i in range(128):
        (publisher.state / f'completion-extra-{i}.json').write_text('{}')
    with pytest.raises(DeliveryError, match='capacity'):
        recover_completions(publisher)
