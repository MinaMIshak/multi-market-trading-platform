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
