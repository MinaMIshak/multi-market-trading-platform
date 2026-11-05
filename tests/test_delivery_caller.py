"""Temporary-file handoff fixtures, not host installation evidence."""
import os
from unittest.mock import patch

import pytest

from tests.test_delivery_publication import setup
from tests.test_delivery_completion import event
from tools.delivery_caller import CompletionOutbox
from tools.delivery_observations import retain_completion, recover_completions, ARCHIVE
from tools.delivery_runner import DeliveryError


@pytest.fixture
def caller(setup, tmp_path):
    state = tmp_path / 'caller'
    state.mkdir()
    receiver = setup[0]
    with patch('tools.delivery_caller._protected'):
        yield CompletionOutbox(state, receiver.state,
            lambda **kw: retain_completion(receiver, **kw), owner=os.getuid())


def test_handoff_and_restart_preserve_identity(caller, setup):
    path, digest = caller.retain(**event())
    raw = path.read_bytes()
    result = caller.handoff()
    assert result == dict(status='RETAINED_BY_RECEIVER', transferred=1,
                          publication_verified=False, live_money=False)
    assert not path.exists()
    assert (caller.state / ARCHIVE / path.name).read_bytes() == raw
    assert (setup[0].state / path.name).read_bytes() == raw
    assert not (setup[0].state / 'observations.json').exists()
    restarted = CompletionOutbox(caller.state, caller.receiver_state, caller.accept,
                                owner=os.getuid())
    assert restarted.handoff()['transferred'] == 0
    assert restarted.retain(**event())[1] == digest
    recover_completions(setup[0])
    assert (setup[0].state / ARCHIVE / path.name).read_bytes() == raw


@pytest.mark.parametrize('reason', ['queue full', 'disk full', 'spool busy'])
def test_receiver_rejection_retains_event_for_retry(caller, reason):
    path, _ = caller.retain(**event())
    raw = path.read_bytes()
    with patch('tools.delivery_observations._admit_completion',
               side_effect=DeliveryError(reason)):
        with pytest.raises(DeliveryError, match=reason):
            caller.handoff()
    assert path.read_bytes() == raw
    assert caller.handoff()['transferred'] == 1


def test_crash_after_receiver_retention_retries_even_after_newer_event(caller, setup):
    path, _ = caller.retain(**event(observed_at='2026-11-03T00:00:00+00:00'))
    with patch('tools.delivery_caller.os.rename', side_effect=OSError('crash')):
        with pytest.raises(OSError):
            caller.handoff()
    recover_completions(setup[0])
    retain_completion(setup[0], **event(event_id='later', observed_at='2026-11-04T00:00:00+00:00'))
    recover_completions(setup[0])
    assert path.exists()
    assert caller.handoff()['transferred'] == 1


@pytest.mark.parametrize('change', [dict(live_money=True), dict(source_head='bad'),
    dict(event_id='../bad'), dict(evidence=''), dict(status='GREEN')])
def test_invalid_events_never_retained(caller, change):
    with pytest.raises(DeliveryError):
        caller.retain(**event(**change))
    assert not list(caller.state.glob('completion-*.json'))


def test_invalid_batch_rejects_before_any_handoff(caller, setup):
    caller.retain(**event())
    (caller.state / 'completion-bad.json').write_text('{}')
    with pytest.raises(DeliveryError):
        caller.handoff()
    assert not list(setup[0].state.glob('completion-*.json'))


def test_unsafe_receiver_acknowledgement_preserves_event(caller):
    path, _ = caller.retain(**event())
    bad = CompletionOutbox(caller.state, caller.receiver_state,
                          lambda **kw: (path, '0' * 64), owner=os.getuid())
    with pytest.raises(DeliveryError, match='acknowledgement'):
        bad.handoff()
    assert path.exists()


def test_concurrent_handoff_rejected(caller):
    from tools.delivery_observations import _spool_lock
    path, _ = caller.retain(**event())
    with _spool_lock(caller.state), patch('tools.delivery_observations.SPOOL_WAIT', 0):
        with pytest.raises(DeliveryError, match='spool busy'):
            caller.handoff()
    assert path.exists()


def test_local_admission_failure_does_not_call_receiver(caller, setup):
    with patch('tools.delivery_observations._disk_admission', side_effect=OSError('full')):
        with pytest.raises(OSError):
            caller.retain(**event())
    assert not list(setup[0].state.glob('completion-*.json'))


def test_disjoint_roots_and_binding_required(caller):
    with pytest.raises(DeliveryError, match='disjoint'):
        CompletionOutbox(caller.state, caller.state, caller.accept, owner=os.getuid())
    caller.accept = lambda **kw: None
    with pytest.raises(DeliveryError, match='binding changed'):
        caller.handoff()


def test_partial_handoff_keeps_remaining_work(caller, setup):
    first, _ = caller.retain(**event(observed_at='2026-11-03T00:00:00+00:00'))
    second, _ = caller.retain(**event(event_id='second', observed_at='2026-11-04T00:00:00+00:00'))
    def accept(**kw):
        if kw['event_id'] == 'second':
            raise OSError('receiver unavailable')
        return retain_completion(setup[0], **kw)
    partial = CompletionOutbox(caller.state, caller.receiver_state, accept, owner=os.getuid())
    with pytest.raises(OSError):
        partial.handoff()
    assert not first.exists() and second.exists()
    assert caller.handoff()['transferred'] == 1


def test_symlink_envelope_rejected_before_receiver(caller, setup):
    path, _ = caller.retain(**event())
    moved = caller.state / 'original'
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises((DeliveryError, OSError)):
        caller.handoff()
    assert not list(setup[0].state.glob('completion-*.json'))


def test_archive_fsync_failure_preserves_receiver_and_caller_evidence(caller, setup):
    path, _ = caller.retain(**event())
    with patch('tools.delivery_caller._fsync_directory', side_effect=OSError('fsync')):
        with pytest.raises(OSError):
            caller.handoff()
    assert (setup[0].state / path.name).exists()
    assert (caller.state / ARCHIVE / path.name).exists()
    assert caller.handoff()['transferred'] == 0
