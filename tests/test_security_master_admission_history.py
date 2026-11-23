"""Temporary-database admission history, not market or runtime evidence."""
from datetime import date
import sqlite3

import pytest

from app.data.security_master_refresh_job import SecurityMasterRefreshJobError
from app.storage.security_master_repository import SecurityMasterRepository
from tests.test_security_master_refresh_runtime import (
    DAY, FakeEGIDProvider, build, egid_record, egid_records,
)


def refresh(runtime, day=DAY, records=None):
    return runtime.job.run(provider=FakeEGIDProvider(
        [egid_record()] if records is None else records), snapshot_date=day)


def receipt(database, result):
    return SecurityMasterRepository(database).get_admission(
        provider=result.provider, snapshot_date=result.snapshot_date,
        artifact_sha256=result.artifact_sha256)


def test_history_survives_replacement_restart_and_exact_replay(tmp_path):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    original = receipt(database, first)
    refresh(runtime)
    assert receipt(database, first) == original
    second = refresh(runtime, date(2026, 9, 30),
                     [egid_record(symbol_code='EGS2', reuters='OTHER.CA')])
    database.initialize()
    assert receipt(database, first) == original
    assert original['instruments'][0]['canonical_ticker'] == 'CCAP'
    assert receipt(database, second)['instruments'][0]['canonical_ticker'] == 'OTHER'
    assert original['live_money'] is False
    assert original['previous_instrument_count'] == 0
    assert original['artifact_sha256'] == first.artifact_sha256


@pytest.mark.parametrize('field,value', [('provider', 'us'), ('artifact_sha256', '0'*64),
                                         ('snapshot_date', date(2026, 9, 28))])
def test_lookup_requires_exact_evidence_without_current_fallback(tmp_path, field, value):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    args = dict(provider='egid', snapshot_date=DAY, artifact_sha256=first.artifact_sha256)
    args[field] = value
    with pytest.raises(KeyError):
        SecurityMasterRepository(database).get_admission(**args)


@pytest.mark.parametrize('statement', [
    "UPDATE security_master_admissions SET receipt_json = '{}'",
    'DELETE FROM security_master_admissions',
    'INSERT OR REPLACE INTO security_master_admissions SELECT * FROM security_master_admissions',
])
def test_history_cannot_be_changed_or_deleted(tmp_path, statement):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    before = receipt(database, first)
    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(statement)
    assert receipt(database, first) == before


def test_receipt_failure_rolls_back_snapshot_replacement(tmp_path):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    with database.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_receipt BEFORE INSERT ON
            security_master_admissions BEGIN SELECT RAISE(ABORT, 'disk fixture'); END""")
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime, date(2026, 9, 30),
                [egid_record(symbol_code='EGS2', reuters='OTHER.CA')])
    assert SecurityMasterRepository(database).list_equity_tickers() == ('CCAP',)
    assert receipt(database, first)['snapshot_date'] == DAY.isoformat()


def test_snapshot_failure_rolls_back_receipt(tmp_path):
    database, runtime = build(tmp_path)
    with database.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_snapshot BEFORE INSERT ON
            canonical_instruments BEGIN SELECT RAISE(ABORT, 'write fixture'); END""")
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime)
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM security_master_admissions').fetchone()[0] == 0


@pytest.mark.parametrize('records', [[], egid_records(8), [egid_record(symbol_code='EGX30', reuters=None)]])
def test_failed_completeness_never_creates_admission(tmp_path, records):
    database, runtime = build(tmp_path)
    refresh(runtime, records=egid_records(10))
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime, date(2026, 9, 30), records)
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM security_master_admissions').fetchone()[0] == 1
    assert SecurityMasterRepository(database).count_instruments() == 10


