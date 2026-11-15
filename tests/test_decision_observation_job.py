"""Artificial retained inputs, disposable databases and temporary directories only.

No scheduler, runtime producer, market coverage, outcome, admission or edge
evidence is implied by these tests.
"""
from datetime import timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

import app.decision_observation_job as job
import app.scanner_v2_card_report_job as card_job
from app.decision_observation_job import (
    CONTRACT_VERSION, EXIT_CODES, FAILURE_STAGES, JOB_STATUSES, main,
    run_egx_decision_observation_job)
from app.decision_observation_producer import (
    DecisionObservationProductionError, produce_egx_decision_observations)
from app.decision_observation_store import load_retained_decision_observations
from app.scanner_v2_card_report_job import JOB_REASONS, CardReportJobError
from app.scanner_v2_retained_evidence import RetainedEvidenceError
from app.storage import Database, SecurityMasterRepository, TradingRepository
from tests.test_scanner_v2_card_report import VIEW, evidence
from tests.test_scanner_v2_card_report_job import IDENTITY_BYTES, _store
from tests.test_scanner_v2_egx_rank import report
from tests.test_scanner_v2_retained_binding import OPEN

NOW = VIEW + timedelta(minutes=5)
CALENDAR = 'c' * 64
SAFETY = {'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


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
    out = tmp_path / 'observations'
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
                  directory=str(host.out), now=NOW)
    values.update(overrides)
    return values


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def test_job_wires_retained_inputs_into_producer_and_store(host, fake_loader):
    out = run_egx_decision_observation_job(**kwargs(host))
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-observation-job-v1'
    assert out['status'] == 'PRODUCED' and out['status'] in JOB_STATUSES
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
    production = out['production']
    assert production['schema_version'] == 'decision-observation-production-v1'
    assert production['ranking_artifact_sha256'] == sha256(host.artifact).hexdigest()
    assert production['produced_at'] == NOW.isoformat().replace('+00:00', 'Z')
    assert production['status_counts'] == {
        'RETAINED': 2, 'ALREADY_RETAINED': 0, 'WINDOW_CLOSED': 0}
    assert production['event_context'] == 'NOT_ATTACHED'
    assert production['outcome_status'] == 'NOT_OBSERVED'
    loaded = dict(load_retained_decision_observations(str(host.out), market='EGX'))
    assert set(loaded) == {e['identity'] for e in production['decisions']}
    for entry in production['decisions']:
        held = loaded[entry['identity']]
        assert held['decision_id'] == entry['decision_id']
        assert held['recorded_at'] == entry['recorded_at']
        assert held['live_money'] is False
    json.dumps(out, allow_nan=False)


def test_job_binds_exactly_what_the_card_report_job_binds(host, monkeypatch, tmp_path):
    calls = []

    def fake(artifact, **kwargs):
        calls.append((artifact, kwargs))
        return evidence()

    monkeypatch.setattr(job, 'load_retained_egx_rank_binding', fake)
    monkeypatch.setattr(card_job, 'load_retained_egx_rank_binding', fake)
    cards = tmp_path / 'cards'
    cards.mkdir()
    cards.chmod(0o700)
    run_egx_decision_observation_job(**kwargs(host))
    card_job.run_egx_card_report_job(**kwargs(host, directory=str(cards), now=VIEW))
    [(a1, k1), (a2, k2)] = calls
    assert a1 == a2 == host.artifact
    assert set(k1) == set(k2)
    for key in ('expected_sha256', 'identity_artifact', 'identity_manifest', 'calendar_sha256'):
        assert k1[key] == k2[key], key
    for key in ('security_master', 'trading'):
        assert type(k1[key]) is type(k2[key]), key


def test_rerun_keeps_first_observations_and_writes_nothing(host, fake_loader):
    first = run_egx_decision_observation_job(**kwargs(host))
    before = snapshot(host.out)
    again = run_egx_decision_observation_job(**kwargs(host, now=NOW + timedelta(minutes=30)))
    assert snapshot(host.out) == before
    assert again['status'] == 'PRODUCED'
    assert again['production']['status_counts'] == {
        'RETAINED': 0, 'ALREADY_RETAINED': 2, 'WINDOW_CLOSED': 0}
    assert [e['recorded_at'] for e in again['production']['decisions']] == \
        [e['recorded_at'] for e in first['production']['decisions']]


def test_late_run_is_visible_and_never_backfilled(host, fake_loader):
    out = run_egx_decision_observation_job(**kwargs(host, now=OPEN))
    assert out['status'] == 'WINDOW_CLOSED_PRESENT'
    assert out['production']['status_counts'] == {
        'RETAINED': 0, 'ALREADY_RETAINED': 0, 'WINDOW_CLOSED': 2}
    assert snapshot(host.out) == []


