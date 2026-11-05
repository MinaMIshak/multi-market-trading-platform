"""POSIX process-group supervision for trusted host commands.

The host service must additionally contain the supervisor and all children in a
kill-on-stop cgroup. Process groups cannot contain children that call setsid(),
nor survive supervisor SIGKILL safely. No request-supplied commands are accepted
by the delivery API; this helper is only for trusted supervisor wiring.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from tools.delivery_runner import DeliveryError, require


class CleanupError(DeliveryError):
    """Host must reconcile containment before any further work."""


def _signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def run_owned(argv, *, cwd: Path, timeout: float, grace: float = 1.0, env=None):
    """Run one trusted command; terminate its group on *every* exit path.

    Output inherits the supervisor's log handles (no unbounded pipe buffering).
    Returning zero means the leader succeeded and group cleanup completed, not
    that application validation succeeded. Descendants must not daemonize.
    """
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
            'invalid process deadline')
    with owned_process(argv, cwd=cwd, grace=grace, env=env) as process:
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DeliveryError('supervised command timed out') from None
        require(returncode == 0, 'supervised command failed')
        return returncode


@contextmanager
def owned_process(argv, *, cwd: Path, grace=1.0, env=None, pass_fds=()):
    """Own a temporary server and clean its process group before returning."""
    require(isinstance(argv, (list, tuple)) and argv
            and all(isinstance(v, str) and v and '\0' not in v for v in argv)
            and Path(argv[0]).is_absolute(), 'invalid trusted command')
    require(cwd.is_absolute() and cwd.resolve() == cwd and cwd.is_dir(),
            'invalid command directory')
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0 for v in (grace,)),
            'invalid process deadline')
    process = subprocess.Popen(argv, cwd=cwd, start_new_session=True,
                               stdin=subprocess.DEVNULL, close_fds=True, env=env,
                               pass_fds=pass_fds)
    try:
        yield process
    finally:
        try:
            # A successful/exited leader can still leave a writer grandchild alive.
            # Always kill the group, and wait out the TERM grace even if leader exited.
            _signal_group(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            _signal_group(process.pid, signal.SIGKILL)
            process.wait(timeout=grace)
            # SIGKILL delivery is asynchronous. Linux /proc lets us distinguish dead
            # (possibly unreaped zombie) members from processes that can still write.
            deadline = time.monotonic() + grace
            while _live_members(process.pid):
                require(time.monotonic() < deadline, 'process group cleanup unconfirmed')
                time.sleep(0.01)
        except BaseException as exc:
            raise CleanupError("process group cleanup unconfirmed") from exc


def _live_members(group):
    """Linux host requirement; unknown process state must fail closed."""
    proc = Path('/proc')
    require(proc.is_dir(), 'Linux process inspection required')
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            # A process can exit after procfs open but before read (ESRCH).
            continue
        if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
            return True
    return False
