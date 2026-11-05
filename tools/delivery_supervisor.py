"""Host supervisor coordination; no CLI or production adapter.

All Codex writers, commit bridges and delivery invocations must use this API with
one host-owned state directory. Hold writer_session until the writer and all its
children have exited, including on timeout, then run the commit bridge before
leaving the session. A lock is not a process termination mechanism.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from tools.delivery_runner import (LeaseContention, deliver, delivery_lock, recover, require,
                                   require_settled_journal, _delivery_lease)


@contextmanager
def supervisor_lock(state: Path):
    require(state.is_absolute() and state.resolve() == state and state.is_dir(),
            'invalid supervisor state directory')
    fd = os.open(state / 'supervisor.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'unsafe supervisor lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LeaseContention('concurrent supervisor operation rejected') from None
        yield
    finally:
        os.close(fd)


class DeliverySupervisor:
    """Trusted host dependency injection; never construct adapters from requests.

    Lock order is supervisor then delivery. Direct delivery/recovery calls remain
    protected by the delivery lock, which a writer session also holds. The host
    must exclude uncoordinated writers and must keep these locks out of the repo.
    """
    def __init__(self, state: Path, source, host, *, launcher=None, preflight=None, reconcile_writer=None):
        self.state = state
        self.source = source
        self.host = host
        self.launcher = launcher
        self.preflight = preflight
        self.reconcile_writer = reconcile_writer

    def _recover(self):
        marker = self.state / 'writer-active.json'
        if os.path.lexists(marker):
            require(self.reconcile_writer is not None,
                    'writer containment requires host reconciliation')
            with _delivery_lease(self.state):
                # Callback verifies the protected latch and whole-cgroup cleanup.
                # No admission or bridge replay is allowed during reconciliation.
                require(self.reconcile_writer(marker) is True,
                        'writer reconciliation unconfirmed')
                marker.unlink()
                fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        journal = self.state / 'delivery.json'
        # lexists includes dangling symlinks: unsafe evidence cannot mean absent.
        if os.path.lexists(journal):
            return recover(self.state, self.host)
        return None

    def startup(self):
        """Recover before accepting any work; failures propagate and block work."""
        with supervisor_lock(self.state):
            return self._recover()

    @contextmanager
    def writer_session(self):
        """Serialize an entire Codex cycle AND its commit bridge with delivery."""
        with supervisor_lock(self.state):
            self._recover()
            with delivery_lock(self.state):
                # Recheck under lock in case a direct runner interrupted between
                # recovery and acquisition. Do not run a writer on uncertain state.
                require_settled_journal(self.state / 'delivery.json')
                if self.preflight is not None:
                    self.preflight()
                yield

    def run_cycle(self, writer, bridge, *, cwd, writer_timeout, bridge_timeout,
                  grace=1.0):
        """Trusted commands in a host kill-on-stop cgroup; never request commands.

        A durable marker blocks all subsequent operations if this supervisor
        dies or cannot prove cleanup. Protected host reconciliation must verify cgroup
        emptiness before startup can clear it.
        """
        from tools.delivery_process import CleanupError
        from tools.delivery_runner import atomic_json

        require(self.launcher is not None, 'protected writer launcher required')
        with self.writer_session():
            marker = self.state / 'writer-active.json'
            atomic_json(marker, {'schema': 'writer-active-v1'})
            try:
                self.launcher.run('writer', writer, cwd=cwd, timeout=writer_timeout, grace=grace)
                self.launcher.run('bridge', bridge, cwd=cwd, timeout=bridge_timeout, grace=grace)
            except CleanupError:
                raise
            except Exception:
                marker.unlink()
                raise
            else:
                marker.unlink()

    def release(self, request, now=None):
        """Recover then deliver; requests never bypass source or acceptance gates."""
        with supervisor_lock(self.state):
            self._recover()
            if self.preflight is not None:
                self.preflight()
            return deliver(request, self.state, self.source, self.host, now=now)