@pytest.mark.parametrize('field,value', [
    ('db_path', 'relative.db'), ('raw_root', 'raw'), ('ranking_path', 'egx-ranking.json'),
    ('directory', 'observations'), ('directory', None), ('ranking_sha256', 'A' * 64),
    ('ranking_sha256', 'a' * 63), ('calendar_sha256', None), ('calendar_sha256', 'g' * 64),
    ('identity_raw_path', ''), ('identity_raw_path', None), ('directory', '/x\x00y'),
])
def test_invalid_configuration_reads_and_writes_nothing(host, fake_loader, field, value):
    with pytest.raises(CardReportJobError) as info:
        run_egx_decision_observation_job(**kwargs(host, **{field: value}))
    assert info.value.reason == 'CONFIG_INVALID' and 'CONFIG_INVALID' in JOB_REASONS
    assert fake_loader == [] and snapshot(host.out) == []


def test_missing_database_is_never_created(host, fake_loader, tmp_path):
    missing = tmp_path / 'absent' / 'platform.db'
    with pytest.raises(CardReportJobError) as info:
        run_egx_decision_observation_job(**kwargs(host, db_path=str(missing)))
    assert info.value.reason == 'DATABASE_UNAVAILABLE'
    assert not missing.exists() and not missing.parent.exists()
    assert fake_loader == [] and snapshot(host.out) == []


def test_evidence_failure_writes_nothing(host, monkeypatch):
    def reject(artifact, **kwargs):
        raise RetainedEvidenceError('CALENDAR_OBSERVATION_UNAVAILABLE')

    monkeypatch.setattr(job, 'load_retained_egx_rank_binding', reject)
    with pytest.raises(RetainedEvidenceError):
        run_egx_decision_observation_job(**kwargs(host))
    assert snapshot(host.out) == []


def test_real_loader_without_retained_identity_observation_fails_closed(host):
    # Fresh database: no observation was ever retained, so nothing may be
    # synthesized and no decision observation is written.
    with pytest.raises(RetainedEvidenceError) as info:
        run_egx_decision_observation_job(**kwargs(host))
    assert info.value.reason == 'IDENTITY_OBSERVATION_UNAVAILABLE'
    assert snapshot(host.out) == []


def test_production_refusal_propagates(host, fake_loader):
    host.out.chmod(0o777)
    with pytest.raises(DecisionObservationProductionError) as info:
        run_egx_decision_observation_job(**kwargs(host))
    assert info.value.reason == 'RETENTION_FAILED'
    assert 'DIRECTORY_UNSAFE' in str(info.value.detail)
    assert snapshot(host.out) == []


def _cli(host, **overrides):
    values = kwargs(host, **overrides)
    return ['--db-path', values['db_path'], '--raw-root', values['raw_root'],
            '--ranking-report', values['ranking_path'],
            '--ranking-sha256', values['ranking_sha256'],
            '--identity-raw-path', values['identity_raw_path'],
            '--calendar-sha256', values['calendar_sha256'],
            '--output-dir', values['directory']]


def _clock(monkeypatch, now):
    # Always wrap the unchanged producer, so repeated calls replace the clock.
    monkeypatch.setattr(job, 'produce_egx_decision_observations',
                        lambda *a, **k: produce_egx_decision_observations(
                            *a, **{**k, 'now': now}))


def test_cli_reports_fixed_failure_stage_and_exit_code(host, capsys, monkeypatch):
    assert main(_cli(host)) == EXIT_CODES['FAILED'] == 1
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'FAILED' and out['stage'] == 'EVIDENCE'
    assert out['reason'] == 'IDENTITY_OBSERVATION_UNAVAILABLE'
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert main(_cli(host, ranking_sha256='bad')) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('INPUT', 'CONFIG_INVALID')
    monkeypatch.setattr(job, 'load_retained_egx_rank_binding', lambda a, **k: evidence())
    host.out.chmod(0o777)
    _clock(monkeypatch, NOW)
    assert main(_cli(host)) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('PRODUCTION', 'RETENTION_FAILED')
    assert set(FAILURE_STAGES) == {'INPUT', 'EVIDENCE', 'PRODUCTION'}
    assert snapshot(host.out) == []


def test_cli_success_and_late_run_exit_codes(host, fake_loader, capsys, monkeypatch):
    _clock(monkeypatch, OPEN)
    assert main(_cli(host)) == EXIT_CODES['WINDOW_CLOSED_PRESENT'] == 2
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'WINDOW_CLOSED_PRESENT' and out['live_money'] is False
    assert snapshot(host.out) == []
    _clock(monkeypatch, NOW)
    assert main(_cli(host)) == EXIT_CODES['PRODUCED'] == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'PRODUCED' and out['live_money'] is False
    assert out['production']['status_counts']['RETAINED'] == 2
    assert set(EXIT_CODES) == set(JOB_STATUSES)
