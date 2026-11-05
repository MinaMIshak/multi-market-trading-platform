"""Trusted host event ingestion; callers attest semantics, never repository code.

Call outside supervisor/delivery leases. retain_completion alone may run during an
operation that holds them. Evidence must already be protected and bounded. No log
guessing, subprocess execution, or release authorization occurs.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from app.delivery_status import observation_records, OBSERVATIONS
from tools.delivery_entrypoint import protected_bytes, _unique
from tools.delivery_runner import atomic_json, require, DeliveryError, _delivery_lease
from tools.delivery_supervisor import supervisor_lock

# These paths use the lease rather than delivery_lock: an unreconciled writer latch
# must not stop reporting that cycle as interrupted. They only append private
# observations; the leases exclude live sessions and the latch is left untouched.

# Settled envelopes move here so restart recovery only scans unsettled work.
ARCHIVE = 'completion-archive'
# Bounded wait: holders only do short file work, unlike the operation leases.
SPOOL_WAIT = 30.0
MAX_PENDING_EVENTS = 128
MAX_PENDING_BYTES = 8 * 1024 * 1024
# Pinned code policy: leave recovery headroom as permanent evidence accumulates.
MIN_FREE_BYTES = 64 * 1024 * 1024
MIN_FREE_INODES = 256


def _disk_admission(state, pending_bytes, pending_count):
    """Advisory free-space gate, not a filesystem reservation or quota guarantee.

    Budget the envelope plus hex receipt, block rounding and atomic replacements
    for the whole pending batch. Existing retries and settlement bypass this gate.
    Unknown inode accounting fails closed; host provisioning must support it.
    """
    usage = os.statvfs(state)
    require(usage.f_frsize > 0 and usage.f_bavail >= 0 and usage.f_favail >= 0,
            'completion storage accounting unavailable')
    overhead = max(16384, 4 * usage.f_frsize) * pending_count
    require(usage.f_bavail * usage.f_frsize >=
            MIN_FREE_BYTES + 3 * pending_bytes + overhead,
            'completion storage byte headroom exhausted')
    require(usage.f_favail >= MIN_FREE_INODES + 2 * pending_count,
            'completion storage inode headroom exhausted')


@contextmanager
def _spool_lock(state):
    """Serialize retained-event files and the sidecar, separately from operations.

    A writer session holds the leases for hours, and a chain finishing meanwhile must
    still be retained. Lock order is supervisor, delivery, spool; retention takes only
    this lock, so it cannot deadlock against them. Never hold it across a publication
    or a delivery operation.
    """
    require(state.is_absolute() and state.resolve() == state and state.is_dir(),
            'invalid completion state directory')
    fd = os.open(state / 'completion-spool.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'unsafe completion spool lock')
        deadline = time.monotonic() + SPOOL_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, 'completion spool busy')
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _pending_paths(state):
    """Bound the scan before reading envelopes; caller holds the spool lock."""
    paths = []
    with os.scandir(state) as entries:
        for entry in entries:
            if entry.name.startswith('completion-') and entry.name.endswith('.json'):
                paths.append(state / entry.name)
                require(len(paths) <= MAX_PENDING_EVENTS,
                        'completion recovery capacity exceeded')
    return paths


def _admit_completion(publication, size):
    """Reject overflow before retaining it, so accepted work can still settle.

    Identical retries bypass admission. Never prune evidence or interpret an
    unreadable sibling as free space. This bounds pending work, not disk usage
    of the permanent archive; the host must still provision archive capacity.
    """
    paths = _pending_paths(publication.state)
    require(len(paths) < MAX_PENDING_EVENTS, 'completion retention capacity exceeded')
    total = size
    for path in paths:
        total += len(protected_bytes(path, publication.owner))
        require(total <= MAX_PENDING_BYTES, 'completion retention capacity exceeded')
    require(total <= MAX_PENDING_BYTES, 'completion retention capacity exceeded')
    _disk_admission(publication.state, total, len(paths) + 1)


def _existing_observations(publication):
    """Read the current sidecar under the caller's leases; absent means empty."""
    sidecar = publication.state / 'observations.json'
    if not os.path.lexists(sidecar):
        return {}
    content = protected_bytes(sidecar, publication.owner)
    try:
        raw = json.loads(content, object_pairs_hook=_unique)
    except (ValueError, RecursionError, DeliveryError):
        raise DeliveryError('invalid existing observations') from None
    current = observation_records(raw)
    require(type(raw) is dict and raw.get('schema') == 'delivery-observations-v1'
            and set(raw) == {'schema', *current}, 'invalid existing observations')
    return current


