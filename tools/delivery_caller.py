"""Durable trusted-caller outbox; no event discovery or host installation.

The host pins the accept callback to InstalledBootstrap.retain_completion and
supplies protected disjoint roots. Handoff means retained, never published.
"""
import hashlib
import os
from pathlib import Path

from tools.delivery_containment import _absolute, _protected
from tools.delivery_entrypoint import protected_bytes
from tools.delivery_observations import (
    ARCHIVE, MAX_PENDING_BYTES, _archive_root, _completion_event,
    _fsync_directory, _pending_paths, _spool_lock, completion_payload,
    retain_completion, _reject_archived_time_conflict,
)
from tools.delivery_runner import DeliveryError, require


class CompletionOutbox:
    def __init__(self, state, receiver_state, accept, *, owner=0):
        self.state = Path(_absolute(str(state)))
        self.receiver_state = Path(_absolute(str(receiver_state)))
        self.accept, self.owner = accept, owner
        self._binding = (self.state, self.receiver_state, accept, owner, os.getpid())
        self._check()

    def _check(self):
        require((self.state, self.receiver_state, self.accept, self.owner, os.getpid())
                == self._binding, 'caller binding changed')
        require(type(self.owner) is int and self.owner >= 0
                and os.getuid() == self.owner and os.geteuid() == self.owner,
                'caller identity required')
        require(callable(self.accept), 'trusted retention callback required')
        for root in (self.state, self.receiver_state):
            _protected(root, self.owner)
            require(root.is_dir(), 'invalid caller root')
        require(not self.state.is_relative_to(self.receiver_state)
                and not self.receiver_state.is_relative_to(self.state),
                'caller and receiver roots must be disjoint')

    def retain(self, **event):
        """Persist before attempting transfer; caller must handle local rejection."""
        return retain_completion(self, **event)

    def handoff(self):
        """Transfer a bounded validated batch; retries preserve original bytes.

        Failures leave unacknowledged events pending. A crash after receiver retention
        repeats that exact identity. Receiver retention also accepts archived retries,
        even when a newer observation exists. No delivery/publication lease is taken.
        """
        self._check()
        with _spool_lock(self.state):
            self._check()
            batch, total, moments = [], 0, {}
            for path in _pending_paths(self.state):
                raw = protected_bytes(path, self.owner)
                total += len(raw)
                require(total <= MAX_PENDING_BYTES, 'caller recovery capacity exceeded')
                digest = hashlib.sha256(raw).hexdigest()
                event = _completion_event(self, path, digest)
                event.pop('schema')
                _, canonical, moment = completion_payload(**event)
                require(raw == canonical, 'noncanonical caller envelope')
                require(path.name == 'completion-' + event['kind'] + '-'
                        + event['event_id'] + '.json', 'caller filename identity mismatch')
                key = (event['kind'], moment)
                require(key not in moments or moments[key] == digest,
                        'observation time conflict')
                moments[key] = digest
                _reject_archived_time_conflict(self, event['kind'], moment, digest)
                batch.append((moment, path.name, path, raw, digest, event))
            transferred = 0
            for _, _, path, raw, digest, event in sorted(batch):
                self._check()
                require(protected_bytes(path, self.owner) == raw, 'caller evidence changed')
                received, pin = self.accept(**event)
                received = Path(_absolute(str(received)))
                require(received in (self.receiver_state / path.name,
                        self.receiver_state / ARCHIVE / path.name)
                        and pin == digest, 'receiver acknowledgement mismatch')
                require(protected_bytes(received, self.owner) == raw,
                        'receiver retention mismatch')
                self._check()
                require(protected_bytes(path, self.owner) == raw, 'caller evidence changed')
                archive = _archive_root(self)
                require(not os.path.lexists(archive / path.name), 'caller archive conflict')
                os.rename(path, archive / path.name)
                _fsync_directory(archive)
                _fsync_directory(self.state)
                transferred += 1
            return {'status': 'RETAINED_BY_RECEIVER', 'transferred': transferred,
                    'publication_verified': False, 'live_money': False}


class CompletionRetryError(DeliveryError):
    """A retry remains incomplete; original phase failures stay inspectable."""
    def __init__(self, failures):
        self.failures = tuple(failures)
        super().__init__('completion retry incomplete: '
                         + ', '.join(phase for phase, _ in self.failures))


class CompletionRetry:
    """One bounded host-scheduled retry, never a delivery or producer replay.

    Installed code supplies the pinned bootstrap recovery callback. Settle first
    to free receiver capacity, then hand off, then settle even a partial handoff.
    No caller spool lease is held while taking receiver operation leases.
    """
    def __init__(self, outbox, settle):
        require(type(outbox) is CompletionOutbox and callable(settle),
                'trusted outbox and settlement callback required')
        self.outbox, self.settle = outbox, settle
        self._binding = (outbox, settle, os.getpid())
        self._check()

    def _check(self):
        require((self.outbox, self.settle, os.getpid()) == self._binding,
                'retry binding changed')
        self.outbox._check()

    def run(self):
        self._check()
        # An unavailable receiver or failed policy gate stops admission this tick.
        # Local retention remains independent, including during writer sessions.
        try:
            self.settle()
        except (DeliveryError, OSError) as exc:
            raise CompletionRetryError([('before_handoff', exc)]) from exc
        self._check()
        failures, handoff = [], None
        try:
            handoff = self.outbox.handoff()
        except (DeliveryError, OSError) as exc:
            failures.append(('handoff', exc))
        # Do not hide partial acceptance behind the handoff failure. Settlement
        # is safe to retry and never clears or replays the writer/delivery job.
        self._check()
        try:
            self.settle()
        except (DeliveryError, OSError) as exc:
            failures.append(('after_handoff', exc))
        self._check()
        if failures:
            raise CompletionRetryError(failures) from failures[0][1]
        return {'status': 'RETRY_COMPLETED', 'transferred': handoff['transferred'],
                'publication_verified': False, 'live_money': False}
