"""Load EGX outcome daily evidence from explicitly named retained raw ingestions.

Phase G/H collection step (decision-outcome-daily-evidence-v1). The outcome
producer ``app.decision_outcome_producer`` takes caller-supplied
``EgxDailyEvidence`` (canonical daily rows of one instrument plus the receipt
time of their exact bytes). This library is the retained-evidence source for
that input; it adds no binding, evaluation, record or store logic:

1. each ``data_ingestions.raw_path`` named by the caller is resolved with the
   unchanged ``DataIngestionRepository.get_manifest_by_raw_path``; nothing is
   searched for, defaulted or chosen as "latest";
2. the ingestion must be DAILY_BARS/D1, not REJECTED, carry a source URI and
   the ingestor's metadata (instrument UUID, canonical/provider symbol,
   snapshot and requested dates) consistent with the manifest;
3. the exact raw bytes are read and canonicalized by the unchanged
   ``DailyCanonicalPipeline.canonicalize_ingestion`` (size and SHA-256 checked
   by ``ImmutableRawStore.read_verified``); every row must lie inside the
   requested date range;
4. ``received_at`` is the ingestion manifest's receipt time of those bytes.

Two named ingestions for one instrument are refused (DUPLICATE_INSTRUMENT)
rather than resolved by preference, so the evidence each outcome uses is always
the one the caller named. Quarantined rows are passed through unchanged; the
session-bar binding rejects them on the outcome path. Any failure refuses the
whole load with one fixed reason (no partial mapping is returned).

The receipt time is the database's record, labelled
INGESTION_MANIFEST_UNVERIFIED (it is not independently attested). Market
membership is not established here: an instrument that matches no EGX
observation is simply unused, and the binding re-checks instrument UUID and
symbol against every record. Corporate actions NOT_CHECKED. Read only: the
database is never created, initialized, upgraded or written. No job
entrypoint, scheduler, route or caller yet. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import date, datetime
import json
import os
import sqlite3
from uuid import UUID

from app.data.daily_canonical_pipeline import DailyCanonicalPipeline
from app.data.daily_ingestion import DailyBarIngestionResult
from app.data.ingestion_repository import DataIngestionRepository
from app.data.models import BarGranularity, DataAssetType
from app.data.raw_store import ImmutableRawStore
from app.decision_outcome_producer import EgxDailyEvidence

CONTRACT_VERSION = 'decision-outcome-daily-evidence-v1'
LOAD_REASONS = (
    'CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'INGESTION_UNAVAILABLE', 'INGESTION_REJECTED',
    'ARTIFACT_UNREADABLE', 'CANONICALIZATION_REJECTED', 'DUPLICATE_INSTRUMENT')
ACCEPTED_INGESTION_STATUSES = ('RECEIVED', 'VALIDATED')
MAX_RAW_PATHS = 1000
MAX_DETAIL = 200


class DailyEvidenceLoadError(RuntimeError):
    """Load refused; ``reason`` is one of LOAD_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _fail(reason, raw_path, why):
    raise DailyEvidenceLoadError(reason, f'{raw_path}: {why}'[:MAX_DETAIL])


def _raw_paths(raw_paths):
    if (type(raw_paths) is not tuple or not 0 < len(raw_paths) <= MAX_RAW_PATHS
            or any(type(p) is not str or not p or '\x00' in p for p in raw_paths)):
        raise DailyEvidenceLoadError(
            'CONFIG_INVALID', f'tuple of 1..{MAX_RAW_PATHS} nonempty raw paths required')
    if len(set(raw_paths)) != len(raw_paths):
        raise DailyEvidenceLoadError('CONFIG_INVALID', 'duplicate raw path')
    return raw_paths


def _iso_date(metadata, key):
    value = metadata.get(key)
    if type(value) is not str:
        raise ValueError(f'metadata {key} missing')
    return date.fromisoformat(value)


def _ingestion(database, raw_path):
    """Manifest plus the ingestion row's status, source URI and metadata."""
    try:
        manifest = DataIngestionRepository(database).get_manifest_by_raw_path(raw_path)
        if manifest is None:
            _fail('INGESTION_UNAVAILABLE', raw_path, 'no ingestion for raw path')
        with database.connect() as connection:
            row = connection.execute(
                'SELECT status, source_uri, metadata_json FROM data_ingestions '
                'WHERE ingestion_id = ? AND raw_path = ?',
                (str(manifest.ingestion_id), raw_path)).fetchone()
    except sqlite3.Error:
        raise DailyEvidenceLoadError('DATABASE_UNAVAILABLE') from None
    except (ValueError, TypeError) as exc:
        _fail('INGESTION_REJECTED', raw_path, f'manifest {type(exc).__name__}')
    if row is None:
        _fail('INGESTION_UNAVAILABLE', raw_path, 'ingestion row disappeared')
    return manifest, row


