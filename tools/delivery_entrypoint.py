"""Protected host invocation boundary; no request-selected imports or commands.

An installed host bootstrap supplies the pinned digest and composition factory.
This module is not a privileged CLI and does not install host isolation.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from tools.delivery_containment import _absolute, _protected
from tools.delivery_runner import DeliveryError, require
from tools.delivery_supervisor import DeliverySupervisor

LIMIT = 1024 * 1024


def protected_bytes(path, owner):
    path = Path(_absolute(str(path)))
    _protected(path, owner)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner
                and not before.st_mode & 0o022 and before.st_size <= LIMIT,
                'unsafe host configuration')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            content = stream.read(LIMIT + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(content) <= LIMIT and identity(before) == identity(after)
                and identity(path.lstat()) == identity(after),
                'host configuration changed during read')
        return content
    finally:
        os.close(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate host configuration key')
        result[key] = value
    return result


class PublicationFailure(DeliveryError):
    """Retain both errors for trusted supervisor reporting; no public error text."""
    def __init__(self, operation_error, publication_error):
        super().__init__('delivery publication failed after host operation')
        self.operation_error = operation_error
        self.publication_error = publication_error


class HostEntrypoint:
    def __init__(self, config, digest, factory, *, owner=0):
        require(type(owner) is int and owner >= 0 and os.getuid() == owner
                and os.geteuid() == owner, 'host supervisor identity required')
        self.owner, self.pid = owner, os.getpid()
        self.config, self.digest = Path(config), digest
        raw = protected_bytes(self.config, owner)
        require(hashlib.sha256(raw).hexdigest() == digest, 'host configuration pin mismatch')
        try:
            policy = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, RecursionError):
            raise DeliveryError('malformed host configuration') from None
        require(type(policy) is dict, 'invalid host configuration')
        fields = {'schema', 'state', 'repository', 'writer', 'bridge', 'live_money'}
        if policy.get('schema') == 'delivery-host-v2':
            fields.add('publication')
        require(set(policy) == fields,
            'invalid host configuration fields')
        require(policy['schema'] in ('delivery-host-v1', 'delivery-host-v2') and policy['live_money'] is False,
                'unsafe host configuration')
        self.state = Path(_absolute(policy['state']))
        self.repository = Path(_absolute(policy['repository']))
        _protected(self.state, owner)
        require(self.state.is_dir() and self.repository.is_dir()
                and self.repository.resolve() == self.repository
                and not self.state.is_relative_to(self.repository)
                and not self.repository.is_relative_to(self.state), 'unsafe host roots')
        self.publication = None
        if policy['schema'] == 'delivery-host-v2':
            from tools.delivery_publication import DeliveryPublication
            destination = Path(_absolute(policy['publication']))
            require(not destination.parent.is_relative_to(self.repository)
                    and not self.repository.is_relative_to(destination.parent),
                    'publication must be separate from repository')
            self.publication = DeliveryPublication(self.state, destination, owner=owner)
        self._publication = self.publication
        self._publication_binding = (None if self.publication is None else
                                     (self.state, self.publication.destination, owner))
        self.commands = {}
        for name in ('writer', 'bridge'):
            command = policy[name]
            require(type(command) is dict and set(command) == {'argv', 'timeout'},
                    'invalid host command')
            argv, timeout = command['argv'], command['timeout']
            require(type(argv) is list and argv and all(type(a) is str and a
                    and '\x00' not in a for a in argv), 'invalid host argv')
            executable = Path(_absolute(argv[0]))
            _protected(executable, owner)
            require(executable.is_file() and os.access(executable, os.X_OK),
                    'invalid host executable')
            require(type(timeout) in (int, float) and math.isfinite(timeout)
                    and 0 < timeout <= 86400, 'invalid host timeout')
            self.commands[name] = (tuple(argv), timeout)
        # Factory is supplied by protected installed code, never JSON/import strings.
        supervisor = factory()
        require(type(supervisor) is DeliverySupervisor and supervisor.state == self.state
                and supervisor.preflight is not None and supervisor.launcher is not None,
                'pinned host composition required')
        require(Path(supervisor.source.repository) == self.repository,
                'host source binding mismatch')
        self.supervisor = supervisor
        self._bindings = (supervisor, supervisor.source, supervisor.host,
                          supervisor.launcher, supervisor.preflight, supervisor.reconcile_writer)

    def _check(self):
        require(os.getuid() == self.owner and os.geteuid() == self.owner
                and os.getpid() == self.pid, 'host supervisor identity changed')
        require(hashlib.sha256(protected_bytes(self.config, self.owner)).hexdigest()
                == self.digest, 'host configuration pin mismatch')
        _protected(self.state, self.owner)
        require(all(current is pinned for current, pinned in zip(
                    (self.supervisor, self.supervisor.source, self.supervisor.host,
                     self.supervisor.launcher, self.supervisor.preflight,
                     self.supervisor.reconcile_writer), self._bindings)),
                'host composition changed')
        require(self.supervisor.state == self.state
                and Path(self.supervisor.source.repository) == self.repository,
                'host binding changed')
        require(self.publication is self._publication, 'publication binding changed')
        if self.publication is not None:
            require((self.publication.state, self.publication.destination,
                     self.publication.owner) == self._publication_binding,
                    'publication binding changed')
            self.publication._check()
        for argv, _ in self.commands.values():
            _protected(Path(argv[0]), self.owner)

    def report_completion(self, **event):
        """Retain a host-attested event before observation dispatch."""
        from tools.delivery_observations import retain_completion
        self._check()
        require(self.publication is not None, 'completion requires delivery-host-v2')
        path, digest = retain_completion(self.publication, **event)
        return self.completion(path, digest)

    def completion(self, path, digest):
        """Trusted historical callback outside leases; never executes operations."""
        from tools.delivery_observations import record_completion
        self._check()
        require(self.publication is not None, 'completion requires delivery-host-v2')
        path = Path(_absolute(str(path)))
        require(path.is_relative_to(self.state), 'completion must be in private host state')
        return record_completion(self.publication, path, digest)

    def invoke(self, operation, request=None):
        self._check()
        require(operation in ('startup', 'cycle', 'release'), 'unknown host operation')
        require((operation == 'release' and type(request) is dict)
                or (operation != 'release' and request is None), 'invalid host invocation')
        operation_error = None
        try:
            return self._invoke(operation, request)
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            # Both operation leases are released before publication reacquires them.
            # Rejected policy/request checks above never trigger publication.
            if self.publication is not None:
                try:
                    self.publication.publish()
                except Exception as exc:
                    raise PublicationFailure(operation_error, exc) from exc

    def _invoke(self, operation, request):
        # Every entry, including a fresh process after restart, reconciles first.
        # Each operation reacquires the supervisor lease and rechecks recovery.
        self.supervisor.startup()
        if operation == 'startup':
            return None
        if operation == 'release':
            return self.supervisor.release(request)
        writer, writer_timeout = self.commands['writer']
        bridge, bridge_timeout = self.commands['bridge']
        return self.supervisor.run_cycle(writer, bridge, cwd=self.repository,
                                         writer_timeout=writer_timeout,
                                         bridge_timeout=bridge_timeout)