def _fsync_directory(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _archive_root(publication):
    root = publication.state / ARCHIVE
    if not os.path.lexists(root):
        os.mkdir(root, 0o700)
        _fsync_directory(publication.state)
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == publication.owner
            and not info.st_mode & 0o022, 'unsafe completion archive')
    return root


def _archive_settled(publication, paths):
    """Move published envelopes out of the recovery scan, never deleting evidence.

    An envelope moves only once its exact bytes are in the durable receipt and the
    sidecar is at or past its event time. Failure here cannot undo a publication
    that already succeeded, so the envelope stays for a later recovery to settle.
    """
    try:
        with supervisor_lock(publication.state), _delivery_lease(publication.state), \
                _spool_lock(publication.state):
            publication._check()
            current = _existing_observations(publication)
            for path in map(Path, paths):
                raw = protected_bytes(path, publication.owner)
                event = _completion_event(publication, path, hashlib.sha256(raw).hexdigest())
                kind, event_id = event['kind'], event['event_id']
                # Host-named callback files and archived retries are not retained work.
                if (path.parent != publication.state
                        or path.name != 'completion-' + kind + '-' + event_id + '.json'):
                    continue
                receipt = json.loads(protected_bytes(
                    publication.state / ('observation-' + kind + '-' + event_id + '.json'),
                    publication.owner), object_pairs_hook=_unique)
                prior = current.get(kind)
                if (bytes.fromhex(receipt['evidence_hex']) != raw or not prior
                        or datetime.fromisoformat(event['observed_at'])
                        > datetime.fromisoformat(prior['observed_at'])):
                    continue
                root = _archive_root(publication)
                require(not os.path.lexists(root / path.name), 'completion archive conflict')
                os.rename(path, root / path.name)
                _fsync_directory(root)
                _fsync_directory(publication.state)
    except (DeliveryError, OSError, ValueError, KeyError, TypeError, RecursionError):
        pass


def _reject_time_conflict(publication, kind, moment, digest):
    """Keep an ambiguous event out of retention, where it would block all recovery.

    Unreadable state is left for recording and recovery to reject: damage elsewhere
    must not stop this event's evidence from being retained.
    """
    try:
        prior = _existing_observations(publication).get(kind)
    except (DeliveryError, OSError):
        prior = None
    require(not prior or datetime.fromisoformat(prior['observed_at']) != moment
            or prior['evidence_sha256'] == digest, 'observation time conflict')
    prefix = 'completion-' + kind + '-'
    with os.scandir(publication.state) as entries:
        names = [e.name for e in entries if e.name.startswith(prefix) and e.name.endswith('.json')]
    for name in names:
        try:
            raw = protected_bytes(publication.state / name, publication.owner)
            other = _completion_event(publication, publication.state / name,
                                      hashlib.sha256(raw).hexdigest())
            observed = datetime.fromisoformat(other['observed_at'])
        except (DeliveryError, OSError, ValueError, TypeError):
            continue
        require(observed != moment, 'observation time conflict')


