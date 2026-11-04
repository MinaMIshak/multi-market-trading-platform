"""Real local process groups; no sockets, deployment or network."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from tools.delivery_process import CleanupError, _live_members, run_owned
from tools.delivery_runner import DeliveryError, delivery_lock
from tools.delivery_supervisor import DeliverySupervisor, supervisor_lock
from tests.test_delivery_runner import setup


def command(script):
    return [sys.executable, '-c', script]


@pytest.mark.parametrize('leader', ['time.sleep(30)', 'sys.exit(0)', 'sys.exit(7)'])
def test_descendants_terminated_even_after_leader_exit(tmp_path, leader):
    record = tmp_path / 'group'
    script = f'''
import os, signal, sys, time
from pathlib import Path
Path({str(record)!r}).write_text(str(os.getpgrp()))
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
else:
    {leader}
'''
    if leader == 'sys.exit(0)':
        assert run_owned(command(script), cwd=tmp_path, timeout=0.5, grace=0.1) == 0
    else:
        with pytest.raises(DeliveryError, match='timed out|command failed'):
            run_owned(command(script), cwd=tmp_path, timeout=0.5, grace=0.1)
    assert not _live_members(int(record.read_text()))


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), True])
def test_invalid_deadlines_do_not_launch(tmp_path, value):
    with patch('tools.delivery_process.subprocess.Popen') as launch:
        with pytest.raises(DeliveryError):
            run_owned(command('pass'), cwd=tmp_path, timeout=value)
        launch.assert_not_called()


def test_cycle_orders_cleanup_and_bridge_under_locks(setup, tmp_path):
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3])
    calls = []
    def owned(argv, **kwargs):
        calls.append(argv)
        for lock in (supervisor_lock, delivery_lock):
            with pytest.raises(DeliveryError):
                with lock(setup[1]):
                    pytest.fail('lease released')
    with patch('tools.delivery_process.run_owned', side_effect=owned):
        supervisor.run_cycle(['writer'], ['bridge'], cwd=tmp_path,
                             writer_timeout=1, bridge_timeout=1)
    assert calls == [['writer'], ['bridge']]
    assert not (setup[1] / 'writer-active.json').exists()


@pytest.mark.parametrize('error', [DeliveryError('timeout'), CleanupError('cleanup')])
def test_writer_failure_never_runs_bridge(setup, tmp_path, error):
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3])
    with patch('tools.delivery_process.run_owned', side_effect=error) as run:
        with pytest.raises(DeliveryError):
            supervisor.run_cycle(['writer'], ['bridge'], cwd=tmp_path,
                                 writer_timeout=1, bridge_timeout=1)
    assert run.call_count == 1
    if isinstance(error, CleanupError):
        for operation in (supervisor.startup,
                          lambda: supervisor.release(setup[0])):
            with pytest.raises(DeliveryError, match='host reconciliation'):
                operation()
        with pytest.raises(DeliveryError, match='host reconciliation'):
            with supervisor.writer_session():
                pytest.fail('writer entered')
    else:
        supervisor.startup()


def test_actual_cycle_bridge_runs_after_descendant_cleanup(setup, tmp_path):
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3])
    record = tmp_path / 'pid'
    writer = command(f'import os, time; from pathlib import Path; '
                     f'Path({str(record)!r}).write_text(str(os.getpgrp())); '
                     'pid=os.fork(); time.sleep(30) if pid == 0 else None')
    bridge = command(f'from pathlib import Path; '
                     'from tools.delivery_process import _live_members; '
                     f'assert not _live_members(int(Path({str(record)!r}).read_text()))')
    supervisor.run_cycle(writer, bridge, cwd=Path.cwd(), writer_timeout=2,
                         bridge_timeout=2, grace=0.1)


def test_cleanup_inspection_failure_is_distinct(tmp_path):
    with patch('tools.delivery_process._live_members', side_effect=PermissionError):
        with pytest.raises(CleanupError):
            run_owned(command('pass'), cwd=tmp_path, timeout=2, grace=0.1)


def test_bridge_failure_prevents_success_and_clears_clean_lease(setup, tmp_path):
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3])
    with patch('tools.delivery_process.run_owned',
               side_effect=[0, DeliveryError('bridge failed')]) as run:
        with pytest.raises(DeliveryError, match='bridge failed'):
            supervisor.run_cycle(['writer'], ['bridge'], cwd=tmp_path,
                                 writer_timeout=1, bridge_timeout=1)
    assert run.call_count == 2
    assert not (setup[1] / 'writer-active.json').exists()
    setup[2].push.assert_not_called()


def test_dangling_active_marker_also_blocks_recovery(setup):
    (setup[1] / 'writer-active.json').symlink_to(setup[1] / 'absent')
    with pytest.raises(DeliveryError, match='host reconciliation'):
        DeliverySupervisor(setup[1], setup[2], setup[3]).startup()
    assert not setup[3].method_calls


def test_uncertain_writer_blocks_direct_delivery_lock(setup):
    (setup[1] / 'writer-active.json').write_text('{}')
    with pytest.raises(DeliveryError, match='host reconciliation'):
        with delivery_lock(setup[1]):
            pytest.fail('direct runner bypassed containment')


def test_interruption_retains_marker(setup, tmp_path):
    supervisor = DeliverySupervisor(setup[1], setup[2], setup[3])
    with patch('tools.delivery_process.run_owned', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            supervisor.run_cycle(['writer'], ['bridge'], cwd=tmp_path,
                                 writer_timeout=1, bridge_timeout=1)
    with pytest.raises(DeliveryError, match='host reconciliation'):
        supervisor.startup()


def test_explicit_environment_excludes_inherited_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('DELIVERY_TEST_UNTRUSTED', 'inherited')
    script = ('import os; assert "DELIVERY_TEST_UNTRUSTED" not in os.environ; '
              'assert os.environ["LIVE_MONEY"] == "false"')
    assert run_owned(command(script), cwd=tmp_path, timeout=2,
                     env={'LIVE_MONEY': 'false'}) == 0


def test_owned_server_cleanup_on_caller_interruption(tmp_path):
    from tools.delivery_process import owned_process
    with pytest.raises(KeyboardInterrupt):
        with owned_process(command('import time; time.sleep(30)'), cwd=tmp_path, grace=0.1) as process:
            group = process.pid
            raise KeyboardInterrupt()
    assert process.poll() is not None and not _live_members(group)


def test_owned_server_passes_explicit_descriptor_and_cleans_up(tmp_path):
    from tools.delivery_process import owned_process
    read_fd, write_fd = os.pipe()
    try:
        script = f'import os; os.write({write_fd}, b"ready")'
        with owned_process(command(script), cwd=tmp_path, grace=0.1,
                           pass_fds=(write_fd,)) as process:
            assert process.wait(timeout=2) == 0
            assert os.read(read_fd, 5) == b'ready'
        assert not _live_members(process.pid)
    finally:
        os.close(read_fd)
        os.close(write_fd)
