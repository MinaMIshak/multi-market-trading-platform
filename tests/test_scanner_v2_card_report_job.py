"""Artificial retained inputs, disposable databases and temporary directories only.

No scheduler, runtime producer, market coverage, admission or edge evidence is
implied by these tests.
"""
from datetime import datetime
from hashlib import sha256
import json
import os
from types import SimpleNamespace

import pytest

import app.scanner_v2_card_report_job as job
from app.data.ingestion_repository import DataIngestionRepository
from app.data.models import DataAssetType
from app.data.raw_store import ImmutableRawStore
from app.scanner_v2_card_report_job import (
    JOB_REASONS, JOB_SCHEMA, CardReportJobError, main, run_egx_card_report_job)
from app.scanner_v2_card_report_loader import ENV, load_retained_egx_card_report
from app.scanner_v2_card_report_publisher import CardReportPublicationError
from app.scanner_v2_retained_evidence import RetainedEvidenceError
from app.storage import Database, SecurityMasterRepository, TradingRepository
from tests.test_scanner_v2_card_report import VIEW, evidence
from tests.test_scanner_v2_egx_rank import report
from tests.test_scanner_v2_retained_binding import SESSION

SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}
IDENTITY_BYTES = b'[]'
CALENDAR = 'c' * 64


def _store(db, raw_root, provider='egid', payload=IDENTITY_BYTES):
    manifest = ImmutableRawStore(raw_root).store_bytes(
        provider=provider, asset_type=DataAssetType.SECURITY_MASTER, payload=payload,
        filename='egid.json', market_date=SESSION, record_count=0)
    return DataIngestionRepository(db).save_manifest(manifest)


@pytest.fixture
def host(tmp_path):
    tmp_path.chmod(0o700)
    db = Database(tmp_path / 'platform.db')
    db.initialize()
    raw_root = tmp_path / 'raw'
    manifest = _store(db, raw_root)
    ranking = tmp_path / 'egx-ranking.json'
    artifact = report()
    ranking.write_bytes(artifact)
    out = tmp_path / 'cards'
    out.mkdir()
    out.chmod(0o700)
    return SimpleNamespace(db=db, raw_root=raw_root, manifest=manifest, ranking=ranking,
                           artifact=artifact, out=out)


@pytest.fixture
def fake_loader(monkeypatch):
    calls = []

    def fake(artifact, **kwargs):
        calls.append((artifact, kwargs))
        return evidence()

    monkeypatch.setattr(job, 'load_retained_egx_rank_binding', fake)
    return calls


def kwargs(host, **overrides):
    values = dict(db_path=str(host.db.path), raw_root=str(host.raw_root),
                  ranking_path=str(host.ranking),
                  ranking_sha256=sha256(host.artifact).hexdigest(),
                  identity_raw_path=host.manifest.raw_path, calendar_sha256=CALENDAR,
                  directory=str(host.out), now=VIEW)
    values.update(overrides)
    return values


def failed(reason, host, **overrides):
    assert reason in JOB_REASONS
    with pytest.raises(CardReportJobError) as info:
        run_egx_card_report_job(**kwargs(host, **overrides))
    assert info.value.reason == reason
    assert list(host.out.iterdir()) == []
    return info.value


def test_job_wires_retained_inputs_into_publisher_and_loader_serves_it(host, fake_loader):
    out = run_egx_card_report_job(**kwargs(host))
    assert out['schema_version'] == JOB_SCHEMA and out['status'] == 'PUBLISHED'
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert out['ranking_sha256'] == sha256(host.artifact).hexdigest()
    assert out['identity_artifact_sha256'] == sha256(IDENTITY_BYTES).hexdigest()
    assert out['calendar_sha256'] == CALENDAR
    [(artifact, call)] = fake_loader
    assert artifact == host.artifact
    assert call['expected_sha256'] == sha256(host.artifact).hexdigest()
    assert call['identity_artifact'] == IDENTITY_BYTES
    assert call['identity_manifest'] == host.manifest
    assert call['calendar_sha256'] == CALENDAR
    assert type(call['security_master']) is SecurityMasterRepository
    assert type(call['trading']) is TradingRepository
    served = load_retained_egx_card_report(environ={ENV: str(host.out)}, now=VIEW)
    assert served['status'] == 'AVAILABLE'
    assert served['report_sha256'] == out['publication']['report_sha256']
    assert datetime.fromisoformat(served['report']['observed_at']) == VIEW


def test_rerun_with_same_inputs_is_already_current(host, fake_loader):
    first = run_egx_card_report_job(**kwargs(host))
    again = run_egx_card_report_job(**kwargs(host))
    assert again['status'] == 'ALREADY_CURRENT'
    assert again['publication']['report_sha256'] == first['publication']['report_sha256']


@pytest.mark.parametrize('field,value', [
    ('db_path', 'relative.db'), ('raw_root', 'raw'), ('ranking_path', 'egx-ranking.json'),
    ('directory', 'cards'), ('ranking_sha256', 'A' * 64), ('ranking_sha256', 'a' * 63),
    ('calendar_sha256', None), ('calendar_sha256', 'g' * 64), ('identity_raw_path', ''),
    ('identity_raw_path', None), ('db_path', '/x\x00y'),
])
def test_invalid_configuration_reads_nothing(host, fake_loader, field, value):
    failed('CONFIG_INVALID', host, **{field: value})
    assert fake_loader == []


