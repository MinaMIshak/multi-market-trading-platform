"""Read-only Linux verifier for a host-provisioned persistent release service.

No installation or containment creation. Policies must come from protected host
wiring, never release requests. Run under the delivery lease. Inactive checks are
preflight only; stage must call again with running=True before acceptance.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from tools.delivery_runner import SHA, require


def _absolute(value):
    require(isinstance(value, str) and value.startswith('/') and
            str(PurePosixPath(value)) == value and '..' not in value.split('/'),
            'noncanonical containment path')
    return value


def _protected(path, owner):
    path = Path(_absolute(str(path)))
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'unprotected containment artifact')
    require(path.is_file() or path.is_dir(), 'invalid containment artifact')


def _mounts(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        require('-' in fields and len(fields) >= 10, 'malformed mount observation')
        separator = fields.index('-')
        require(separator >= 6 and len(fields) == separator + 4,
                'malformed mount observation')
        # Reject escaped paths rather than interpret ambiguous policy spellings.
        path = _absolute(fields[4])
        require('\\' not in path and path not in result, 'ambiguous mount observation')
        flags = set(fields[5].split(','))
        require(('ro' in flags) != ('rw' in flags), 'unknown mount permissions')
        result[path] = flags
    require('/' in result, 'root mount missing')
    return result


class ServiceContainment:
    def __init__(self, policies, verify_release, *, owner=0,
                 proc=Path('/proc'), cgroups=Path('/sys/fs/cgroup')):
        self.policies = policies
        self.verify_release = verify_release
        self.owner = owner
        self.proc = Path(proc)
        self.cgroups = Path(cgroups)

    def _policy(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        p = self.policies.get(sha)
        require(isinstance(p, dict) and set(p) == {
            'uid', 'gid', 'cgroup', 'protected', 'executable', 'executable_sha256',
            'writable_mounts'}, 'containment policy missing')
        require(type(p['uid']) is int and p['uid'] > 0 and p['uid'] != self.owner
                and type(p['gid']) is int and p['gid'] > 0, 'unsafe service identity')
        require(_absolute(p['cgroup']) != '/', 'unsafe cgroup')
        require(isinstance(p['protected'], list) and p['protected'], 'protected roots missing')
        for path in [*p['protected'], p['executable']]:
            _protected(path, self.owner)
        require(re.fullmatch('[0-9a-f]{64}', p['executable_sha256']) and
                hashlib.sha256(Path(p['executable']).read_bytes()).hexdigest()
                == p['executable_sha256'], 'runtime executable mismatch')
        require(isinstance(p['writable_mounts'], list), 'invalid writable mount policy')
        for path in p['writable_mounts']:
            require(_absolute(path) != '/' and not any(
                PurePosixPath(root).is_relative_to(path) or PurePosixPath(path).is_relative_to(root)
                for root in [*p['protected'], p['executable'], '/sys/fs/cgroup']), 'unsafe writable mount policy')
        require(self.verify_release(sha) is True, 'release artifacts unverified')
        return p

    def _process(self, pid, p):
        base = self.proc / str(pid)
        before = (base / 'stat').read_text()
        fields = {}
        for line in (base / 'status').read_text().splitlines():
            key, sep, value = line.partition(':')
            require(sep and key not in fields, 'malformed process status')
            fields[key] = value.split()
        require(fields.get('Uid') == [str(p['uid'])] * 4
                and fields.get('Gid') == [str(p['gid'])] * 4
                and fields.get('Groups') in ([], [str(p['gid'])])
                and fields.get('NoNewPrivs') == ['1']
                and fields.get('Seccomp') == ['2'], 'process privilege boundary mismatch')
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            require(len(fields.get(key, [])) == 1 and
                    re.fullmatch('0+', fields[key][0]), 'process capabilities enabled')
        require((base / 'cgroup').read_text() == f"0::{p['cgroup']}\n",
                'process cgroup mismatch')
        require(os.readlink(base / 'exe') == p['executable'], 'process runtime mismatch')
        require(os.readlink(base / 'ns/mnt') != os.readlink(self.proc / 'self/ns/mnt'),
                'host mount namespace shared')
        mounts = _mounts((base / 'mountinfo').read_text())
        require({path for path, flags in mounts.items() if 'rw' in flags}
                == set(p['writable_mounts']), 'writable mount boundary mismatch')
        for path, flags in mounts.items():
            if 'rw' in flags:
                require({'nosuid', 'nodev', 'noexec'} <= flags, 'unsafe writable mount flags')
        for root in [*p['protected'], p['executable'], '/sys/fs/cgroup']:
            covering = max((path for path in mounts if PurePosixPath(root).is_relative_to(path)),
                           key=len)
            require('ro' in mounts[covering], 'protected path mounted writable')
            require(not any(path != root and PurePosixPath(path).is_relative_to(root)
                            for path in mounts), 'nested protected mount rejected')
        for root in [*p['protected'], p['executable']]:
            host = Path(root).stat()
            visible = (base / 'root' / root.lstrip('/')).stat()
            require((host.st_dev, host.st_ino) == (visible.st_dev, visible.st_ino),
                    'process filesystem identity mismatch')
        # Only starttime is stable across observations; other stat counters may change.
        require(before.rsplit(')', 1)[1].split()[19] ==
                (base / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                'process identity changed')

    def __call__(self, sha, observed, running):
        p = self._policy(sha)
        if not running and observed.get('ActiveState') == 'inactive':
            require(observed.get('MainPID') == '0', 'inactive process mismatch')
            return True  # preflight, never runtime acceptance
        require(observed.get('ActiveState') == 'active' and observed.get('SubState') == 'running'
                and observed.get('ControlGroup') == p['cgroup'], 'service cgroup mismatch')
        group = self.cgroups / p['cgroup'].lstrip('/')
        require(group.resolve() == group and group.is_dir(), 'invalid cgroup directory')
        _protected(group, self.owner)
        _protected(group / 'cgroup.procs', self.owner)
        # Dedicated leaf cgroups only: delegation/unknown descendant groups fail closed.
        require(not any(child.is_dir() for child in group.iterdir()), 'nested cgroup rejected')
        members = (group / 'cgroup.procs').read_text().split()
        require(members and len(set(members)) == len(members) and
                all(v.isascii() and v.isdigit() and int(v) > 0 for v in members)
                and observed.get('MainPID') in members, 'cgroup membership mismatch')
        for pid in members:
            self._process(pid, p)
        require(set((group / 'cgroup.procs').read_text().split()) == set(members),
                'cgroup membership changed')
        self._policy(sha)
        return True
