"""Host-private, read-only repair planning. No repair execution or authorization.

The trusted host supplies the request pin and independent originating-job records.
Their semantic authority is NOT established by a hash. Plans bind a bounded scope,
not the entire permanent archive, and never select a winning conflicting event.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from tools.delivery_entrypoint import protected_bytes, _unique
from tools.delivery_observations import _pending_paths, _spool_lock, MAX_PENDING_BYTES
from tools.delivery_runner import require, DeliveryError, _delivery_lease
from tools.delivery_supervisor import supervisor_lock

MAX_REFERENCES = 128


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pinned(path, pin, owner):
    require(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin), 'invalid repair pin')
    raw = protected_bytes(path, owner)
    require(_digest(raw) == pin, 'repair evidence drift')
    return raw


def _reference(name):
    require(type(name) is str and re.fullmatch(
        r'(?:completion-archive/)?completion-[A-Za-z0-9_-]+\.json|'
        r'observation-[A-Za-z0-9_-]+\.json', name), 'invalid repair reference')
    return name


def plan_repair(publication, request_path, request_sha256):
    """Return deterministic private plan + digest under all existing operation leases.

    Caller persists the plan in protected host storage if needed. Missing/unsafe
    files, changed pins or capacity overflow fail closed without partial plans.
    Malformed event bytes may be inventoried: their meaning remains unresolved.
    """
    publication._check()
    with supervisor_lock(publication.state), _delivery_lease(publication.state), \
            _spool_lock(publication.state):
        publication._check()
        raw = _pinned(request_path, request_sha256, publication.owner)
        try:
            request = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, RecursionError):
            raise DeliveryError('malformed repair request') from None
        require(type(request) is dict and set(request) == {
            'schema', 'live_money', 'references', 'originating_records'}, 'invalid repair request')
        require(request['schema'] == 'delivery-repair-request-v1'
                and request['live_money'] is False, 'unsafe repair request')
        refs, origins = request['references'], request['originating_records']
        require(type(refs) is dict and 0 < len(refs) <= MAX_REFERENCES
                and type(origins) is dict and 0 < len(origins) <= MAX_REFERENCES,
                'repair evidence required or capacity exceeded')
        records, total = {}, 0

        def capture(label, path, pin=None):
            nonlocal total
            content = (protected_bytes(path, publication.owner) if pin is None
                       else _pinned(path, pin, publication.owner))
            total += len(content)
            require(total <= MAX_PENDING_BYTES, 'repair planning capacity exceeded')
            records[label] = dict(sha256=_digest(content), bytes=len(content))

        for name, pin in sorted(refs.items()):
            capture(_reference(name), publication.state / name, pin)
        pending = sorted(path.name for path in _pending_paths(publication.state))
        for name in pending:
            capture(name, publication.state / name)
        sidecar = publication.state / 'observations.json'
        if os.path.lexists(sidecar):
            capture('observations.json', sidecar)
        else:
            records['observations.json'] = None
        for name, pin in sorted(origins.items()):
            path = Path(name)
            require(path.is_absolute() and path.resolve() == path
                    and not path.is_relative_to(publication.state),
                    'originating record must be independent of receiver state')
            capture('origin:' + name, path, pin)
        # Recheck all captured bytes, including explicit pins and request, before return.
        for label, record in records.items():
            path = Path(label[7:]) if label.startswith('origin:') else publication.state / label
            if record is None:
                require(not os.path.lexists(path), 'repair evidence drift')
            else:
                _pinned(path, record['sha256'], publication.owner)
        for name, pin in refs.items():
            _pinned(publication.state / name, pin, publication.owner)
        require(pending == sorted(p.name for p in _pending_paths(publication.state)),
                'repair pending inventory drift')
        _pinned(request_path, request_sha256, publication.owner)
        publication._check()
        plan = dict(schema='delivery-repair-plan-v1', request_sha256=request_sha256,
                    receiver_state=str(publication.state), pending=pending, records=records,
                    scope='PENDING_SIDECAR_AND_EXPLICIT_REFERENCES_ONLY',
                    disposition='UNRESOLVED_REQUIRES_ORIGIN_RECONCILIATION',
                    mutation_authorized=False, live_money=False)
        encoded = (json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()
        return plan, _digest(encoded)
