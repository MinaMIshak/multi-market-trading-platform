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
               evidence_path, live_money, now=None):
        publisher = self.publication
        publisher._check()
        require(kind in OBSERVATIONS and isinstance(event_id, str)
                and re.fullmatch('[A-Za-z0-9_-]{1,128}', event_id), 'invalid event identity')
        with supervisor_lock(publisher.state), delivery_lock(publisher.state):
            publisher._check()
            evidence = protected_bytes(evidence_path, publisher.owner)
            digest = hashlib.sha256(evidence).hexdigest()
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