def _result(manifest, row, raw_path):
    """Rebuild the ingestor's DailyBarIngestionResult or refuse."""
    if (manifest.asset_type != DataAssetType.DAILY_BARS
            or manifest.granularity != BarGranularity.D1):
        _fail('INGESTION_REJECTED', raw_path, 'DAILY_BARS D1 ingestion required')
    if row['status'] not in ACCEPTED_INGESTION_STATUSES:
        _fail('INGESTION_REJECTED', raw_path, 'ingestion not RECEIVED or VALIDATED')
    if not row['source_uri']:
        _fail('INGESTION_REJECTED', raw_path, 'source provenance missing')
    if manifest.market_date is None or manifest.record_count is None:
        _fail('INGESTION_REJECTED', raw_path, 'snapshot date or record count missing')
    try:
        metadata = json.loads(row['metadata_json'])
        if type(metadata) is not dict:
            raise ValueError('metadata must be an object')
        instrument_id = metadata.get('instrument_id')
        if type(instrument_id) is not str or str(UUID(instrument_id)) != instrument_id:
            raise ValueError('canonical instrument UUID required')
        result = DailyBarIngestionResult(
            provider=manifest.provider,
            canonical_symbol=metadata['canonical_symbol'],
            provider_symbol=metadata['provider_symbol'],
            instrument_id=instrument_id,
            snapshot_date=_iso_date(metadata, 'snapshot_date'),
            requested_start_date=_iso_date(metadata, 'requested_start_date'),
            requested_end_date=_iso_date(metadata, 'requested_end_date'),
            manifest=manifest, record_count=manifest.record_count,
            response_metadata=metadata.get('response') or {})
    except (ValueError, TypeError, KeyError) as exc:
        _fail('INGESTION_REJECTED', raw_path, f'metadata {type(exc).__name__}')
    if (type(result.canonical_symbol) is not str or type(result.provider_symbol) is not str
            or result.canonical_symbol != manifest.symbol
            or result.snapshot_date != manifest.market_date
            or not result.requested_start_date <= result.requested_end_date
            <= result.snapshot_date):
        _fail('INGESTION_REJECTED', raw_path, 'metadata inconsistent with manifest')
    return result


def _rows(raw_root, ingestion, raw_path):
    store = ImmutableRawStore(raw_root)
    # Verified read first so unreadable/unsafe/tampered bytes are classified
    # apart from canonicalization refusals; the pipeline re-verifies the bytes.
    try:
        store.read_verified(ingestion.manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail('ARTIFACT_UNREADABLE', raw_path, type(exc).__name__)
    try:
        rows = DailyCanonicalPipeline(raw_store=store).canonicalize_ingestion(ingestion).rows
    except (OSError, RuntimeError) as exc:
        _fail('ARTIFACT_UNREADABLE', raw_path, type(exc).__name__)
    except (ValueError, TypeError, ArithmeticError) as exc:
        _fail('CANONICALIZATION_REJECTED', raw_path, type(exc).__name__)
    if any(not ingestion.requested_start_date <= r.market_date <= ingestion.requested_end_date
           for r in rows):
        _fail('CANONICALIZATION_REJECTED', raw_path, 'row outside requested dates')
    return rows


def load_egx_daily_evidence(database, raw_root, raw_paths) -> dict:
    """Return ``{'daily': {instrument_id: EgxDailyEvidence}, 'sources': [...]}``.

    ``database`` is an existing ``app.storage.Database`` (read only);
    ``raw_root`` is the raw store root; ``raw_paths`` is a tuple of exact
    ``data_ingestions.raw_path`` values. ``daily`` is the producer's input
    mapping; ``sources`` lists one provenance entry per raw path in the given
    order. Refusals raise ``DailyEvidenceLoadError``.
    """
    if type(raw_root) is not str or not os.path.isabs(raw_root) or '\x00' in raw_root:
        raise DailyEvidenceLoadError('CONFIG_INVALID', 'absolute raw root required')
    raw_paths = _raw_paths(raw_paths)
    daily, sources = {}, []
    for raw_path in raw_paths:
        manifest, row = _ingestion(database, raw_path)
        ingestion = _result(manifest, row, raw_path)
        if ingestion.instrument_id in daily:
            _fail('DUPLICATE_INSTRUMENT', raw_path, 'instrument already named')
        received = manifest.received_at
        if type(received) is not datetime or received.utcoffset() is None:
            _fail('INGESTION_REJECTED', raw_path, 'timezone-aware received_at required')
        rows = _rows(raw_root, ingestion, raw_path)
        daily[ingestion.instrument_id] = EgxDailyEvidence(rows=tuple(rows), received_at=received)
        sources.append({
            'raw_path': raw_path,
            'ingestion_id': str(manifest.ingestion_id),
            'ingestion_status': row['status'],
            'instrument_id': ingestion.instrument_id,
            'canonical_symbol': ingestion.canonical_symbol,
            'provider': manifest.provider,
            'snapshot_date': ingestion.snapshot_date.isoformat(),
            'raw_sha256': manifest.sha256,
            'rows': len(rows),
            'received_at': received.isoformat(),
        })
    return {
        'schema_version': CONTRACT_VERSION,
        'daily': daily,
        'sources': sources,
        'received_at_clock': 'INGESTION_MANIFEST_UNVERIFIED',
        'market_membership': 'NOT_ESTABLISHED',
        'corporate_actions': 'NOT_CHECKED',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED',
    }
