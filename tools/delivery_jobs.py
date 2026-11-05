"""Cleanup for protected, host-provisioned cgroup-v2 temporary jobs.

The host launcher must place jobs inside these groups before executing code,
exclude other writers, and deny job migration/delegation. JobLauncher admits
commands before exec; neither component establishes filesystem isolation. Never
map a persistent service or the supervisor's own cgroup here. Policies are trusted wiring, not requests.
"""
import math
import os
from pathlib import Path
import stat
import time

from tools.delivery_containment import _absolute, _protected
from tools.delivery_process import CleanupError
from tools.delivery_runner import require


class JobContainment:
    def __init__(self, policies, *, owner=0, cgroups=Path('/sys/fs/cgroup'),
                 proc=Path('/proc'), timeout=5.0):
        require(type(timeout) in (int, float) and math.isfinite(timeout)
                and timeout > 0, 'invalid cleanup deadline')
        self.policies = dict(policies)
        self.owner = owner
        self.cgroups = Path(cgroups)
        self.proc = Path(proc)
        self.timeout = timeout

    def _group(self, key):
        value = self.policies.get(key)
        require(isinstance(value, str) and _absolute(value) != '/',
                'temporary cgroup policy missing')
        # Disjoint groups prevent one job's cleanup from destroying another job.
        groups = [Path(_absolute(v)) for v in self.policies.values()]
        require(all(a != b and not a.is_relative_to(b) and not b.is_relative_to(a)
                    for i, a in enumerate(groups) for b in groups[i + 1:]),
                'overlapping temporary cgroups')
        own = (self.proc / 'self/cgroup').read_text().splitlines()
        require(len(own) == 1 and own[0].startswith('0::'),
                'unified supervisor cgroup required')
        own_path = Path(_absolute(own[0][3:]))
        require(not own_path.is_relative_to(value), 'supervisor inside temporary cgroup')
        group = self.cgroups / value.lstrip('/')
        require(group.is_absolute() and group.resolve() == group,
                'invalid temporary cgroup path')
        _protected(group, self.owner)
        require(group.is_dir() and not any(p.is_dir() for p in group.iterdir()),
                'temporary cgroup must be a leaf')
        return group

    def _read(self, group, name):
        path = group / name
        _protected(path, self.owner)
        require(path.is_file(), 'invalid cgroup control')
        return path.read_text()

    def quiescent(self, key):
        group = self._group(key)
        before = group.stat()
        events = {}
        for line in self._read(group, 'cgroup.events').splitlines():
            pair = line.split()
            require(len(pair) == 2 and pair[0] not in events
                    and pair[1] in ('0', '1'), 'invalid cgroup events')
            events[pair[0]] = pair[1]
        require('populated' in events and 'frozen' in events, 'missing cgroup events')
        members = self._read(group, 'cgroup.procs').split()
        require(all(p.isascii() and p.isdigit() and int(p) > 0 for p in members),
                'invalid temporary membership')
        after = self._group(key).stat()
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
                'temporary cgroup replaced')
        return events['populated'] == '0' and not members

    def cleanup(self, key):
        """Kill all members, including detached children, and verify emptiness.

        Missing/unsafe groups are uncertainty, never evidence of cleanup. The
        caller must retain its durable active latch on every cleanup exception.
        """
        try:
            group = self._group(key)
            identity = group.stat()
            path = group / 'cgroup.kill'
            _protected(path, self.owner)
            fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == self.owner
                        and not info.st_mode & 0o022, 'unsafe cgroup kill control')
                require(os.write(fd, b'1') == 1, 'incomplete cgroup kill')
            finally:
                os.close(fd)
            deadline = time.monotonic() + self.timeout
            while not self.quiescent(key):
                require(time.monotonic() < deadline, 'temporary cleanup timed out')
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            after = self._group(key).stat()
            require((identity.st_dev, identity.st_ino) == (after.st_dev, after.st_ino),
                    'temporary cgroup replaced')
        except BaseException as exc:
            raise CleanupError('temporary cgroup cleanup unconfirmed') from exc


# The bootstrap runs only trusted stdlib code before joining the group. The target
# cannot run unless the kernel accepts admission. No preexec_fn (unsafe in a
# threaded supervisor), shell, or request-selected Python code is involved.
_ADMIT = '''import os, sys
fd = int(sys.argv[1])
if os.write(fd, b"0") != 1:
    raise RuntimeError("incomplete cgroup admission")
os.close(fd)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
'''


class JobLauncher:
    """Host-only launch binding; caller holds the exclusive delivery lease.

    The bootstrap interpreter, its stdlib and host isolation policy must be
    immutable. Jobs must lack cgroup migration rights. This is admission and
    cleanup, not privilege dropping or filesystem/network sandbox installation.
    """
    def __init__(self, containment, *, python):
        self.containment = containment
        self.python = Path(python)

    def process(self, key, argv, *, cwd, grace=1.0, env=None, pass_fds=()):
        from contextlib import contextmanager
        from tools.delivery_process import owned_process

        @contextmanager
        def session():
            require(isinstance(argv, (list, tuple)) and argv
                    and all(isinstance(v, str) and v and '\0' not in v for v in argv)
                    and Path(argv[0]).is_absolute(), 'invalid trusted command')
            require(self.python.is_absolute() and self.python.resolve() == self.python
                    and self.python.is_file(), 'invalid bootstrap interpreter')
            _protected(self.python, self.containment.owner)
            try:
                group = self.containment._group(key)
                identity = group.stat()
                require(self.containment.quiescent(key), 'temporary job already active')
                control = group / 'cgroup.procs'
                _protected(control, self.containment.owner)
                fd = os.open(control, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except BaseException as exc:
                raise CleanupError('job admission state unconfirmed') from exc
            try:
                try:
                    info = os.fstat(fd)
                    require(stat.S_ISREG(info.st_mode) and info.st_uid == self.containment.owner
                            and not info.st_mode & 0o022, 'unsafe admission control')
                    require((info.st_dev, info.st_ino) ==
                            (control.stat().st_dev, control.stat().st_ino),
                            'admission control replaced')
                except BaseException as exc:
                    raise CleanupError('job admission control unconfirmed') from exc
                try:
                    with owned_process([str(self.python), '-I', '-c', _ADMIT, str(fd), *argv],
                                       cwd=cwd, grace=grace, env=env,
                                       pass_fds=(*pass_fds, fd)) as process:
                        yield process
                finally:
                    # Even a failed bootstrap/exec or detached child requires
                    # whole-cgroup cleanup before the durable latch can clear.
                    try:
                        after = self.containment._group(key).stat()
                        require((identity.st_dev, identity.st_ino) ==
                                (after.st_dev, after.st_ino), 'admission group replaced')
                        self.containment.cleanup(key)
                    except BaseException as exc:
                        raise CleanupError('launched job cleanup unconfirmed') from exc
            finally:
                os.close(fd)
        return session()

    def run(self, key, argv, *, cwd, timeout, grace=1.0, env=None):
        import subprocess
        from tools.delivery_runner import DeliveryError
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                'invalid process deadline')
        with self.process(key, argv, cwd=cwd, grace=grace, env=env) as process:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise DeliveryError('supervised command timed out') from None
            require(code == 0, 'supervised command failed')
            return code
