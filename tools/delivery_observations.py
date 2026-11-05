"""Trusted host event ingestion; callers attest semantics, never repository code.

Call outside supervisor/delivery leases. Evidence must already be protected and
bounded. No log guessing, subprocess execution, or release authorization occurs.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from app.delivery_status import observation_records, OBSERVATIONS
from tools.delivery_entrypoint import protected_bytes, _unique
from tools.delivery_runner import atomic_json, delivery_lock, require
from tools.delivery_supervisor import supervisor_lock


class ObservationProducer:
    def __init__(self, publication):
        self.publication = publication

    def record(self, kind, *, event_id, source_head, status, observed_at,
               evidence_path, live_money, now=None, expected_evidence_sha256=None):
        publisher = self.publication
        publisher._check()
        require(isinstance(kind, str) and kind in OBSERVATIONS and isinstance(event_id, str)
                and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
        with supervisor_lock(publisher.state), delivery_lock(publisher.state):
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
            current = {}
            if os.path.lexists(sidecar):
                raw = json.loads(protected_bytes(sidecar, publisher.owner), object_pairs_hook=_unique)
                current = observation_records(raw)
                require(isinstance(raw, dict) and raw.get('schema') == 'delivery-observations-v1'
                        and set(raw) == {'schema', *current}, 'invalid existing observations')
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
        from tools.delivery_runner import DeliveryError
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
    return ObservationProducer(publication).record(event['kind'],
        event_id=event['event_id'], source_head=event['source_head'],
        status=event['status'], observed_at=event['observed_at'],
        live_money=event['live_money'], evidence_path=path,
        expected_evidence_sha256=digest)


def retain_completion(publication, *, kind, event_id, source_head, status,
                      observed_at, live_money, evidence):
    """Retain a trusted caller's event before dispatch; identical retries only.

    Callers preserve original event IDs/timestamps across restart. No inference,
    automatic discovery, deletion or delivery operation is performed here.
    """
    publication._check()
    require(type(kind) is str and kind in OBSERVATIONS and type(event_id) is str
            and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
    require(type(evidence) is str and bool(evidence.strip()), 'invalid completion evidence')
    record = dict(status=status, source_head=source_head, observed_at=observed_at,
                  live_money=live_money, evidence_sha256='0' * 64)
    records = observation_records(dict(schema='delivery-observations-v1', **{kind: record}))
    require(kind in records, 'invalid observation')
    require(datetime.fromisoformat(records[kind]['observed_at']) <= datetime.now(timezone.utc),
            'future observation')
    event = dict(schema='delivery-completion-v1', kind=kind, event_id=event_id,
                 source_head=source_head, status=status, observed_at=observed_at,
                 live_money=live_money, evidence=evidence)
    # Match atomic_json and bound the later hex-encoded archive too.
    raw = (json.dumps(event, sort_keys=True, indent=2) + '\n').encode()
    require(len(raw) * 2 + 4096 < 1000000, 'event evidence too large')
    path = publication.state / ('completion-' + kind + '-' + event_id + '.json')
    with supervisor_lock(publication.state), delivery_lock(publication.state):
        publication._check()
        if os.path.lexists(path):
            require(protected_bytes(path, publication.owner) == raw, 'completion identity conflict')
        else:
            atomic_json(path, event, mode=0o400)
        require(protected_bytes(path, publication.owner) == raw, 'completion retention mismatch')
    return path, hashlib.sha256(raw).hexdigest()


def recover_completions(publication):
    """Validate a bounded retained batch before advancing historical observations."""
    publication._check()
    with supervisor_lock(publication.state), delivery_lock(publication.state):
        publication._check()
        paths = []
        with os.scandir(publication.state) as entries:
            for entry in entries:
                if entry.name.startswith('completion-') and entry.name.endswith('.json'):
                    paths.append(publication.state / entry.name)
                    require(len(paths) <= 128, 'completion recovery capacity exceeded')
        current = {}
        sidecar = publication.state / 'observations.json'
        if os.path.lexists(sidecar):
            raw = json.loads(protected_bytes(sidecar, publication.owner), object_pairs_hook=_unique)
            current = observation_records(raw)
            require(type(raw) is dict and raw.get('schema') == 'delivery-observations-v1'
                    and set(raw) == {'schema', *current}, 'invalid existing observations')
        receipts, total = [], 0
        moments = {(kind, datetime.fromisoformat(record['observed_at'])): record
                   for kind, record in current.items()}
        now = datetime.now(timezone.utc)
        for path in sorted(paths):
            evidence = protected_bytes(path, publication.owner)
            total += len(evidence)
            require(total <= 8 * 1024 * 1024, 'completion recovery capacity exceeded')
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
    return publication.publish()