def test_schema_upgrade_requires_permission_and_never_backfills_history(tmp_path):
    database, runtime = build(tmp_path)
    refresh(runtime)
    with database.connect() as connection:
        connection.execute('DROP TABLE security_master_admissions')
        connection.execute("UPDATE schema_meta SET value = '9' WHERE key = 'schema_version'")
    with pytest.raises(RuntimeError, match='upgrade required'):
        database.initialize(allow_upgrade=False)
    database.initialize(allow_upgrade=True)
    assert database.schema_version() == 13
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM security_master_admissions').fetchone()[0] == 0
    assert SecurityMasterRepository(database).list_equity_tickers() == ('CCAP',)


def direct_inputs(database, result):
    from app.data.models import RawArtifactManifest
    from app.data.security_master import CanonicalInstrument
    saved = receipt(database, result)
    manifest = RawArtifactManifest(
        provider='egid', asset_type='SECURITY_MASTER', market_date=result.snapshot_date,
        sha256=result.artifact_sha256, byte_size=saved['byte_size'], raw_path='unused',
        record_count=saved['record_count'], received_at=saved['received_at'])
    return manifest, [CanonicalInstrument.model_validate(i) for i in saved['instruments']]


@pytest.mark.parametrize('changes', [dict(provider='us'), dict(sha256='0'*64),
    dict(record_count=2), dict(market_date=date(2026, 9, 28)), dict(symbol='CCAP')])
def test_manifest_mismatch_rejects_before_replacement(tmp_path, changes):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    manifest, instruments = direct_inputs(database, first)
    manifest = manifest.model_copy(update=changes)
    repository = SecurityMasterRepository(database)
    with pytest.raises(ValueError, match='manifest'):
        repository.replace_provider_snapshot(provider='egid', instruments=instruments,
                                             admission_manifest=manifest)
    assert repository.list_equity_tickers() == ('CCAP',)


def test_same_day_conflicting_identity_cannot_rewrite_receipt_or_master(tmp_path):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    before = receipt(database, first)
    manifest, instruments = direct_inputs(database, first)
    instruments[0].canonical_ticker = 'CHANGED'
    with pytest.raises(ValueError, match='conflict'):
        SecurityMasterRepository(database).replace_provider_snapshot(
            provider='egid', instruments=instruments, admission_manifest=manifest)
    assert receipt(database, first) == before
    assert SecurityMasterRepository(database).list_equity_tickers() == ('CCAP',)


def test_old_replay_cannot_replace_newer_master(tmp_path):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    refresh(runtime, date(2026, 9, 30), [egid_record(symbol_code='EGS2', reuters='OTHER.CA')])
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime)
    assert receipt(database, first)['instruments'][0]['canonical_ticker'] == 'CCAP'
    # CCAP stays as a durable identity but is not current scan scope.
    assert SecurityMasterRepository(database).list_equity_tickers() == ('OTHER',)


def test_transaction_rechecks_retention_after_job_precheck(tmp_path, monkeypatch):
    database, runtime = build(tmp_path)
    refresh(runtime, records=egid_records(10))
    monkeypatch.setattr(runtime.job, '_require_complete_snapshot', lambda **kw: None)
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime, date(2026, 9, 30), egid_records(8))
    assert SecurityMasterRepository(database).count_instruments() == 10


def test_corrupt_receipt_is_not_used_or_repaired(tmp_path):
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    with database.connect() as connection:
        connection.execute('DROP TRIGGER security_master_admissions_no_update')
        connection.execute("UPDATE security_master_admissions SET receipt_json = '{}'")
    with pytest.raises(ValueError, match='integrity'):
        receipt(database, first)
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime)
    with database.connect() as connection:
        assert connection.execute('SELECT receipt_json FROM security_master_admissions').fetchone()[0] == '{}'


def test_future_receipt_rejected_even_on_existing_admission(tmp_path):
    from datetime import datetime, timezone
    database, runtime = build(tmp_path)
    first = refresh(runtime)
    manifest, instruments = direct_inputs(database, first)
    manifest.received_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match='future'):
        SecurityMasterRepository(database).replace_provider_snapshot(
            provider='egid', instruments=instruments, admission_manifest=manifest)