class ObservationProducer:
    def __init__(self, publication):
        self.publication = publication

    def record(self, kind, *, event_id, source_head, status, observed_at,
               evidence_path, live_money, now=None, expected_evidence_sha256=None):
        publisher = self.publication
        publisher._check()
        require(isinstance(kind, str) and kind in OBSERVATIONS and isinstance(event_id, str)
                and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
        with supervisor_lock(publisher.state), _delivery_lease(publisher.state), \
                _spool_lock(publisher.state):
            publisher._check()
            evidence = protected_bytes(evidence_path, publisher.owner)
            digest = hashlib.sha256(evidence).hexdigest()
            require(expected_evidence_sha256 is None or digest == expected_evidence_sha256,
                    'completion evidence pin mismatch')
            record = dict(status=status, source_head=source_head, observed_at=observed_at,
                          evidence_sha256=digest, live_money=live_money)
            records = observation_records(dict(schema='delivery-observations-v1', **{kind: record}))
            require(kind in records, 'invalid observation')
            record = records[kind]
            moment = datetime.fromisoformat(record['observed_at'])
            require(moment <= (now or datetime.now(timezone.utc)), 'future observation')
            # Write-once event receipt includes identity, digest and exact evidence bytes.
            # Persist it before advancing the sidecar; retry after interruption is safe.
            archive = publisher.state / ('observation-' + kind + '-' + event_id + '.json')
            receipt = dict(schema='delivery-observation-event-v1', kind=kind,
                           event_id=event_id, record=record, evidence_hex=evidence.hex())
            if os.path.lexists(archive):
                existing = json.loads(protected_bytes(archive, publisher.owner), object_pairs_hook=_unique)
                require(existing == receipt, 'event identity conflict')
            else:
                # Hex expansion must remain readable by the protected reader.
                require(len(json.dumps(receipt).encode()) < 1000000, 'event evidence too large')
                atomic_json(archive, receipt, mode=0o400)
            sidecar = publisher.state / 'observations.json'
            current = _existing_observations(publisher)
            prior = current.get(kind)
            if prior:
                previous = datetime.fromisoformat(prior['observed_at'])
                require(moment >= previous, 'stale observation')
                require(moment != previous or prior == record, 'observation time conflict')
            current[kind] = record
            atomic_json(sidecar, dict(schema='delivery-observations-v1', **current))
        return publisher.publish()


def _completion_event(publication, path, digest):
    """Read a protected envelope and verify its exact byte digest."""
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest),
            'invalid completion pin')
    raw = protected_bytes(path, publication.owner)
    require(hashlib.sha256(raw).hexdigest() == digest, 'completion evidence pin mismatch')
    try:
        event = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise DeliveryError('malformed completion envelope') from None
    require(type(event) is dict and set(event) == {
        'schema', 'kind', 'event_id', 'source_head', 'status', 'observed_at',
        'live_money', 'evidence'}, 'invalid completion fields')
    require(event['schema'] == 'delivery-completion-v1'
            and type(event['evidence']) is str and bool(event['evidence'].strip()),
            'invalid completion evidence')
    return event


def record_completion(publication, path, digest):
    event = _completion_event(publication, path, digest)
    result = ObservationProducer(publication).record(event['kind'],
        event_id=event['event_id'], source_head=event['source_head'],
        status=event['status'], observed_at=event['observed_at'],
        live_money=event['live_money'], evidence_path=path,
        expected_evidence_sha256=digest)
    _archive_settled(publication, [path])
    return result


