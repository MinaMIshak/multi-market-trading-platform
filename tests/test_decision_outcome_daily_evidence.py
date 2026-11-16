"""decision-outcome-daily-evidence-v1: named retained daily ingestions -> producer daily input."""
from datetime import date
import json

import pytest

from app.data.daily_canonical import CanonicalDailyBar, DailyBarSemanticClass
from app.data.ingestion_repository import DataIngestionRepository
from app.data.models import BarGranularity, DataAssetType, IngestionStatus
from app.data.raw_store import ImmutableRawStore
from app.decision_outcome_daily_evidence import (
    CONTRACT_VERSION, LOAD_REASONS, DailyEvidenceLoadError, load_egx_daily_evidence)
from app.decision_outcome_producer import EgxDailyEvidence
from app.storage import Database

INSTRUMENT = '4c1f3369-f71c-5856-aa52-953040a2cbc0'
OTHER = '0d5b0f0e-6a4e-5f43-9b39-3c1c2f6a7b11'
PAYLOAD = (b'[{"date":"2026-09-07","open":140,"high":141,"low":138,"close":139,'
           b'"adjusted_close":139,"volume":100},'
           b'{"date":"2026-09-08","open":150,"high":141,"low":138,"close":140,'
           b'"adjusted_close":140,"volume":200}]')


def _metadata(instrument=INSTRUMENT, symbol='COMI', **overrides):
    metadata = {'canonical_symbol': symbol, 'provider_symbol': f'{symbol}.EGX',
                'instrument_id': instrument, 'snapshot_date': '2026-09-09',
                'requested_start_date': '2026-09-07', 'requested_end_date': '2026-09-08',
                'response': {}}
    metadata.update(overrides)
    return {k: v for k, v in metadata.items() if v is not None}


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / 'platform.db')
    database.initialize()
    raw = ImmutableRawStore(tmp_path / 'raw')

    def ingest(*, filename='COMI.EGX-D1.json', payload=PAYLOAD, symbol='COMI',
               status=IngestionStatus.RECEIVED, source_uri='https://example.invalid/d1',
               metadata=None):
        manifest = raw.store_bytes(
            provider='eodhd', asset_type=DataAssetType.DAILY_BARS, payload=payload,
            filename=filename, market_date=date(2026, 9, 9), symbol=symbol,
            granularity=BarGranularity.D1, record_count=len(json.loads(payload)))
        return DataIngestionRepository(database).save_manifest(
            manifest, status=status, source_uri=source_uri,
            metadata=_metadata(symbol=symbol) if metadata is None else metadata)

    return database, str(tmp_path / 'raw'), raw, ingest


def _reason(database, root, paths):
    with pytest.raises(DailyEvidenceLoadError) as caught:
        load_egx_daily_evidence(database, root, paths)
    assert caught.value.reason in LOAD_REASONS
    return caught.value.reason


def test_loads_exact_rows_and_manifest_receipt(env):
    database, root, _, ingest = env
    manifest = ingest()
    out = load_egx_daily_evidence(database, root, (manifest.raw_path,))
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-daily-evidence-v1'
    assert list(out['daily']) == [INSTRUMENT]
    evidence = out['daily'][INSTRUMENT]
    assert type(evidence) is EgxDailyEvidence
    assert evidence.received_at == manifest.received_at
    assert evidence.received_at.utcoffset() is not None
    assert type(evidence.rows) is tuple and all(type(r) is CanonicalDailyBar for r in evidence.rows)
    assert [r.market_date for r in evidence.rows] == [date(2026, 9, 7), date(2026, 9, 8)]
    assert {r.source_sha256 for r in evidence.rows} == {manifest.sha256}
    assert {str(r.instrument_id) for r in evidence.rows} == {INSTRUMENT}
    # Quarantined rows pass through; the session-bar binding rejects them on a path.
    assert [r.semantic_class for r in evidence.rows] == [
        DailyBarSemanticClass.VALID_EXECUTABLE, DailyBarSemanticClass.QUARANTINED_ANOMALY]
    assert out['sources'] == [{
        'raw_path': manifest.raw_path, 'ingestion_id': str(manifest.ingestion_id),
        'ingestion_status': 'RECEIVED', 'instrument_id': INSTRUMENT,
        'canonical_symbol': 'COMI', 'provider': 'eodhd', 'snapshot_date': '2026-09-09',
        'raw_sha256': manifest.sha256, 'rows': 2,
        'received_at': manifest.received_at.isoformat()}]
    assert out['received_at_clock'] == 'INGESTION_MANIFEST_UNVERIFIED'
    assert out['market_membership'] == 'NOT_ESTABLISHED'
    assert out['corporate_actions'] == 'NOT_CHECKED'
    assert out['performance_claim'] == 'NONE' and out['mode'] == 'SHADOW'
    assert out['live_money'] is out['execution_allowed'] is False
    assert out['promotion'] == 'NOT_AUTHORIZED'


