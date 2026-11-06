"""Historical EGID identity projection; not admission or membership certification.

The trusted caller supplies the preserved manifest. Never read today's mutable
security master to reconstruct yesterday's decision. Receipt time establishes
availability only, not admission, licensing, or authoritative listing status.
"""
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from app.data.models import DataAssetType, RawArtifactManifest
from app.data.security_master import InstrumentType, build_canonical_security_master
from app.scanner_v2_egx_rank import CAIRO, _invalid_constant, _unique_object
from app.strategies.contracts import Contract, aware

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10000
MAX_REQUESTED = 1000


class IdentityProjection(Contract):
    schema_version: Literal['scanner-v2-egid-identity-v1'] = 'scanner-v2-egid-identity-v1'
    market: Literal['EGX'] = 'EGX'
    session: date
    observed_at: datetime
    received_at: datetime
    artifact_sha256: str
    instrument_ids: tuple[tuple[str, UUID], ...]
    exclusions: tuple[tuple[str, Literal['IDENTITY_MISSING', 'NOT_EQUITY']], ...]
    evidence_id: str
    admission: Literal['NOT_ESTABLISHED'] = 'NOT_ESTABLISHED'
    live_money: Literal[False] = False


def project_egid_identities(artifact, *, manifest, session, observed_at, requested):
    """Bounded exact-symbol projection, with no file/network/database operations.

    Require a same-session snapshot known by observation time. This conservative
    rule avoids inventing a validity interval for an older identity snapshot.
    No alias guessing or automatic fallback to a current repository is allowed.
    """
    if type(artifact) is not bytes or len(artifact) > MAX_BYTES:
        raise ValueError('bounded exact artifact bytes required')
    if type(manifest) is not RawArtifactManifest:
        raise ValueError('raw artifact manifest required')
    manifest = RawArtifactManifest.model_validate(manifest.model_dump())
    if type(session) is not date:
        raise ValueError('session date required')
    aware(observed_at)
    if (manifest.provider != 'egid' or manifest.asset_type != DataAssetType.SECURITY_MASTER
            or manifest.granularity is not None or manifest.symbol is not None
            or manifest.market_date != session):
        raise ValueError('wrong identity source or session')
    if (aware(manifest.received_at) > observed_at
            or manifest.received_at.astimezone(CAIRO).date() < session
            or observed_at.astimezone(CAIRO).date() < session):
        raise ValueError('identity evidence unavailable at observation')
    if manifest.byte_size != len(artifact) or manifest.sha256 != sha256(artifact).hexdigest():
        raise ValueError('identity artifact integrity mismatch')
    if (type(requested) is not tuple or len(requested) > MAX_REQUESTED
            or any(type(s) is not str or not s or len(s) > 64
                   or s != s.upper() or any(c.isspace() for c in s) for s in requested)
            or len(set(requested)) != len(requested)):
        raise ValueError('bounded unique canonical symbols required')
    records = json.loads(artifact, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    json.dumps(records, allow_nan=False)
    if (type(records) is not list or len(records) > MAX_RECORDS
            or manifest.record_count != len(records)
            or any(type(r) is not dict for r in records)):
        raise ValueError('invalid identity records')
    # Validate identity-bearing fields before the legacy canonicalizer's coercion.
    for record in records:
        if (type(record.get('SYMBOL_CODE')) is not str
                or not record['SYMBOL_CODE'].strip()
                or (record.get('Reuters') is not None and type(record['Reuters']) is not str)):
            raise ValueError('invalid identity fields')
    instruments = build_canonical_security_master(
        records=records, source_sha256=manifest.sha256, source_market_date=session.isoformat())
    by_symbol = {i.canonical_ticker: i for i in instruments}
    identities, exclusions = [], []
    for symbol in sorted(requested):
        item = by_symbol.get(symbol)
        if item is None:
            exclusions.append((symbol, 'IDENTITY_MISSING'))
        elif item.instrument_type != InstrumentType.EQUITY:
            exclusions.append((symbol, 'NOT_EQUITY'))
        else:
            identities.append((symbol, item.instrument_id))
    return IdentityProjection(
        session=session, observed_at=observed_at, received_at=manifest.received_at,
        artifact_sha256=manifest.sha256, instrument_ids=tuple(identities),
        exclusions=tuple(exclusions), evidence_id=f'egid-security-master-sha256:{manifest.sha256}')


class ObservedIdentityProjection(Contract):
    schema_version: Literal['scanner-v2-observed-identity-v1'] = 'scanner-v2-observed-identity-v1'
    identity: IdentityProjection
    available_at: datetime
    receipt_sha256: str
    admission: Literal['INTERNAL_SNAPSHOT_VERIFIED'] = 'INTERNAL_SNAPSHOT_VERIFIED'
    live_money: Literal[False] = False


def bind_observed_egid_identities(artifact, *, manifest, observation, session,
                                  observed_at, requested):
    """Bind trusted retained post-commit evidence, never caller-supplied web input.

    Hashes provide integrity, not authenticity. This verifies internal snapshot
    admission only; calendar, source rights and membership remain separate gates.
    The observation producer must be trusted and its exact envelope preserved.
    """
    identity = project_egid_identities(artifact, manifest=manifest, session=session,
                                     observed_at=observed_at, requested=requested)
    if (type(observation) is not dict or set(observation) != {
            'schema_version', 'observed_at', 'receipt_json', 'receipt_sha256', 'live_money'}
            or observation['schema_version'] != 'security-master-observation-v1'
            or observation['live_money'] is not False):
        raise ValueError('invalid admission observation')
    payload = observation['receipt_json']
    if (type(payload) is not str or len(payload.encode()) > MAX_BYTES * 8
            or sha256(payload.encode()).hexdigest() != observation['receipt_sha256']):
        raise ValueError('admission receipt integrity mismatch')
    receipt = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    json.dumps(receipt, allow_nan=False)
    expected_keys = {'schema_version', 'provider', 'snapshot_date', 'artifact_sha256',
                     'byte_size', 'record_count', 'instrument_count', 'instruments',
                     'policy', 'live_money', 'recorded_at', 'received_at', 'previous_instrument_count'}
    if (type(receipt) is not dict or set(receipt) != expected_keys
            or receipt['schema_version'] != 'security-master-admission-v1'
            or receipt['provider'] != 'egid' or receipt['snapshot_date'] != session.isoformat()
            or receipt['artifact_sha256'] != manifest.sha256
            or receipt['live_money'] is not False
            or receipt['policy'] != 'EGID_EQUITY_NONEMPTY_RETAIN_90_PERCENT_V1'):
        raise ValueError('admission source or policy mismatch')
    available_at = aware(datetime.fromisoformat(observation['observed_at']))
    recorded_at = aware(datetime.fromisoformat(receipt['recorded_at']))
    received_at = aware(datetime.fromisoformat(receipt['received_at']))
    if not (received_at <= recorded_at <= available_at <= observed_at):
        raise ValueError('admission unavailable at observation')
    # Exact retries may have a later raw receipt; never replace the original
    # admitted receipt or use the later manifest to backdate availability.
    if received_at > manifest.received_at:
        raise ValueError('manifest predates admitted receipt')
    records = json.loads(artifact, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    canonical = build_canonical_security_master(records=records,
        source_sha256=manifest.sha256, source_market_date=session.isoformat())
    expected = sorted((i.model_dump(mode='json') for i in canonical), key=lambda i: i['instrument_id'])
    for key, value in [('byte_size', len(artifact)), ('record_count', len(records)),
                       ('instrument_count', len(expected))]:
        if type(receipt[key]) is not int or receipt[key] != value:
            raise ValueError('admission count mismatch')
    previous = receipt['previous_instrument_count']
    if (type(previous) is not int or previous < 0 or len(expected) * 10 < previous * 9
            or not any(i.instrument_type == InstrumentType.EQUITY for i in canonical)):
        raise ValueError('admission completeness mismatch')
    if receipt['instruments'] != expected:
        raise ValueError('raw and admitted canonical snapshot disagree')
    return ObservedIdentityProjection(identity=identity, available_at=available_at,
                                      receipt_sha256=observation['receipt_sha256'])
