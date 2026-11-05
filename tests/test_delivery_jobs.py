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
