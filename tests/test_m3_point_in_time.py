"""Synthetic contracts only. No provider transport is used."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.data.models import BarGranularity, DataAssetType
from app.data.point_in_time import PointInTimeDailyRepository
from app.data.raw_store import ImmutableRawStore
from app.storage.database import Database, SCHEMA_VERSION
from app.storage.reference_repository import ReferenceRepository

ID = UUID('00000000-0000-0000-0000-000000000001')
OTHER = UUID('00000000-0000-0000-0000-000000000002')
DAY = date(2020, 1, 2)
PREVIOUS = date(2020, 1, 1)
PUBLISHED = '2020-01-02T15:00:00+00:00'


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / 'isolated.db')
    db.initialize()
    return ReferenceRepository(db, ImmutableRawStore(tmp_path / 'raw'))


def cutoff():
    return datetime.now(timezone.utc) + timedelta(seconds=1)


def ingest(repo, doc, provider='synthetic'):
    return repo.ingest(provider=provider, payload=json.dumps(doc).encode(),
                       source_uri='fixture://synthetic', contract=doc['contract'])


def review(repo, manifest, contract):
    return ingest(repo, dict(
        contract='egx-source-review-v1', subject_ingestion_id=str(manifest.ingestion_id),
        subject_sha256=manifest.sha256,
        subject_provider=manifest.provider, subject_contract=contract,
        reviewed_at=datetime.now(timezone.utc).isoformat(), reviewer='fixture-reviewer',
        evidence_uri='fixture://validation', methodology='synthetic fixture comparison',
        approved=True,
    ))


def universe(members=None, day=DAY):
    return dict(contract='egx-universe-v1', market='EGX', effective_date=str(day),
                published_at=PUBLISHED, complete=True,
                members=members if members is not None else [dict(instrument_id=str(ID), symbol='SYNTH', eligible=True)])


def actions(events=None):
    return dict(contract='egx-actions-v1', instrument_id=str(ID), coverage_start=str(PREVIOUS),
                coverage_end=str(DAY), published_at=PUBLISHED, complete=True,
                actions=events or [])


def split():
    return dict(event_id='synthetic-split', effective_date=str(DAY), action_type='SPLIT',
                new_shares='2', old_shares='1', details='Synthetic two new shares per old share')


def bars():
    return [dict(date=str(d), open=10, high=12, low=8, close=10, volume=100,
                 adjusted_close=999) for d in (PREVIOUS, DAY)]


def daily(repo, records=None, provider='synthetic'):
    records = bars() if records is None else records
    import hashlib
    payload = json.dumps(records).encode()
    manifest = repo.raw_store.store_bytes(
        provider=provider, asset_type=DataAssetType.DAILY_BARS,
        payload=payload, filename=hashlib.sha256(payload).hexdigest()+'.json',
        market_date=DAY, symbol='SYNTH', granularity=BarGranularity.D1, record_count=len(records),
    )
    return repo.ingestions.save_manifest(manifest, source_uri='fixture://daily', metadata=dict(
        canonical_symbol='SYNTH', provider_symbol='SYNTH.TEST', instrument_id=str(ID),
        snapshot_date=str(DAY), requested_start_date=str(PREVIOUS),
        requested_end_date=str(DAY), response={},
    ))


def ready(repo, *, events=None, records=None):
    for doc in (universe(), universe(day=PREVIOUS), actions(events)):
        m = ingest(repo, doc)
        review(repo, m, doc['contract'])
    m = daily(repo, records)
    review(repo, m, 'egx-daily-semantic-v1')
    return m


def load(repo, m, **kwargs):
    return PointInTimeDailyRepository(repo).load(
        raw_path=m.raw_path, universe_date=kwargs.get('universe_date', DAY),
        expected_market_date=kwargs.get('expected_market_date', DAY),
        as_of=kwargs.get('as_of', cutoff()),
    )


def test_split_raw_preservation_provenance_and_restart_replay(repo):
    m = ready(repo, events=[split()])
    original = repo.raw_store.read_verified(m)
    at = cutoff()
    first = load(repo, m, as_of=at)
    restarted = ReferenceRepository(Database(repo.database.path), ImmutableRawStore(repo.raw_store.root))
    second = load(restarted, m, as_of=at)
    assert first == second
    assert first.dq_status == 'VALIDATED'
    assert len(first.provenance_ids) == 8
    assert first.rows[0].close == Decimal(10)
    assert first.rows[0].provider_adjusted_close_reference == Decimal(999)
    assert first.split_adjusted[0].close == Decimal(5)
    assert first.split_adjusted[0].volume == Decimal(200)
    assert first.split_adjusted[1].close == Decimal(10)
    assert first.split_adjusted[0].event_ids == ('synthetic-split',)
    assert repo.raw_store.read_verified(m) == original
    with repo.database.connect() as con:
        audit = con.execute('SELECT * FROM audit_events').fetchall()
    assert len(audit) == 1
    assert 'split-only-v1-decimal34' in audit[0]['payload_json']


def test_reference_replay_is_immutable_and_durable(repo):
    doc = universe()
    a = ingest(repo, doc)
    b = ingest(repo, doc)
    assert a == b
    assert repo.ingestions.count_ingestions() == 1
    with repo.database.connect() as con:
        for sql in ('DELETE FROM reference_artifacts', "UPDATE reference_artifacts SET contract='egx-actions-v1'"):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                con.execute(sql)
    payload = repo.raw_store.read_verified(a)
    with pytest.raises(FileExistsError):
        repo.raw_store.store_bytes(provider=a.provider, asset_type=a.asset_type,
                                  filename=a.raw_path.split('/')[-1], payload=payload+b' ')
    assert repo.raw_store.read_verified(a) == payload


@pytest.mark.parametrize('change', [
    {'complete': False}, {'complete': 1}, {'published_at': '2099-01-01T00:00:00Z'},
    {'published_at': '2020-01-01T00:00:00'}, {'market': 'OTHER'},
    {'members': [dict(instrument_id=str(ID), symbol='SYNTH', eligible=True)]*2},
    {'members': [dict(instrument_id=str(ID), symbol='SYNTH')]},
])
def test_bad_reference_rejected_with_idempotent_dq(repo, change):
    doc = universe() | change
    for _ in range(2):
        with pytest.raises(ValueError, match='invalid reference'):
            ingest(repo, doc)
    assert repo.ingestions.count_issues() == 1
    with repo.database.connect() as con:
        assert con.execute('SELECT status FROM data_ingestions').fetchone()[0] == 'REJECTED'
        assert con.execute('SELECT COUNT(*) FROM reference_artifacts').fetchone()[0] == 0


@pytest.mark.parametrize('payload', [b'{', b'[]', b'{}', b'{"contract":1,"contract":2}'])
def test_malformed_reference_raw_retained(repo, payload):
    with pytest.raises(ValueError):
        repo.ingest(provider='synthetic', payload=payload, source_uri='fixture://bad', contract='egx-universe-v1')
    assert repo.ingestions.count_ingestions() == 1
    assert repo.ingestions.count_issues() == 1


def test_sources_never_auto_promoted_even_named_official(repo):
    m = ingest(repo, universe(), provider='egid')
    with pytest.raises(ValueError, match='validation evidence unavailable'):
        repo.universe(market_date=DAY, as_of=cutoff())
    review(repo, m, 'egx-universe-v1')
    assert repo.universe(market_date=DAY, as_of=cutoff())[1].members[0].instrument_id == ID


def test_current_universe_does_not_supply_history(repo):
    m = ingest(repo, universe(day=date(2020, 1, 3)))
    review(repo, m, 'egx-universe-v1')
    with pytest.raises(ValueError, match='dated universe'):
        repo.universe(market_date=DAY, as_of=cutoff())


def test_historical_member_survives_later_removal(repo):
    m = ready(repo)
    newer = ingest(repo, universe([], day=date(2020, 1, 3)))
    review(repo, newer, 'egx-universe-v1')
    assert load(repo, m).rows[0].instrument_id == ID
    with pytest.raises(ValueError, match='not eligible'):
        load(repo, m, universe_date=date(2020, 1, 3))
    with repo.database.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM canonical_instruments').fetchone()[0] == 0


def test_conflicting_universe_fails_without_latest_wins(repo):
    m = ready(repo)
    other = ingest(repo, universe([]))
    review(repo, other, 'egx-universe-v1')
    with pytest.raises(ValueError, match='dated universe'):
        load(repo, m)


def test_future_receipt_cannot_repair_history(repo):
    m = ready(repo)
    with pytest.raises(ValueError, match='not known'):
        load(repo, m, as_of=datetime(2020, 1, 3, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='dated universe'):
        repo.universe(market_date=DAY, as_of=datetime(2020, 1, 3, tzinfo=timezone.utc))


def test_late_review_is_not_available_at_earlier_cutoff(repo):
    m = daily(repo)
    at = datetime.now(timezone.utc)
    review(repo, m, 'egx-daily-semantic-v1')
    with pytest.raises(ValueError, match='validation evidence unavailable'):
        repo.require_review(m, 'egx-daily-semantic-v1', as_of=at)


@pytest.mark.parametrize('records, message', [
    (bars()[:1], 'stale'), (bars()+bars()[:1], 'duplicate'),
    ([bars()[0], bars()[1] | {'close': 100}], 'quarantined'),
    ([bars()[0], bars()[1] | {'open': None}], 'invalid daily'),
    ([bars()[0], bars()[1] | {'volume': 'NaN'}], 'non-finite'),
    ([bars()[0], bars()[1] | {'date': '2020-01-03'}], 'after source snapshot'),
    ([], 'empty'),
])
def test_daily_rejections_are_durable(repo, records, message):
    m = ready(repo, records=records)
    at = cutoff()
    for _ in range(2):
        with pytest.raises(ValueError, match=message):
            load(repo, m, as_of=at)
    assert repo.ingestions.count_issues() == 1


@pytest.mark.parametrize('kind', ['DIVIDEND','RIGHTS','CAPITAL_INCREASE','CAPITAL_REDUCTION','SYMBOL_CHANGE','DELISTING','OTHER'])
def test_unimplemented_actions_block_instead_of_guessing(repo, kind):
    event = dict(event_id='synthetic-event', effective_date=str(DAY), action_type=kind, details='synthetic')
    m = ready(repo, events=[event])
    with pytest.raises(ValueError, match='unsupported corporate action'):
        load(repo, m)


def test_missing_actions_and_partial_coverage_fail_closed(repo):
    for doc in (universe(), universe(day=PREVIOUS)):
        u = ingest(repo, doc)
        review(repo, u, 'egx-universe-v1')
    m = daily(repo)
    review(repo, m, 'egx-daily-semantic-v1')
    with pytest.raises(ValueError, match='action coverage'):
        load(repo, m)
    partial = ingest(repo, actions() | {'coverage_start': str(DAY)})
    review(repo, partial, 'egx-actions-v1')
    with pytest.raises(ValueError, match='action coverage'):
        load(repo, m)


def test_conflicting_action_sources_fail_closed(repo):
    m = ready(repo)
    conflict = ingest(repo, actions([split()]))
    review(repo, conflict, 'egx-actions-v1')
    with pytest.raises(ValueError, match='action coverage'):
        load(repo, m)


@pytest.mark.parametrize('events', [[split() | {'new_shares': None}], [split(), split()],
                                   [split() | {'old_shares': 'NaN'}]])
def test_ambiguous_split_terms_are_rejected(repo, events):
    with pytest.raises(ValueError, match='invalid reference'):
        ingest(repo, actions(events))


def test_tampered_raw_fails_closed_and_records_issue(repo):
    m = ready(repo)
    path = repo.raw_store.root / m.raw_path
    path.write_bytes(path.read_bytes().replace(b'999', b'998'))
    with pytest.raises(ValueError, match='sha256 mismatch'):
        load(repo, m)
    assert repo.ingestions.count_issues() == 1


def test_review_identity_cannot_cross_providers(repo):
    a = daily(repo, provider='synthetic-a')
    b = daily(repo, provider='synthetic-b')
    review(repo, a, 'egx-daily-semantic-v1')
    assert a.sha256 == b.sha256
    with pytest.raises(ValueError, match='validation evidence unavailable'):
        repo.require_review(b, 'egx-daily-semantic-v1', as_of=cutoff())


def test_schema_8_upgrade_is_explicit_additive_and_monotonic(tmp_path):
    db = Database(tmp_path / 'old.db')
    with db.connect() as con:
        con.executescript("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                          "INSERT INTO schema_meta VALUES('schema_version','8');"
                          "CREATE TABLE sentinel(value TEXT); INSERT INTO sentinel VALUES('preserved');")
    with pytest.raises(RuntimeError, match='upgrade required'):
        db.initialize()
    assert db.schema_version() == 8
    with db.connect() as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='reference_artifacts'").fetchone()
    db.initialize(allow_upgrade=True)
    db.initialize()
    assert db.schema_version() == SCHEMA_VERSION == 9
    with db.connect() as con:
        assert con.execute('SELECT value FROM sentinel').fetchone()[0] == 'preserved'
        assert con.execute('PRAGMA foreign_key_check').fetchall() == []


def test_daily_conflicts_are_preserved_and_block_both_versions(repo):
    first = ready(repo)
    conflicting = daily(repo, [bars()[0], bars()[1] | {'volume': 101}])
    review(repo, conflicting, 'egx-daily-semantic-v1')
    for m in (first, conflicting):
        with pytest.raises(ValueError, match='conflicting daily source'):
            load(repo, m)
        assert repo.raw_store.read_verified(m)


def test_no_current_membership_shortcut_for_older_bars(repo):
    for doc in (universe(), actions()):
        m = ingest(repo, doc)
        review(repo, m, doc['contract'])
    m = daily(repo)
    review(repo, m, 'egx-daily-semantic-v1')
    with pytest.raises(ValueError, match='dated universe'):
        load(repo, m)
    historical = ingest(repo, universe([], day=PREVIOUS))
    review(repo, historical, 'egx-universe-v1')
    with pytest.raises(ValueError, match='historical membership'):
        load(repo, m)


def test_reference_tampering_records_durable_issue(repo):
    m = ingest(repo, universe())
    review(repo, m, 'egx-universe-v1')
    path = repo.raw_store.root / m.raw_path
    path.write_bytes(path.read_bytes().replace(b'SYNTH', b'WRONG'))
    for _ in range(2):
        with pytest.raises(ValueError, match='reference integrity'):
            repo.universe(market_date=DAY, as_of=cutoff())
    assert repo.ingestions.count_issues() == 1


def test_naive_and_future_queries_fail_closed(repo):
    m = ready(repo)
    with pytest.raises(ValueError, match='timezone-aware'):
        load(repo, m, as_of=datetime(2020, 1, 3))
    with pytest.raises(ValueError, match='future universe'):
        repo.universe(market_date=date(2099, 1, 1), as_of=cutoff())


def test_source_review_binds_ingestion_not_only_equal_bytes(repo):
    m = daily(repo)
    review(repo, m, 'egx-daily-semantic-v1')
    from app.data.models import RawArtifactManifest
    forged = RawArtifactManifest(**(m.model_dump() | {'ingestion_id': OTHER}))
    with pytest.raises(ValueError, match='validation evidence unavailable'):
        repo.require_review(forged, 'egx-daily-semantic-v1', as_of=cutoff())


def test_partial_nonobject_daily_rows_are_rejected(repo):
    m = ready(repo, records=[None])
    with pytest.raises(ValueError, match='array of objects'):
        load(repo, m)


def test_ingestion_restart_after_raw_catalog_receipt(repo):
    # Simulate a crash after raw receipt but before reference catalog insertion.
    doc = universe()
    payload = json.dumps(doc).encode()
    import hashlib
    m = repo.raw_store.store_bytes(
        provider='synthetic', asset_type=DataAssetType.SECURITY_MASTER, payload=payload,
        filename='egx-universe-v1-'+hashlib.sha256(payload).hexdigest()+'.json',
    )
    original = repo.ingestions.save_manifest(m, source_uri='fixture://synthetic',
                                            metadata={'contract': doc['contract']})
    restarted = ReferenceRepository(Database(repo.database.path), ImmutableRawStore(repo.raw_store.root))
    replay = ingest(restarted, doc)
    assert replay == original
    review(restarted, replay, doc['contract'])
    assert restarted.universe(market_date=DAY, as_of=cutoff())[1].effective_date == DAY


def test_decimal_adjustment_is_independent_of_ambient_context(repo):
    from decimal import localcontext, ROUND_UP
    m = ready(repo, events=[split() | {'new_shares': '3'}])
    at = cutoff()
    expected = load(repo, m, as_of=at)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        assert load(repo, m, as_of=at) == expected


def test_future_effective_split_does_not_adjust_history(repo):
    for doc in (universe(), universe(day=PREVIOUS), actions([
        split() | {'effective_date': '2020-01-03'}
    ]) | {'coverage_end': '2020-01-03'}):
        m = ingest(repo, doc)
        review(repo, m, doc['contract'])
    m = daily(repo)
    review(repo, m, 'egx-daily-semantic-v1')
    dataset = load(repo, m)
    assert all(row.price_factor == 1 and row.event_ids == () for row in dataset.split_adjusted)


def test_numerically_unrepresentable_split_fails_with_dq(repo):
    m = ready(repo, events=[split() | {'new_shares': '1e9999999'}])
    with pytest.raises(ValueError):
        load(repo, m)
    assert repo.ingestions.count_issues() == 1


def test_unknown_ingestion_status_fails_closed(repo):
    m = ready(repo)
    with repo.database.connect() as con:
        con.execute("UPDATE data_ingestions SET status='UNKNOWN' WHERE ingestion_id=?",
                    (str(m.ingestion_id),))
    with pytest.raises(ValueError, match='source rejected'):
        load(repo, m)