def test_legacy_repository_write_does_not_invent_admission(tmp_path):
    from app.data.security_master import build_canonical_security_master
    database, _ = build(tmp_path)
    repository = SecurityMasterRepository(database)
    instruments = build_canonical_security_master(records=[egid_record()],
        source_sha256='a'*64, source_market_date=DAY.isoformat())
    repository.replace_provider_snapshot(provider='egid', instruments=instruments)
    with pytest.raises(KeyError):
        repository.get_admission(provider='egid', snapshot_date=DAY, artifact_sha256='a'*64)


def test_refresh_preserves_historical_identity_referenced_by_daily_artifact(
    tmp_path,
):
    """A later security-master snapshot must never delete an identity that
    historical canonical market evidence references.

    canonical_instruments is durable identity storage; an admitted snapshot
    records current provider membership separately.
    """
    from datetime import datetime, timezone
    from uuid import UUID, uuid4

    from app.data.daily_canonical_store import (
        CanonicalDailyArtifactManifest,
    )
    from app.data.ingestion_repository import (
        DataIngestionRepository,
    )
    from app.data.models import (
        BarGranularity,
        DataAssetType,
        IngestionStatus,
        RawArtifactManifest,
    )
    from app.storage.daily_canonical_artifact_repository import (
        DailyCanonicalArtifactRepository,
    )

    database, runtime = build(tmp_path)

    first = refresh(
        runtime,
        records=[
            egid_record(
                symbol_code="EGS1",
                reuters="CCAP.CA",
            )
        ],
    )

    first_receipt = receipt(database, first)
    old_id = UUID(
        first_receipt["instruments"][0]["instrument_id"]
    )

    ingestion_id = uuid4()

    ingestion_manifest = RawArtifactManifest(
        ingestion_id=ingestion_id,
        provider="eodhd",
        asset_type=DataAssetType.DAILY_BARS,
        granularity=BarGranularity.D1,
        symbol="CCAP",
        market_date=DAY,
        raw_path="eodhd/daily/ccap.json",
        sha256="d" * 64,
        byte_size=100,
        record_count=1,
        received_at=datetime.now(timezone.utc),
    )

    DataIngestionRepository(database).save_manifest(
        ingestion_manifest,
        status=IngestionStatus.RECEIVED,
        metadata={
            "canonical_symbol": "CCAP",
            "provider_symbol": "CCAP.EGX",
            "instrument_id": str(old_id),
            "snapshot_date": DAY.isoformat(),
        },
    )

    DailyCanonicalArtifactRepository(
        database
    ).promote_validated_artifact(
        manifest=CanonicalDailyArtifactManifest(
            instrument_id=old_id,
            provider="eodhd",
            provider_symbol="CCAP.EGX",
            canonical_symbol="CCAP",
            asset_type=DataAssetType.DAILY_BARS,
            granularity=BarGranularity.D1,
            source_snapshot_date=DAY,
            oldest_market_date=DAY,
            newest_market_date=DAY,
            relative_path=(
                "eodhd/daily_bars/"
                "CCAP-canonical-v1.json"
            ),
            sha256="e" * 64,
            byte_size=200,
            record_count=1,
            valid_bar_count=1,
            quarantined_bar_count=0,
        ),
        source_ingestion_ids=[
            str(ingestion_id)
        ],
        metadata={"validation": "regression"},
    )

    second = refresh(
        runtime,
        date(2026, 9, 30),
        [
            egid_record(
                symbol_code="EGS2",
                reuters="OTHER.CA",
            )
        ],
    )

    second_receipt = receipt(database, second)

    assert (
        second_receipt["instrument_count"]
        == 1
    )

    with database.connect() as connection:
        old_row = connection.execute(
            """
            SELECT canonical_ticker
            FROM canonical_instruments
            WHERE instrument_id = ?
            """,
            (str(old_id),),
        ).fetchone()

        historical_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM daily_canonical_artifacts
            WHERE instrument_id = ?
            """,
            (str(old_id),),
        ).fetchone()[0]

        violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert old_row is not None
    assert old_row["canonical_ticker"] == "CCAP"
    assert historical_count == 1
    assert violations == []

    # Durable identity attribution is not current membership: the retained
    # historical identity must not widen current scan scope.
    assert (
        SecurityMasterRepository(database)
        .list_equity_tickers()
        == ("OTHER",)
    )


def test_retention_gate_uses_previous_admission_not_cumulative_registry(
    tmp_path,
):
    """Historical identities retained in canonical_instruments must not inflate
    the previous-snapshot denominator used by the completeness gate.
    """

    database, runtime = build(tmp_path)

    refresh(
        runtime,
        records=egid_records(10),
    )

    refresh(
        runtime,
        date(2026, 9, 30),
        [
            egid_record(
                symbol_code=f"NEW{index:05d}",
                reuters=f"N{index:04d}.CA",
            )
            for index in range(10)
        ],
    )

    # Registry now contains both historical and current identities.
    assert (
        SecurityMasterRepository(database)
        .count_provider_instruments("egid")
        == 20
    )

    # 9 is 90% of the immediately previous admitted snapshot (10),
    # and must therefore pass even though it is <90% of cumulative 20.
    result = refresh(
        runtime,
        date(2026, 10, 1),
        [
            egid_record(
                symbol_code=f"THIRD{index:05d}",
                reuters=f"T{index:04d}.CA",
            )
            for index in range(9)
        ],
    )

    assert receipt(
        database,
        result,
    )["previous_instrument_count"] == 10


def test_current_membership_is_replaced_not_accumulated(tmp_path):
    database, runtime = build(tmp_path)
    refresh(runtime, records=egid_records(10))
    refresh(
        runtime,
        date(2026, 9, 30),
        [
            egid_record(symbol_code=f"NEW{index:05d}", reuters=f"N{index:04d}.CA")
            for index in range(9)
        ],
    )
    repository = SecurityMasterRepository(database)
    assert repository.count_provider_instruments("egid") == 19
    assert repository.list_equity_tickers() == tuple(
        sorted(f"N{index:04d}" for index in range(9))
    )
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM security_master_current_members"
            " WHERE provider = 'egid'"
        ).fetchone()[0] == 9


def test_failed_refresh_leaves_current_membership_unchanged(tmp_path):
    database, runtime = build(tmp_path)
    refresh(runtime, records=egid_records(10))
    before = SecurityMasterRepository(database).list_equity_tickers()
    with pytest.raises(SecurityMasterRefreshJobError):
        refresh(runtime, date(2026, 9, 30), egid_records(8))
    assert SecurityMasterRepository(database).list_equity_tickers() == before
    assert len(before) == 10


def test_v12_upgrade_backfills_current_membership_exactly(tmp_path):
    """Pre-13 snapshot writes deleted dropped rows, so the stored identities
    are exactly current membership; the upgrade records that once."""
    database, runtime = build(tmp_path)
    refresh(runtime)
    with database.connect() as connection:
        connection.execute('DROP TABLE security_master_current_members')
        connection.execute(
            'DROP INDEX idx_reference_universe_effective_date_unique'
        )
        connection.execute(
            "UPDATE schema_meta SET value = '12' WHERE key = 'schema_version'"
        )
    with pytest.raises(RuntimeError, match='upgrade required'):
        database.initialize()
    database.initialize(allow_upgrade=True)
    assert database.schema_version() == 13
    assert SecurityMasterRepository(database).list_equity_tickers() == ('CCAP',)
    database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            'SELECT COUNT(*) FROM security_master_current_members'
        ).fetchone()[0] == 1
