"""Real filesystem lock tests and injected host recovery; no production access."""
import json
import subprocess
import sys

import pytest

from tools.delivery_runner import DeliveryError, deliver, delivery_lock
from tools.delivery_supervisor import DeliverySupervisor, supervisor_lock
from tests.test_delivery_runner import HEAD, NOW, setup  # shared transaction fixture


def coordinator(setup):
    return DeliverySupervisor(setup[1], setup[2], setup[3])


def interrupted(setup, phase='CUTOVER_PENDING'):
    record = dict(schema='delivery-state-v1', request_id='previous', source_head=HEAD,
                  status=phase, live_money=False, started_at=NOW.isoformat())
    if phase == 'CUTOVER_PENDING':
        record['rollback'] = setup[3].snapshot.return_value
    (setup[1] / 'delivery.json').write_text(json.dumps(record))


def test_empty_startup_does_not_mutate_host(setup):
    assert coordinator(setup).startup() is None
    assert not setup[3].method_calls
    assert not setup[2].method_calls


def test_writer_and_bridge_share_lease(setup):
    supervisor = coordinator(setup)
    with supervisor.writer_session():
        # Both a second supervisor and a direct transaction are excluded.
        with pytest.raises(DeliveryError, match='concurrent supervisor'):
            supervisor.release(setup[0], now=NOW)
        with pytest.raises(DeliveryError, match='concurrent release'):
            deliver(*setup, now=NOW)
        with pytest.raises(DeliveryError, match='concurrent supervisor'):
            with coordinator(setup).writer_session():
                pytest.fail('second writer entered')
    assert supervisor.release(setup[0], now=NOW)['status'] == 'SUCCEEDED'


def test_writer_exception_releases_both_locks(setup):
    with pytest.raises(RuntimeError):
        with coordinator(setup).writer_session():
            raise RuntimeError('writer failed')
    with supervisor_lock(setup[1]), delivery_lock(setup[1]):
        pass


@pytest.mark.parametrize('operation', ['startup', 'writer', 'release'])
def test_recovery_precedes_all_work(setup, operation):
    interrupted(setup)
    supervisor = coordinator(setup)
    if operation == 'writer':
        with supervisor.writer_session():
            assert setup[3].rollback_check.called
            assert json.loads((setup[1] / 'delivery.json').read_text())['status'] == 'ROLLED_BACK'
    elif operation == 'release':
        assert supervisor.release(setup[0], now=NOW)['status'] == 'SUCCEEDED'
    else:
        assert supervisor.startup()['status'] == 'ROLLED_BACK'
    calls = [call[0] for call in setup[3].method_calls]
    assert calls[:4] == ['restore_release', 'restore_schedule', 'rollback_check', 'cleanup']


@pytest.mark.parametrize('failure', ['restore_release', 'restore_schedule', 'rollback_check', 'cleanup'])
@pytest.mark.parametrize('operation', ['writer', 'release'])
def test_failed_recovery_blocks_writer_and_push(setup, failure, operation):
    interrupted(setup)
    getattr(setup[3], failure).side_effect = RuntimeError('injected')
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        if operation == 'writer':
            with coordinator(setup).writer_session():
                pytest.fail('writer entered')
        else:
            coordinator(setup).release(setup[0], now=NOW)
    setup[2].push.assert_not_called()
    setup[3].prepare.assert_not_called()


@pytest.mark.parametrize('payload', ['{', '[]', '{}'])
def test_malformed_journal_never_bypassed(setup, payload):
    (setup[1] / 'delivery.json').write_text(payload)
    with pytest.raises((DeliveryError, ValueError)):
        with coordinator(setup).writer_session():
            pytest.fail('writer entered')
    assert not setup[3].method_calls


def test_dangling_journal_is_not_missing(setup):
    (setup[1] / 'delivery.json').symlink_to(setup[1] / 'missing')
    with pytest.raises(DeliveryError, match='unsafe recovery journal'):
        coordinator(setup).startup()


def test_lock_symlink_rejected(setup):
    target = setup[1] / 'target'
    target.write_text('unchanged')
    (setup[1] / 'supervisor.lock').symlink_to(target)
    with pytest.raises(OSError):
        coordinator(setup).startup()
    assert target.read_text() == 'unchanged'


def test_direct_delivery_lock_blocks_writer(setup):
    with delivery_lock(setup[1]):
        with pytest.raises(DeliveryError, match='concurrent release'):
            with coordinator(setup).writer_session():
                pytest.fail('writer entered')


def test_interruption_during_release_recovered_before_next_writer(setup):
    setup[3].adopt_schedule.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        coordinator(setup).release(setup[0], now=NOW)
    with coordinator(setup).writer_session():
        assert json.loads((setup[1] / 'delivery.json').read_text())['status'] == 'ROLLED_BACK'
    setup[3].restore_schedule.assert_called_once()


@pytest.mark.parametrize('lease', ['writer_session', 'release_lock'])
def test_cross_process_contention_and_process_exit(setup, lease):
    # Pipe synchronization avoids sleeps and does not need socket capability.
    script = """
import sys
from pathlib import Path
from tools.delivery_supervisor import DeliverySupervisor, supervisor_lock
state = Path(sys.argv[1])
context = (DeliverySupervisor(state, None, None).writer_session()
           if sys.argv[2] == 'writer_session' else supervisor_lock(state))
with context:
    print('locked', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, '-c', script, str(setup[1]), lease],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(DeliveryError, match='concurrent supervisor'):
            coordinator(setup).startup()
        if lease == 'writer_session':
            with pytest.raises(DeliveryError, match='concurrent release'):
                with delivery_lock(setup[1]):
                    pytest.fail('direct delivery entered')
    finally:
        child.kill()
        child.communicate(timeout=5)
    with coordinator(setup).writer_session():
        pass


def test_unconfigured_writer_launcher_fails_closed(setup, tmp_path):
    with pytest.raises(DeliveryError, match='protected writer launcher required'):
        coordinator(setup).run_cycle(['/writer'], ['/bridge'], cwd=tmp_path,
                                     writer_timeout=1, bridge_timeout=1)
    assert not (setup[1] / 'writer-active.json').exists()


def test_contained_writer_cleanup_failure_latches_before_bridge(setup, tmp_path):
    from unittest.mock import Mock
    from tools.delivery_process import CleanupError
    launcher = Mock()
    launcher.run.side_effect = CleanupError('cgroup still populated')
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3], launcher=launcher)
    with pytest.raises(CleanupError):
        supervisor.run_cycle(['/writer'], ['/bridge'], cwd=tmp_path,
                             writer_timeout=1, bridge_timeout=1)
    assert launcher.run.call_count == 1
    assert launcher.run.call_args.args[0] == 'writer'
    assert (setup[1] / 'writer-active.json').exists()
    with pytest.raises(DeliveryError, match='host reconciliation'):
        supervisor.startup()
