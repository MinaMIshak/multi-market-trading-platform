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
    assert database.schema_version() == 12
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
