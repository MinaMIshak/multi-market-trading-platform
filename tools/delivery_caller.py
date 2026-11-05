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
    retain_completion,
)
from tools.delivery_runner import require


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
            batch, total = [], 0
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
