"""Artificial cgroup controls only; no host cgroup access or deployment."""
import os
from unittest.mock import patch

import pytest

from tools.delivery_jobs import JobContainment
from tools.delivery_process import CleanupError
from tools.delivery_runner import DeliveryError


@pytest.fixture
def job(tmp_path):
    groups = tmp_path / 'groups'
    group = groups / 'temporary'
    group.mkdir(parents=True)
    proc = tmp_path / 'proc'
    (proc / 'self').mkdir(parents=True)
    (proc / 'self/cgroup').write_text('0::/supervisor\n')
    for name, value in [('cgroup.events', 'populated 0\nfrozen 0\n'),
                        ('cgroup.procs', ''), ('cgroup.kill', '')]:
        (group / name).write_text(value)
        (group / name).chmod(0o600)
    binding = JobContainment({'release': '/temporary'}, owner=os.getuid(),
                             cgroups=groups, proc=proc, timeout=0.01)
    # /tmp is intentionally not host-owned/protected. Test path protection
    # separately; all controls and namespace observations here are artificial.
    with patch('tools.delivery_jobs._protected'):
        yield binding, group, proc


def test_cleanup_and_quiescence(job):
    binding, group, _ = job
    assert binding.quiescent('release') is True
    binding.cleanup('release')
    assert (group / 'cgroup.kill').read_text() == '1'


