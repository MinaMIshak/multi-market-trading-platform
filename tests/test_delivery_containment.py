"""Synthetic proc/cgroup fixtures only; no host containment acceptance implied."""
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.delivery_containment import ServiceContainment, _mounts, _protected
from tools.delivery_runner import DeliveryError

SHA = 'a' * 40


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    exe = tmp_path / 'python'
    exe.write_bytes(b'pinned interpreter')
    release = tmp_path / 'release'
    release.mkdir()
    proc = tmp_path / 'proc'
    process = proc / '123'
    (process / 'ns').mkdir(parents=True)
    (proc / 'self/ns').mkdir(parents=True)
    (process / 'ns/mnt').symlink_to('mnt:[2]')
    (proc / 'self/ns/mnt').symlink_to('mnt:[1]')
    (process / 'exe').symlink_to(exe)
    (process / 'root').symlink_to('/')
    (process / 'stat').write_text('123 (python) ' + ' '.join(['1'] * 30))
    (process / 'status').write_text('Uid:\t123 123 123 123\nGid:\t124 124 124 124\n'
        'Groups:\nNoNewPrivs:\t1\nSeccomp:\t2\n' +
        ''.join(f'{key}:\t0000000000000000\n' for key in
                ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')))
    (process / 'cgroup').write_text('0::/release.service\n')
    (process / 'mountinfo').write_text('1 0 1:1 / / ro - ext4 /dev/root ro\n'
        '2 1 1:2 / /state rw,nosuid,nodev,noexec - tmpfs tmpfs rw\n')
    cgroups = tmp_path / 'cgroups'
    group = cgroups / 'release.service'
    group.mkdir(parents=True)
    (group / 'cgroup.procs').write_text('123\n')
    policy = dict(uid=123, gid=124, cgroup='/release.service', protected=[str(release)],
                  executable=str(exe), executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  writable_mounts=['/state'])
    # Temporary fixture ancestors are not protected host roots. Test the real
    # ownership checker independently; never relax its production default.
    monkeypatch.setattr('tools.delivery_containment._protected', Mock())
    verifier = ServiceContainment({SHA: policy}, Mock(return_value=True), proc=proc, cgroups=cgroups)
    observed = dict(ActiveState='active', SubState='running', MainPID='123',
                    ControlGroup='/release.service')
    return verifier, policy, observed, process, group


def test_active_and_inactive_preflight(boundary):
    verifier, _, observed, _, _ = boundary
    assert verifier(SHA, observed, True) is True
    assert verifier(SHA, dict(ActiveState='inactive', MainPID='0'), False) is True
    with pytest.raises(DeliveryError):
        verifier(SHA, dict(ActiveState='inactive', MainPID='0'), True)


@pytest.mark.parametrize('field,value', [('Uid', '0 0 0 0'), ('Gid', '0 0 0 0'),
    ('Groups', '124 0'), ('NoNewPrivs', '0'), ('Seccomp', '0'),
    *[(k, '0001') for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')]])
def test_privilege_drift(boundary, field, value):
    verifier, _, observed, process, _ = boundary
    path = process / 'status'
    path.write_text('\n'.join(f'{field}:\t{value}' if line.startswith(field + ':') else line
                              for line in path.read_text().splitlines()))
    with pytest.raises(DeliveryError):
        verifier(SHA, observed, True)


@pytest.mark.parametrize('fault', ['cgroup', 'member', 'child', 'namespace', 'exe',
    'writable', 'flags', 'nested', 'root', 'missing', 'release', 'pin', 'uid', 'path'])
def test_boundary_failures(boundary, fault):
    verifier, policy, observed, process, group = boundary
    if fault == 'cgroup':
        (process / 'cgroup').write_text('0::/other\n')
    elif fault == 'member':
        (group / 'cgroup.procs').write_text('456\n')
    elif fault == 'child':
        (group / 'child').mkdir()
    elif fault in ('namespace', 'exe', 'root'):
        path = process / {'namespace': 'ns/mnt', 'exe': 'exe', 'root': 'root'}[fault]
        path.unlink()
        path.symlink_to({'namespace': 'mnt:[1]', 'exe': '/other', 'root': str(process)}[fault])
    elif fault in ('writable', 'flags', 'nested'):
        path = process / 'mountinfo'
        text = path.read_text()
        if fault == 'writable':
            text = text.replace('/ / ro', '/ / rw')
        elif fault == 'flags':
            text = text.replace(',noexec', '')
        else:
            text += f"3 1 1:3 / {policy['protected'][0]}/hidden ro - tmpfs tmpfs ro\n"
        path.write_text(text)
    elif fault == 'missing':
        (process / 'status').unlink()
    elif fault == 'release':
        verifier.verify_release.return_value = None
    elif fault == 'pin':
        Path(policy['executable']).write_bytes(b'drift')
    elif fault == 'uid':
        policy['uid'] = 0
    elif fault == 'path':
        policy['cgroup'] = '/x/../release.service'
    with pytest.raises((DeliveryError, OSError)):
        verifier(SHA, observed, True)


def test_all_members_checked(boundary):
    verifier, _, observed, _, group = boundary
    (group / 'cgroup.procs').write_text('123\n456\n')
    with pytest.raises(OSError):
        verifier(SHA, observed, True)


def test_membership_drift(boundary, monkeypatch):
    verifier, _, observed, _, group = boundary
    original = verifier._process
    def drift(pid, p):
        original(pid, p)
        (group / 'cgroup.procs').write_text('123\n456\n')
    monkeypatch.setattr(verifier, '_process', drift)
    with pytest.raises(DeliveryError, match='membership changed'):
        verifier(SHA, observed, True)


@pytest.mark.parametrize('text', ['', 'bad', '1 0 1:1 / / ro,rw - ext4 x ro',
    '1 0 1:1 / / ro - ext4 x ro\n1 0 1:1 / / ro - ext4 x ro'])
def test_malformed_mounts(text):
    with pytest.raises(DeliveryError):
        _mounts(text)


def test_ownership_checker_rejects_unprotected_fixture(tmp_path):
    path = tmp_path / 'policy'
    path.write_text('fixture')
    path.chmod(0o666)
    with pytest.raises(DeliveryError):
        _protected(path, path.stat().st_uid)
    path.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(DeliveryError):
        _protected(link, path.stat().st_uid)


def test_pid_reuse_during_observation(boundary, monkeypatch):
    verifier, _, observed, process, _ = boundary
    read = Path.read_text
    calls = 0
    def changed(path, *args, **kwargs):
        nonlocal calls
        text = read(path, *args, **kwargs)
        if path == process / 'stat':
            calls += 1
            if calls > 1:
                text = text.replace('1', '2')
        return text
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(DeliveryError, match='identity changed'):
        verifier(SHA, observed, True)


@pytest.mark.parametrize('path', ['/', '/sys', '/sys/fs/cgroup', '/sys/fs/cgroup/child'])
def test_writable_policy_cannot_cover_control_group(boundary, path):
    verifier, policy, observed, _, _ = boundary
    policy['writable_mounts'] = [path]
    with pytest.raises(DeliveryError):
        verifier(SHA, observed, True)


def test_writable_policy_cannot_cover_runtime(boundary):
    verifier, policy, observed, _, _ = boundary
    policy['writable_mounts'] = [policy['executable']]
    with pytest.raises(DeliveryError):
        verifier(SHA, observed, True)
