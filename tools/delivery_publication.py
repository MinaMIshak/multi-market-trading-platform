"""Host-owned, sanitized delivery publication; no production installation.

Invoke after supervisor operations (including failures), outside their leases.
The public directory must be disjoint from private state and writable only by the
trusted host owner. Mount only that directory read-only into the API.
"""
import json
import os
from pathlib import Path

from app.delivery_status import public_receipt, observation_records
from tools.delivery_containment import _absolute, _protected
from tools.delivery_entrypoint import protected_bytes, _unique
from tools.delivery_runner import atomic_json, delivery_lock, require, DeliveryError
from tools.delivery_supervisor import supervisor_lock


class DeliveryPublication:
    def __init__(self, state, destination, *, owner=0):
        self.state = Path(_absolute(str(state)))
        self.destination = Path(_absolute(str(destination)))
        self.owner = owner
        require(type(owner) is int and owner >= 0, 'invalid publication owner')
        require(not self.destination.parent.is_relative_to(self.state)
                and not self.state.is_relative_to(self.destination.parent),
                'publication must be separate from private state')
        self._check()

    def _check(self):
        require(os.getuid() == self.owner and os.geteuid() == self.owner,
                'publication identity required')
        _protected(self.state, self.owner)
        _protected(self.destination.parent, self.owner)
        require(self.state.is_dir() and self.destination.parent.is_dir(),
                'invalid publication roots')
        if os.path.lexists(self.destination):
            _protected(self.destination, self.owner)
            require(self.destination.is_file(), 'invalid publication destination')

    def publish(self):
        self._check()
        with supervisor_lock(self.state), delivery_lock(self.state):
            self._check()
            journal = self.state / 'delivery.json'
            try:
                if not os.path.lexists(journal):
                    raw = dict(schema='delivery-observation-publication-v1')
                else:
                    raw = json.loads(protected_bytes(journal, self.owner), object_pairs_hook=_unique)
                # Journal metadata is not an observation authority.
                if isinstance(raw, dict):
                    raw.pop('observations', None)
                    try:
                        observations = json.loads(protected_bytes(
                            self.state / 'observations.json', self.owner), object_pairs_hook=_unique)
                        records = observation_records(observations)
                    except (OSError, ValueError, RecursionError, DeliveryError):
                        records = {}
                    if records:
                        raw['observations'] = dict(schema='delivery-observations-v1', **records)
                result = public_receipt(raw)
                require(os.path.lexists(journal) or bool(result),
                        'delivery publication source unavailable')
            except (OSError, ValueError, RecursionError, DeliveryError):
                # Replace historical success with UNKNOWN when source is unreadable.
                atomic_json(self.destination, {}, mode=0o644)
                raise DeliveryError('delivery publication source unavailable') from None
            atomic_json(self.destination, result, mode=0o644)
            return result