def completion_payload(*, kind, event_id, source_head, status, observed_at,
                       live_money, evidence):
    """Shared strict validation and canonical bytes for durable caller handoff."""
    require(type(kind) is str and kind in OBSERVATIONS and type(event_id) is str
            and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
    require(type(evidence) is str and bool(evidence.strip()), 'invalid completion evidence')
    record = dict(status=status, source_head=source_head, observed_at=observed_at,
                  live_money=live_money, evidence_sha256='0' * 64)
    records = observation_records(dict(schema='delivery-observations-v1', **{kind: record}))
    require(kind in records, 'invalid observation')
    moment = datetime.fromisoformat(records[kind]['observed_at'])
    require(moment <= datetime.now(timezone.utc), 'future observation')
    event = dict(schema='delivery-completion-v1', kind=kind, event_id=event_id,
                 source_head=source_head, status=status, observed_at=observed_at,
                 live_money=live_money, evidence=evidence)
    # Match atomic_json and bound the later hex-encoded archive too.
    raw = (json.dumps(event, sort_keys=True, indent=2) + '\n').encode()
    require(len(raw) * 2 + 4096 < 1000000, 'event evidence too large')
    return event, raw, moment


def retain_completion(publication, *, kind, event_id, source_head, status,
                      observed_at, live_money, evidence):
    """Retain a trusted caller's event before dispatch; identical retries only.

    Callers preserve original event IDs/timestamps across restart. No inference,
    automatic discovery, deletion or delivery operation is performed here. A retry
    of a settled event returns its archived path, so its identity cannot be reused.
    Takes only the spool lock, so an event survives a concurrent writer session.
    """
    publication._check()
    event, raw, moment = completion_payload(kind=kind, event_id=event_id,
        source_head=source_head, status=status, observed_at=observed_at,
        live_money=live_money, evidence=evidence)
    path = publication.state / ('completion-' + kind + '-' + event_id + '.json')
    digest = hashlib.sha256(raw).hexdigest()
    with _spool_lock(publication.state):
        publication._check()
        archived = publication.state / ARCHIVE / path.name
        if not os.path.lexists(path) and os.path.lexists(archived):
            path = archived
        if os.path.lexists(path):
            require(protected_bytes(path, publication.owner) == raw, 'completion identity conflict')
        else:
            _admit_completion(publication, len(raw))
            _reject_time_conflict(publication, kind, moment, digest)
            atomic_json(path, event, mode=0o400)
        require(protected_bytes(path, publication.owner) == raw, 'completion retention mismatch')
    return path, digest


def recover_completions(publication):
    """Validate a bounded retained batch before advancing historical observations."""
    publication._check()
    with supervisor_lock(publication.state), _delivery_lease(publication.state), \
            _spool_lock(publication.state):
        publication._check()
        paths = _pending_paths(publication.state)
        sidecar = publication.state / 'observations.json'
        # With nothing to merge, a damaged sidecar is publication's to clear, not a failure.
        current = _existing_observations(publication) if paths else {}
        receipts, total = [], 0
        moments = {(kind, datetime.fromisoformat(record['observed_at'])): record
                   for kind, record in current.items()}
        now = datetime.now(timezone.utc)
        for path in sorted(paths):
            evidence = protected_bytes(path, publication.owner)
            total += len(evidence)
            require(total <= MAX_PENDING_BYTES, 'completion recovery capacity exceeded')
            digest = hashlib.sha256(evidence).hexdigest()
            event = _completion_event(publication, path, digest)
            kind, event_id = event['kind'], event['event_id']
            require(type(kind) is str and kind in OBSERVATIONS and type(event_id) is str
                    and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
            require(path.name == 'completion-' + kind + '-' + event_id + '.json',
                    'completion filename identity mismatch')
            record = {key: event[key] for key in
                      ('status', 'source_head', 'observed_at', 'live_money')}
            record['evidence_sha256'] = digest
            records = observation_records(dict(schema='delivery-observations-v1', **{kind: record}))
            require(kind in records, 'invalid observation')
            record = records[kind]
            moment = datetime.fromisoformat(record['observed_at'])
            require(moment <= now, 'future observation')
            key = (kind, moment)
            require(key not in moments or moments[key] == record, 'observation time conflict')
            moments[key] = record
            receipt = dict(schema='delivery-observation-event-v1', kind=kind,
                           event_id=event_id, record=record, evidence_hex=evidence.hex())
            require(len(json.dumps(receipt).encode()) < 1000000, 'event evidence too large')
            archive = publication.state / ('observation-' + kind + '-' + event_id + '.json')
            if os.path.lexists(archive):
                existing = json.loads(protected_bytes(archive, publication.owner), object_pairs_hook=_unique)
                require(existing == receipt, 'event identity conflict')
            else:
                receipts.append((archive, receipt))
            prior = current.get(kind)
            previous = datetime.fromisoformat(prior['observed_at']) if prior else None
            require(previous != moment or prior == record, 'observation time conflict')
            if previous is None or moment > previous:
                current[kind] = record
        for archive, receipt in receipts:
            atomic_json(archive, receipt, mode=0o400)
        if paths:
            atomic_json(sidecar, dict(schema='delivery-observations-v1', **current))
    result = publication.publish()
    _archive_settled(publication, paths)
    return result
