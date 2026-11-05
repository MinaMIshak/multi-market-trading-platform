"""Artificial completion callbacks; no installed-host acceptance."""
from datetime import timedelta, timezone
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


def test_retention_survives_operation_leases_and_settles_afterwards(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    from tools.delivery_runner import LeaseContention
    from tools.delivery_supervisor import supervisor_lock
    publisher = setup[0]
    with supervisor_lock(publisher.state), delivery_lock(publisher.state):
        path, digest = retain_completion(publisher, **event())
        assert path.stat().st_mode & 0o777 == 0o400
        # Only retention is lease-free; advancing observations stays excluded.
        with pytest.raises(LeaseContention):
            record_completion(publisher, path, digest)
        with pytest.raises(LeaseContention):
            recover_completions(publisher)
    assert not (publisher.state / 'observations.json').exists()
    result = recover_completions(publisher)
    assert result['observations']['latest_codex_cycle']['evidence_sha256'] == digest
    assert not path.exists() and archived(publisher, path).exists()


def test_busy_spool_fails_closed_without_writes(setup):
    from tools.delivery_observations import _spool_lock, retain_completion, recover_completions
    publisher = setup[0]
    pending, _ = retain_completion(publisher, **event())
    with patch('tools.delivery_observations.SPOOL_WAIT', 0), _spool_lock(publisher.state):
        with pytest.raises(DeliveryError, match='spool busy'):
            retain_completion(publisher, **event(event_id='retained-2', observed_at='2026-11-04T00:00:00+00:00'))
        with pytest.raises(DeliveryError, match='spool busy'):
            recover_completions(publisher)
        with pytest.raises(DeliveryError, match='spool busy'):
            record_completion(publisher, pending, hashlib.sha256(pending.read_bytes()).hexdigest())
    assert [p.name for p in publisher.state.glob('completion-*.json')] == [pending.name]
    assert not list(publisher.state.glob('observation-*.json'))
    assert not (publisher.state / 'observations.json').exists()


def test_spool_waits_for_short_holder(setup):
    import threading
    from tools.delivery_observations import _spool_lock, retain_completion
    publisher = setup[0]
    held, release, outcome = threading.Event(), threading.Event(), []
    def holder():
        with _spool_lock(publisher.state):
            held.set()
            release.wait(10)
    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(10)
    threading.Timer(0.2, release.set).start()
    outcome.append(retain_completion(publisher, **event()))
    thread.join(10)
    assert outcome[0][0].exists()


def test_damaged_sidecar_without_retained_work_is_left_to_publication(setup):
    from tools.delivery_observations import recover_completions
    publisher = setup[0]
    (publisher.state / 'observations.json').write_bytes(b'{')
    assert 'observations' not in recover_completions(publisher)


def test_retention_symlink_rejection(setup):
    from tools.delivery_observations import retain_completion
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
    # Settled envelopes leave the scan, so conflict needs a still-retained event.
    retain_completion(publisher, **event(event_id='retained-2', observed_at='2026-11-04T00:00:00+00:00'))
    receipt = publisher.state / 'observation-latest_codex_cycle-retained-2.json'
    receipt.write_text('{}')
    receipt.chmod(0o600)
    with pytest.raises(DeliveryError, match='identity conflict'):
        recover_completions(publisher)
    assert not (publisher.state / 'observations.json').exists()
    assert len(list(publisher.state.glob('completion-*.json'))) == 2


def test_interrupted_cycle_reportable_while_writer_latched(setup):
    from app.delivery_status import project_delivery
    from tools.delivery_observations import retain_completion
    publisher = setup[0]
    marker = publisher.state / 'writer-active.json'
    marker.write_text('{"schema": "writer-active-v1"}')
    before = marker.read_bytes()
    path, digest = retain_completion(publisher, **event(status='INTERRUPTED'))
    result = record_completion(publisher, path, digest)
    assert result['observations']['latest_codex_cycle']['status'] == 'INTERRUPTED'
    assert project_delivery(result)['blocker'] == 'WRITER_RECONCILIATION_REQUIRED'
    assert marker.read_bytes() == before


def test_retained_events_recoverable_while_writer_latched(setup):
    from app.delivery_status import project_delivery
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    retain_completion(publisher, **event(status='INTERRUPTED'))
    marker = publisher.state / 'writer-active.json'
    marker.write_text('{"schema": "writer-active-v1"}')
    result = recover_completions(publisher)
    assert result['observations']['latest_codex_cycle']['status'] == 'INTERRUPTED'
    assert project_delivery(result)['blocker'] == 'WRITER_RECONCILIATION_REQUIRED'
    assert marker.exists()


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
    # The settled envelope no longer counts, so 129 unsettled files exceed the bound.
    for i in range(129):
        (publisher.state / f'completion-extra-{i}.json').write_text('{}')
    with pytest.raises(DeliveryError, match='capacity'):
        recover_completions(publisher)


def archived(publisher, path):
    return publisher.state / 'completion-archive' / path.name


def test_settled_events_never_reach_recovery_capacity(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    for i in range(130):
        moment = (NOW - timedelta(seconds=1000 - i)).isoformat()
        path, digest = retain_completion(publisher, **event(event_id=f'cycle-{i}', observed_at=moment))
        record_completion(publisher, path, digest)
    assert not list(publisher.state.glob('completion-*.json'))
    assert len(list((publisher.state / 'completion-archive').iterdir())) == 130
    assert len(list(publisher.state.glob('observation-*.json'))) == 130
    result = recover_completions(publisher)
    assert result['observations']['latest_codex_cycle']['observed_at'] == moment


def test_archived_identity_retries_and_cannot_be_rewritten(setup):
    from tools.delivery_observations import retain_completion
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    raw = path.read_bytes()
    result = record_completion(publisher, path, digest)
    assert not path.exists()
    assert archived(publisher, path).read_bytes() == raw
    assert archived(publisher, path).stat().st_mode & 0o777 == 0o400
    with pytest.raises(DeliveryError, match='identity conflict'):
        retain_completion(publisher, **event(status='FAILED'))
    assert not path.exists()
    assert retain_completion(publisher, **event()) == (archived(publisher, path), digest)
    assert record_completion(publisher, archived(publisher, path), digest) == result
    assert archived(publisher, path).read_bytes() == raw


def test_failed_publication_keeps_envelope_for_recovery(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    with patch.object(publisher, 'publish', side_effect=OSError('unavailable')):
        with pytest.raises(OSError):
            record_completion(publisher, path, digest)
        with pytest.raises(OSError):
            recover_completions(publisher)
    assert path.exists()
    recover_completions(publisher)
    assert not path.exists() and archived(publisher, path).exists()


def test_superseded_event_stays_until_recovery_settles_it(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    record_completion(publisher, *retain_completion(publisher, **event(event_id='newer')))
    path, digest = retain_completion(publisher, **event(event_id='older', observed_at='2026-11-04T00:00:00+00:00'))
    with pytest.raises(DeliveryError, match='stale'):
        record_completion(publisher, path, digest)
    assert path.exists()
    result = recover_completions(publisher)
    assert result['observations']['latest_codex_cycle']['observed_at'] == NOW.isoformat()
    assert not path.exists() and archived(publisher, path).exists()


def test_archive_failure_does_not_undo_publication(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    with patch('tools.delivery_observations.os.rename', side_effect=OSError('disk')):
        result = record_completion(publisher, path, digest)
    assert result['observations']['latest_codex_cycle']['evidence_sha256'] == digest
    assert path.exists()
    assert recover_completions(publisher) == result
    assert not path.exists() and archived(publisher, path).exists()


def test_receipt_mismatch_blocks_archive(setup):
    from tools.delivery_observations import retain_completion, _archive_settled
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    with patch('tools.delivery_observations.os.rename', side_effect=OSError('disk')):
        record_completion(publisher, path, digest)
    receipt = publisher.state / 'observation-latest_codex_cycle-retained-1.json'
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(json.loads(receipt.read_bytes()) | dict(evidence_hex='00')))
    _archive_settled(publisher, [path])
    assert path.exists() and not archived(publisher, path).exists()


def test_event_newer_than_sidecar_is_not_archived(setup):
    from tools.delivery_observations import retain_completion, _archive_settled
    publisher = setup[0]
    path, digest = retain_completion(publisher, **event())
    with patch('tools.delivery_observations.os.rename', side_effect=OSError('disk')):
        record_completion(publisher, path, digest)
    sidecar = publisher.state / 'observations.json'
    current = json.loads(sidecar.read_bytes())
    current['latest_codex_cycle']['observed_at'] = '2026-01-01T00:00:00+00:00'
    sidecar.write_text(json.dumps(current))
    _archive_settled(publisher, [path])
    assert path.exists() and not archived(publisher, path).exists()


def test_unsafe_archive_root_keeps_envelope(setup):
    from tools.delivery_observations import retain_completion
    publisher = setup[0]
    root = publisher.state / 'completion-archive'
    root.mkdir()
    root.chmod(0o777)
    path, digest = retain_completion(publisher, **event())
    record_completion(publisher, path, digest)
    assert path.exists() and not list(root.iterdir())


def test_equal_time_event_rejected_before_retention(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    first = retain_completion(publisher, **event())
    # Equal instants conflict even when written with a different UTC offset.
    same = NOW.astimezone(timezone(timedelta(hours=2))).isoformat()
    with pytest.raises(DeliveryError, match='time conflict'):
        retain_completion(publisher, **event(event_id='retained-2', observed_at=same))
    record_completion(publisher, *first)
    with pytest.raises(DeliveryError, match='time conflict'):
        retain_completion(publisher, **event(event_id='retained-3', status='FAILED'))
    assert not list(publisher.state.glob('completion-*.json'))
    # Recovery stays available; the same instant in another kind is independent.
    assert retain_completion(publisher, **event(event_id='retained-3', kind='us_chain_status', status='COMPLETED'))
    assert 'us_chain_status' in recover_completions(publisher)['observations']


def test_recovery_still_rejects_conflict_placed_outside_retention(setup):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    path, _ = retain_completion(publisher, **event())
    raw = json.loads(path.read_bytes()) | dict(event_id='retained-2', status='FAILED')
    other = publisher.state / 'completion-latest_codex_cycle-retained-2.json'
    other.write_text(json.dumps(raw))
    other.chmod(0o600)
    with pytest.raises(DeliveryError, match='time conflict'):
        recover_completions(publisher)
    assert not list(publisher.state.glob('observation-*.json'))
    assert not (publisher.state / 'observations.json').exists()


@pytest.mark.parametrize('payload', [b'{', b'{"schema":1,"schema":2}', b'[]', b'\xff'])
def test_damaged_sidecar_cannot_stop_retention_and_reports_typed_error(setup, payload):
    from tools.delivery_observations import retain_completion, recover_completions
    publisher = setup[0]
    (publisher.state / 'observations.json').write_bytes(payload)
    (publisher.state / 'observations.json').chmod(0o600)
    path, digest = retain_completion(publisher, **event())
    assert path.exists()
    for attempt in (lambda: record_completion(publisher, path, digest),
                    lambda: recover_completions(publisher)):
        with pytest.raises(DeliveryError, match='existing observations'):
            attempt()
    assert path.exists()