def test_missing_database_is_never_created(host, fake_loader, tmp_path):
    missing = tmp_path / 'absent' / 'platform.db'
    failed('DATABASE_UNAVAILABLE', host, db_path=str(missing))
    assert not missing.exists() and not missing.parent.exists()
    assert fake_loader == []


def test_symlinked_database_is_refused(host, fake_loader, tmp_path):
    link = tmp_path / 'link.db'
    link.symlink_to(host.db.path)
    failed('DATABASE_UNAVAILABLE', host, db_path=str(link))


def test_uninitialized_database_is_unavailable(host, fake_loader, tmp_path):
    empty = tmp_path / 'empty.db'
    empty.write_bytes(b'')
    failed('DATABASE_UNAVAILABLE', host, db_path=str(empty))
    assert fake_loader == []


def test_unknown_identity_raw_path_is_unavailable(host, fake_loader):
    failed('IDENTITY_MANIFEST_UNAVAILABLE', host, identity_raw_path='egid/none.json')
    assert fake_loader == []


def test_non_egid_manifest_is_rejected(host, fake_loader):
    other = _store(host.db, host.raw_root, provider='other')
    failed('IDENTITY_MANIFEST_REJECTED', host, identity_raw_path=other.raw_path)
    assert fake_loader == []


def test_tampered_identity_bytes_are_unreadable(host, fake_loader):
    path = host.raw_root / host.manifest.raw_path
    path.write_bytes(b'{}')  # same size, different SHA-256
    failed('IDENTITY_ARTIFACT_UNREADABLE', host)
    assert fake_loader == []


def test_missing_identity_bytes_are_unreadable(host, fake_loader):
    os.unlink(host.raw_root / host.manifest.raw_path)
    failed('IDENTITY_ARTIFACT_UNREADABLE', host)


@pytest.mark.parametrize('make', ['missing', 'symlink', 'directory'])
def test_ranking_must_be_existing_regular_non_symlink_file(host, fake_loader, tmp_path, make):
    target = tmp_path / f'ranking-{make}'
    if make == 'symlink':
        target.symlink_to(host.ranking)
    elif make == 'directory':
        target.mkdir()
    failed('RANKING_UNREADABLE', host, ranking_path=str(target))
    assert fake_loader == []


def test_evidence_failure_publishes_nothing(host, monkeypatch):
    def reject(artifact, **kwargs):
        raise RetainedEvidenceError('CALENDAR_OBSERVATION_UNAVAILABLE')

    monkeypatch.setattr(job, 'load_retained_egx_rank_binding', reject)
    with pytest.raises(RetainedEvidenceError):
        run_egx_card_report_job(**kwargs(host))
    assert list(host.out.iterdir()) == []


def test_real_loader_without_retained_identity_observation_fails_closed(host):
    # Real repositories on a fresh database: no observation was ever retained, so
    # nothing may be synthesized and nothing is published or written.
    with pytest.raises(RetainedEvidenceError) as info:
        run_egx_card_report_job(**kwargs(host))
    assert info.value.reason == 'IDENTITY_OBSERVATION_UNAVAILABLE'
    assert list(host.out.iterdir()) == []


def test_publication_refusal_propagates(host, fake_loader):
    host.out.chmod(0o777)
    with pytest.raises(CardReportPublicationError) as info:
        run_egx_card_report_job(**kwargs(host))
    assert info.value.reason == 'DIRECTORY_UNSAFE'
    assert list(host.out.iterdir()) == []


def _cli(host, **overrides):
    values = kwargs(host, **overrides)
    return ['--db-path', values['db_path'], '--raw-root', values['raw_root'],
            '--ranking-report', values['ranking_path'],
            '--ranking-sha256', values['ranking_sha256'],
            '--identity-raw-path', values['identity_raw_path'],
            '--calendar-sha256', values['calendar_sha256'],
            '--output-dir', values['directory']]


def test_cli_reports_fixed_failure_stage_and_exit_code(host, capsys):
    assert main(_cli(host)) == 1
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'FAILED' and out['stage'] == 'EVIDENCE'
    assert out['reason'] == 'IDENTITY_OBSERVATION_UNAVAILABLE'
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert main(_cli(host, ranking_sha256='bad')) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('INPUT', 'CONFIG_INVALID')


def test_cli_success_exit_code(host, fake_loader, capsys, monkeypatch):
    real = job.publish_egx_card_report
    monkeypatch.setattr(job, 'publish_egx_card_report',
                        lambda *a, **k: real(*a, **{**k, 'now': VIEW}))
    assert main(_cli(host)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'PUBLISHED' and out['live_money'] is False


def test_error_reason_and_detail():
    error = CardReportJobError('CONFIG_INVALID', 'x')
    assert (error.reason, error.detail) == ('CONFIG_INVALID', 'x')
    assert json.dumps(list(JOB_REASONS))
