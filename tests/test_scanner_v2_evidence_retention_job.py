"""Artificial disposable databases, raw stores and clocks only.

No scheduler, runtime producer, admission, market coverage or edge evidence is
implied by these tests.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import inspect
import json
from types import SimpleNamespace

import pytest

import app.scanner_v2_evidence_retention_job as job
import app.storage.repository as repository_module
from app.domain import MarketSession
from app.scanner_v2_evidence_retention_job import (
    JOB_SCHEMA, RETENTION_REASONS, EvidenceRetentionJobError, main,
    run_egx_evidence_retention_job)
from app.storage import Database, TradingRepository
from tests.test_scanner_v2_card_report_job import IDENTITY_BYTES, _store

UTC = timezone.utc
SESSION = date(2026, 11, 3)
THROUGH = date(2026, 11, 4)
AT = datetime(2026, 11, 4, 5, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 11, 4, 8, tzinfo=UTC)
SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}
ENVELOPE = {'schema_version': 'security-master-observation-v1',
            'observed_at': '2026-11-04T04:59:00+00:00', 'receipt_json': '{}',
            'receipt_sha256': 'e' * 64, 'live_money': False}


def _calendar_rows():
    return [
        dict(market_date='2026-11-03', status='VERIFIED', opened_at='2026-11-03T10:00:00+02:00',
             closed_at='2026-11-03T14:30:00+02:00', data_verified_at='2026-11-03T15:00:00+02:00'),
        dict(market_date='2026-11-04', status='UNKNOWN', opened_at='2026-11-04T10:00:00+02:00',
             closed_at='2026-11-04T14:30:00+02:00'),
    ]


class FakeMaster:
    """Stands in for SecurityMasterRepository at the retention boundary only."""

    calls = []
    result = ENVELOPE

    def __init__(self, database):
        self.database = database

    def retain_admission_observation(self, **kwargs):
        FakeMaster.calls.append(kwargs)
        if isinstance(FakeMaster.result, BaseException):
            raise FakeMaster.result
        return dict(FakeMaster.result)


@pytest.fixture
def clock(monkeypatch):
    now = [AT]
    monkeypatch.setattr(repository_module, '_utc_now', lambda: now[0].isoformat())
    return now


@pytest.fixture
def host(tmp_path, clock):
    db = Database(tmp_path / 'platform.db')
    db.initialize()
    trading = TradingRepository(db)
    for row in _calendar_rows():
        trading.save_market_session(MarketSession.model_validate(row))
    raw_root = tmp_path / 'raw'
    manifest = _store(db, raw_root)
    return SimpleNamespace(db=db, raw_root=raw_root, manifest=manifest, trading=trading)


@pytest.fixture
def master(monkeypatch):
    FakeMaster.calls = []
    FakeMaster.result = ENVELOPE
    monkeypatch.setattr(job, 'SecurityMasterRepository', FakeMaster)
    return FakeMaster


def kwargs(host, **overrides):
    values = dict(db_path=str(host.db.path), raw_root=str(host.raw_root),
                  session=SESSION.isoformat(), through=THROUGH.isoformat(),
                  identity_raw_path=host.manifest.raw_path)
    values.update(overrides)
    return values


def calendar_count(host):
    with host.db.connect() as connection:
        return connection.execute('SELECT COUNT(*) FROM calendar_observations').fetchone()[0]


def failed(host, stage, reason, **overrides):
    assert reason in RETENTION_REASONS
    with pytest.raises(EvidenceRetentionJobError) as info:
        run_egx_evidence_retention_job(**kwargs(host, **overrides))
    assert (info.value.stage, info.value.reason) == (stage, reason)
    return info.value


def test_retains_identity_and_calendar_with_existing_producers(host, master):
    out = run_egx_evidence_retention_job(**kwargs(host))
    assert out['schema_version'] == JOB_SCHEMA and out['status'] == 'RETAINED'
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert master.calls == [dict(provider='egid', snapshot_date=SESSION,
                                 artifact_sha256=sha256(IDENTITY_BYTES).hexdigest())]
    assert out['session'] == '2026-11-03'
    assert out['identity_raw_path'] == host.manifest.raw_path
    assert out['identity_artifact_sha256'] == host.manifest.sha256
    assert out['identity_observed_at'] == ENVELOPE['observed_at']
    assert out['identity_receipt_sha256'] == ENVELOPE['receipt_sha256']
    assert out['calendar_observed_at'] == AT.isoformat()
    assert out['calendar_next_session'] == '2026-11-04'
    assert out['calendar_admission'] == 'NOT_ESTABLISHED'
    assert calendar_count(host) == 1
    json.dumps(out)


def test_printed_calendar_digest_is_what_the_card_report_loader_reads(host, master):
    out = run_egx_evidence_retention_job(**kwargs(host))
    retained = host.trading.get_calendar_observation(
        artifact_sha256=out['calendar_sha256'], decision_time=AT)
    assert retained.session == SESSION and retained.next_session == THROUGH
    assert retained.observed_at == AT and retained.admission == 'NOT_ESTABLISHED'


def test_rerun_keeps_first_calendar_observation(host, master, clock):
    first = run_egx_evidence_retention_job(**kwargs(host))
    clock[0] = AT + timedelta(hours=1)
    again = run_egx_evidence_retention_job(**kwargs(host))
    assert again['calendar_sha256'] == first['calendar_sha256']
    assert again['calendar_observed_at'] == first['calendar_observed_at'] == AT.isoformat()
    assert calendar_count(host) == 1


def test_job_accepts_no_caller_clock():
    params = inspect.signature(run_egx_evidence_retention_job).parameters
    assert set(params) == {'db_path', 'raw_root', 'session', 'through', 'identity_raw_path'}


@pytest.mark.parametrize('field,value', [
    ('db_path', 'relative.db'), ('raw_root', 'raw'), ('db_path', '/x\x00y'),
    ('session', None), ('session', '2026-11-3'), ('session', '20261103'),
    ('session', '2026-02-30'), ('session', SESSION), ('through', ''),
    ('through', '2026-11-04T00:00:00'), ('identity_raw_path', ''),
    ('identity_raw_path', None), ('identity_raw_path', 'a\x00b'),
])
def test_invalid_configuration_retains_nothing(host, master, field, value):
    failed(host, 'INPUT', 'CONFIG_INVALID', **{field: value})
    assert master.calls == [] and calendar_count(host) == 0


def test_missing_database_is_never_created(host, master, tmp_path):
    missing = tmp_path / 'absent' / 'platform.db'
    failed(host, 'INPUT', 'DATABASE_UNAVAILABLE', db_path=str(missing))
    assert not missing.exists() and not missing.parent.exists()
    assert master.calls == []


def test_unknown_identity_raw_path_retains_nothing(host, master):
    failed(host, 'INPUT', 'IDENTITY_MANIFEST_UNAVAILABLE', identity_raw_path='egid/none.json')
    assert master.calls == [] and calendar_count(host) == 0


def test_non_egid_manifest_is_rejected(host, master):
    other = _store(host.db, host.raw_root, provider='other')
    failed(host, 'INPUT', 'IDENTITY_MANIFEST_REJECTED', identity_raw_path=other.raw_path)
    assert master.calls == [] and calendar_count(host) == 0


def test_tampered_identity_bytes_retain_nothing(host, master):
    (host.raw_root / host.manifest.raw_path).write_bytes(b'{}')  # same size, new hash
    failed(host, 'INPUT', 'IDENTITY_ARTIFACT_UNREADABLE')
    assert master.calls == [] and calendar_count(host) == 0


def test_real_repository_without_admission_receipt_synthesizes_nothing(host):
    # Real SecurityMasterRepository: no committed admission for (egid, session,
    # hash) exists, so no identity observation may be retained and the calendar
    # step is never reached.
    failed(host, 'IDENTITY', 'IDENTITY_ADMISSION_UNAVAILABLE')
    with host.db.connect() as connection:
        assert connection.execute(
            'SELECT COUNT(*) FROM security_master_observations').fetchone()[0] == 0
    assert calendar_count(host) == 0


def test_identity_rejection_stops_before_calendar(host, master):
    master.result = ValueError('security master observation binding mismatch')
    error = failed(host, 'IDENTITY', 'IDENTITY_OBSERVATION_REJECTED')
    assert 'binding mismatch' in error.detail
    assert calendar_count(host) == 0


def test_incomplete_calendar_window_is_rejected_without_retention(host, master):
    failed(host, 'CALENDAR', 'CALENDAR_OBSERVATION_REJECTED',
           through=(THROUGH + timedelta(days=1)).isoformat())
    assert calendar_count(host) == 0
    assert len(master.calls) == 1  # identity retention is independent and idempotent


@pytest.mark.parametrize('now', [NEXT_OPEN, NEXT_OPEN + timedelta(minutes=1)])
def test_calendar_retained_at_or_after_next_open_is_rejected(host, master, clock, now):
    clock[0] = now
    failed(host, 'CALENDAR', 'CALENDAR_OBSERVATION_REJECTED')
    assert calendar_count(host) == 0


def test_reversed_window_is_rejected(host, master):
    failed(host, 'CALENDAR', 'CALENDAR_OBSERVATION_REJECTED',
           session=THROUGH.isoformat(), through=SESSION.isoformat())
    assert calendar_count(host) == 0


def _cli(host, **overrides):
    values = kwargs(host, **overrides)
    return ['--db-path', values['db_path'], '--raw-root', values['raw_root'],
            '--session', values['session'], '--through', values['through'],
            '--identity-raw-path', values['identity_raw_path']]


def test_cli_success_prints_one_json_line(host, master, capsys):
    assert main(_cli(host)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out['status'] == 'RETAINED' and out['live_money'] is False
    assert len(out['calendar_sha256']) == 64


def test_cli_failure_reports_stage_reason_and_exit_code(host, capsys):
    assert main(_cli(host)) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['status'], out['stage'], out['reason']) == (
        'FAILED', 'IDENTITY', 'IDENTITY_ADMISSION_UNAVAILABLE')
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert main(_cli(host, session='bad')) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('INPUT', 'CONFIG_INVALID')


def test_error_fields():
    error = EvidenceRetentionJobError('CALENDAR', 'CALENDAR_OBSERVATION_REJECTED', 'x')
    assert (error.stage, error.reason, error.detail) == (
        'CALENDAR', 'CALENDAR_OBSERVATION_REJECTED', 'x')
    assert json.dumps(list(RETENTION_REASONS))