def test_kill_then_wait_for_detached_members(job):
    binding, group, _ = job
    (group / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
    (group / 'cgroup.procs').write_text('123\n456\n')
    assert binding.quiescent('release') is False
    def drain(_):
        assert (group / 'cgroup.kill').read_text() == '1'
        (group / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
        (group / 'cgroup.procs').write_text('')
    with patch('tools.delivery_jobs.time.sleep', side_effect=drain):
        binding.cleanup('release')


@pytest.mark.parametrize('events,members', [
    ('populated 1\nfrozen 0\n', ''), ('populated 0\nfrozen 0\n', '123\n'),
    ('populated 0\npopulated 0\nfrozen 0\n', ''),
    ('populated 0\n', ''), ('populated 2\nfrozen 0\n', ''),
    ('populated 0\nfrozen 0\n', 'not-a-pid'),
])
def test_uncertain_or_populated_group_never_cleans(job, events, members):
    binding, group, _ = job
    (group / 'cgroup.events').write_text(events)
    (group / 'cgroup.procs').write_text(members)
    with pytest.raises(CleanupError):
        binding.cleanup('release')


@pytest.mark.parametrize('kind', ['missing', 'nested', 'self', 'self-child',
                                  'alias', 'overlap', 'symlink', 'writable'])
def test_unsafe_policy_rejected_before_kill(job, kind):
    binding, group, proc = job
    if kind == 'missing':
        binding.policies.clear()
    elif kind == 'nested':
        (group / 'delegated').mkdir()
    elif kind in ('self', 'self-child'):
        (proc / 'self/cgroup').write_text('0::/temporary' +
                                         ('/child' if kind == 'self-child' else '') + '\n')
    elif kind == 'alias':
        binding.policies['other'] = '/temporary'
    elif kind == 'overlap':
        binding.policies['other'] = '/temporary/child'
    elif kind == 'symlink':
        (group / 'cgroup.kill').unlink()
        (group / 'cgroup.kill').symlink_to(group / 'cgroup.procs')
    else:
        (group / 'cgroup.kill').chmod(0o666)
    with pytest.raises(CleanupError):
        binding.cleanup('release')
    assert (group / 'cgroup.procs').read_text() == ''


def test_protection_failure_is_not_quiescence(job):
    binding, _, _ = job
    with patch('tools.delivery_jobs._protected', side_effect=DeliveryError('unprotected')):
        with pytest.raises(DeliveryError):
            binding.quiescent('release')
        with pytest.raises(CleanupError):
            binding.cleanup('release')


def test_replacement_during_cleanup_rejected(job):
    binding, group, _ = job
    def replace(_):
        group.rename(group.with_name('previous'))
        group.mkdir()
        (group / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
        (group / 'cgroup.procs').write_text('')
        return True
    with patch.object(binding, 'quiescent', side_effect=replace):
        with pytest.raises(CleanupError):
            binding.cleanup('release')


@pytest.mark.parametrize('timeout', [True, 0, -1, float('inf'), float('nan')])
def test_invalid_deadline(timeout):
    with pytest.raises(DeliveryError):
        JobContainment({}, timeout=timeout)


def test_production_cleanup_binding(job):
    from tools.delivery_bindings import ProductionServices
    binding, group, _ = job
    services = ProductionServices(None, None, None, binding.cleanup, binding.quiescent)
    services.cleanup_validation('release')
    assert services.validation_quiescent('release') is True
    (group / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
    with pytest.raises(CleanupError):
        services.cleanup_validation('release')


@pytest.fixture
def launcher(job):
    import sys
    from pathlib import Path
    from tools.delivery_jobs import JobLauncher
    binding, group, proc = job
    return JobLauncher(binding, python=Path(sys.executable).resolve()), group


def test_launch_admission_precedes_target(launcher, tmp_path):
    import sys
    launch, group = launcher
    marker = tmp_path / 'executed'
    script = (f'from pathlib import Path; '
              f'assert Path({str(group / "cgroup.procs")!r}).read_text() == "0"; '
              f'Path({str(marker)!r}).write_text("executed")')
    # Artificial control accepts writes but cannot implement kernel membership.
    def cleanup(key):
        assert marker.read_text() == 'executed'
        (group / 'cgroup.procs').write_text('')
    with patch.object(launch.containment, 'cleanup', side_effect=cleanup) as clean:
        assert launch.run('release', [sys.executable, '-c', script], cwd=tmp_path,
                          timeout=2, grace=0.05) == 0
    clean.assert_called_once_with('release')


@pytest.mark.parametrize('kind', ['populated', 'missing', 'symlink'])
def test_admission_uncertainty_never_executes(launcher, tmp_path, kind):
    import sys
    launch, group = launcher
    if kind == 'populated':
        (group / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
    elif kind == 'missing':
        (group / 'cgroup.procs').unlink()
    else:
        (group / 'cgroup.procs').unlink()
        (group / 'cgroup.procs').symlink_to(group / 'cgroup.kill')
    with patch('tools.delivery_process.subprocess.Popen') as popen:
        with pytest.raises((CleanupError, OSError)):
            launch.run('release', [sys.executable, '-c', 'pass'], cwd=tmp_path, timeout=1)
        popen.assert_not_called()


def test_exec_failure_still_cleans_cgroup(launcher, tmp_path):
    launch, _ = launcher
    with patch.object(launch.containment, 'cleanup') as clean:
        with pytest.raises(DeliveryError, match='command failed'):
            launch.run('release', ['/nonexistent-delivery-command'], cwd=tmp_path,
                       timeout=2, grace=0.05)
    clean.assert_called_once_with('release')


def test_cleanup_failure_overrides_success(launcher, tmp_path):
    import sys
    launch, _ = launcher
    with patch.object(launch.containment, 'cleanup', side_effect=RuntimeError):
        with pytest.raises(CleanupError):
            launch.run('release', [sys.executable, '-c', 'pass'], cwd=tmp_path,
                       timeout=2, grace=0.05)


def test_launch_timeout_cleans_cgroup(launcher, tmp_path):
    import sys
    launch, _ = launcher
    with patch.object(launch.containment, 'cleanup') as clean:
        with pytest.raises(DeliveryError, match='timed out'):
            launch.run('release', [sys.executable, '-c', 'import time; time.sleep(30)'],
                       cwd=tmp_path, timeout=0.1, grace=0.05)
    clean.assert_called_once_with('release')


def test_kernel_admission_error_prevents_target(launcher, tmp_path):
    import sys
    launch, group = launcher
    marker = tmp_path / 'must-not-exist'
    original_open = os.open
    def readonly(path, flags, *args, **kwargs):
        if path == group / 'cgroup.procs':
            flags = os.O_RDONLY
        return original_open(path, flags, *args, **kwargs)
    with patch('tools.delivery_jobs.os.open', side_effect=readonly), \
            patch.object(launch.containment, 'cleanup') as clean:
        with pytest.raises(DeliveryError, match='command failed'):
            launch.run('release', [sys.executable, '-c',
                       f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                       cwd=tmp_path, timeout=2, grace=0.05)
    assert not marker.exists()
    clean.assert_called_once_with('release')


def test_interrupted_server_and_replaced_group_latch(launcher, tmp_path):
    import sys
    launch, group = launcher
    with pytest.raises(CleanupError, match='cleanup unconfirmed'):
        with launch.process('release', [sys.executable, '-c', 'import time; time.sleep(30)'],
                            cwd=tmp_path, grace=0.05):
            group.rename(group.with_name('old'))
            group.mkdir()
            raise KeyboardInterrupt


def test_supervisor_uses_separate_groups_and_cleans_before_bridge(launcher, tmp_path):
    import sys
    from tools.delivery_supervisor import DeliverySupervisor
    launch, group = launcher
    bridge_group = group.with_name('bridge')
    bridge_group.mkdir()
    for name in ('cgroup.procs', 'cgroup.events', 'cgroup.kill'):
        (bridge_group / name).write_text((group / name).read_text())
        (bridge_group / name).chmod(0o600)
    launch.containment.policies = {'writer': '/temporary', 'bridge': '/bridge'}
    state = tmp_path / 'state'
    state.mkdir()
    supervisor = DeliverySupervisor(state, None, None, launcher=launch)
    writer = [sys.executable, '-c', 'pass']
    bridge = [sys.executable, '-c',
              f'from pathlib import Path; assert Path({str(group / "cgroup.procs")!r}).read_text() == ""']
    cleaned = []
    def cleanup(key):
        assert (state / 'writer-active.json').exists()
        target = group if key == 'writer' else bridge_group
        assert (target / 'cgroup.procs').read_text() == '0'
        (target / 'cgroup.procs').write_text('')
        cleaned.append(key)
    with patch.object(launch.containment, 'cleanup', side_effect=cleanup):
        supervisor.run_cycle(writer, bridge, cwd=tmp_path,
                             writer_timeout=2, bridge_timeout=2, grace=0.05)
    assert cleaned == ['writer', 'bridge']
    assert not (state / 'writer-active.json').exists()