def test_validated_ingestion_accepted_and_database_not_written(env):
    database, root, _, ingest = env
    manifest = ingest(status=IngestionStatus.VALIDATED)
    with database.connect() as con:
        before = [tuple(r) for r in con.execute('SELECT * FROM data_ingestions')]
    out = load_egx_daily_evidence(database, root, (manifest.raw_path,))
    assert out['sources'][0]['ingestion_status'] == 'VALIDATED'
    with database.connect() as con:
        assert [tuple(r) for r in con.execute('SELECT * FROM data_ingestions')] == before
        assert con.execute('SELECT COUNT(*) FROM daily_canonical_artifacts').fetchone()[0] == 0


def test_two_instruments_keep_order_and_separation(env):
    database, root, _, ingest = env
    first = ingest()
    second = ingest(filename='HRHO.EGX-D1.json', symbol='HRHO',
                    metadata=_metadata(instrument=OTHER, symbol='HRHO'))
    out = load_egx_daily_evidence(database, root, (second.raw_path, first.raw_path))
    assert [s['instrument_id'] for s in out['sources']] == [OTHER, INSTRUMENT]
    assert {r.canonical_symbol for r in out['daily'][OTHER].rows} == {'HRHO'}
    assert {r.canonical_symbol for r in out['daily'][INSTRUMENT].rows} == {'COMI'}


@pytest.mark.parametrize('paths', [(), ['x'], ('',), ('a\x00b',), ('same', 'same'), None])
def test_config_invalid_paths(env, paths):
    database, root, _, _ = env
    assert _reason(database, root, paths) == 'CONFIG_INVALID'


def test_relative_raw_root_refused(env):
    database, _, _, ingest = env
    manifest = ingest()
    assert _reason(database, 'raw', (manifest.raw_path,)) == 'CONFIG_INVALID'


def test_unknown_raw_path_unavailable(env):
    database, root, _, _ = env
    assert _reason(database, root, ('eodhd/never/ingested.json',)) == 'INGESTION_UNAVAILABLE'


def test_second_ingestion_for_same_instrument_refused_not_preferred(env):
    database, root, _, ingest = env
    first = ingest()
    later = ingest(filename='COMI.EGX-D1-b.json', payload=PAYLOAD.replace(b'"volume":200',
                                                                         b'"volume":201'))
    assert _reason(database, root, (first.raw_path, later.raw_path)) == 'DUPLICATE_INSTRUMENT'


def test_tampered_bytes_unreadable(env):
    database, root, raw, ingest = env
    manifest = ingest()
    target = raw.root / manifest.raw_path
    target.write_bytes(b'X' + target.read_bytes()[1:])
    assert _reason(database, root, (manifest.raw_path,)) == 'ARTIFACT_UNREADABLE'


def test_missing_bytes_unreadable(env):
    database, root, raw, ingest = env
    manifest = ingest()
    (raw.root / manifest.raw_path).unlink()
    assert _reason(database, root, (manifest.raw_path,)) == 'ARTIFACT_UNREADABLE'


def test_rejected_ingestion_refused(env):
    database, root, _, ingest = env
    manifest = ingest(status=IngestionStatus.REJECTED)
    assert _reason(database, root, (manifest.raw_path,)) == 'INGESTION_REJECTED'


def test_missing_source_provenance_refused(env):
    database, root, _, ingest = env
    manifest = ingest(source_uri=None)
    assert _reason(database, root, (manifest.raw_path,)) == 'INGESTION_REJECTED'


@pytest.mark.parametrize('overrides', [
    {'requested_start_date': None},
    {'requested_end_date': None},
    {'snapshot_date': '2026-09-10'},
    {'canonical_symbol': 'HRHO'},
    {'instrument_id': INSTRUMENT.upper()},
    {'instrument_id': 'not-a-uuid'},
    {'requested_start_date': '2026-09-08', 'requested_end_date': '2026-09-07'},
    {'requested_end_date': '2026-09-10'},
])
def test_inconsistent_metadata_refused(env, overrides):
    database, root, _, ingest = env
    manifest = ingest(metadata=_metadata(**overrides))
    assert _reason(database, root, (manifest.raw_path,)) == 'INGESTION_REJECTED'


def test_row_outside_requested_dates_refused(env):
    database, root, _, ingest = env
    manifest = ingest(metadata=_metadata(requested_start_date='2026-09-08'))
    assert _reason(database, root, (manifest.raw_path,)) == 'CANONICALIZATION_REJECTED'


def test_malformed_daily_bytes_refused(env):
    database, root, _, ingest = env
    manifest = ingest(filename='COMI.EGX-D1-c.json', payload=b'[{"date":"2026-09-07"}]')
    assert _reason(database, root, (manifest.raw_path,)) == 'CANONICALIZATION_REJECTED'
