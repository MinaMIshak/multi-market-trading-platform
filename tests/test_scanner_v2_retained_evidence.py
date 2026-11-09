"""Artificial retained evidence and disposable databases only.

No admission, market coverage, provider health, runtime or edge evidence.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

import app.scanner_v2_retained_evidence as module
import app.storage.repository as repository_module
from app.data.models import DataAssetType, RawArtifactManifest
from app.domain import MarketSession
from app.scanner_v2_card_report import project_egx_card_report
from app.scanner_v2_retained_binding import compose_egx_rank_binding
from app.scanner_v2_retained_evidence import (
    EVIDENCE_REASONS, RetainedEvidenceError, load_retained_egx_rank_binding, ranking_scope)
from app.storage import Database, TradingRepository
from tests.test_scanner_v2_egx_rank import GENERATED, record, report
from tests.test_scanner_v2_retained_binding import RAW, SESSION, calendar, identity

UTC = timezone.utc
GEN = datetime.fromisoformat(GENERATED)
SCOPE = ('AAAA', 'BBBB', 'CCCC', 'DDDD', 'EEEE')
IDENTITY_BYTES = b'[]'
OBSERVATION = {'schema_version': 'security-master-observation-v1', 'retained': True}
MANIFEST = RawArtifactManifest(
    provider='egid', asset_type=DataAssetType.SECURITY_MASTER, market_date=SESSION,
    raw_path='/retained/egid.json', sha256=RAW, byte_size=2, record_count=0,
    received_at=datetime(2026, 11, 3, 17, tzinfo=UTC))


class FakeMaster:
    """Exposes only the read method; any retain/write call would fail."""

    def __init__(self, result=OBSERVATION):
        self.result, self.calls = result, []

    def get_admission_observation(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeTrading:
    def __init__(self, result=None):
        self.result, self.calls = result, []

    def get_calendar_observation(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result if self.result is not None else calendar()


@pytest.fixture
def binder(monkeypatch):
    calls = []

    def fake(artifact, **kwargs):
        calls.append((artifact, kwargs))
        return identity(session=kwargs['session'], observed_at=kwargs['observed_at'])

    monkeypatch.setattr(module, 'bind_observed_egid_identities', fake)
    return calls


def load(artifact=None, master=None, trading=None, manifest=MANIFEST, calendar_sha256=None,
         expected=None):
    artifact = artifact if artifact is not None else report()
    return load_retained_egx_rank_binding(
        artifact, expected_sha256=expected or sha256(artifact).hexdigest(),
        identity_artifact=IDENTITY_BYTES, identity_manifest=manifest,
        security_master=master or FakeMaster(), trading=trading or FakeTrading(),
        calendar_sha256=calendar_sha256 or calendar().artifact_sha256)


def reason(excinfo):
    assert excinfo.value.reason in EVIDENCE_REASONS
    return excinfo.value.reason


def test_composes_binding_from_retained_reads_at_ranking_generation(binder):
    master, trading = FakeMaster(), FakeTrading()
    evidence = load(master=master, trading=trading)
    assert master.calls == [dict(provider='egid', snapshot_date=SESSION, artifact_sha256=RAW)]
    [(artifact, kwargs)] = binder
    assert artifact == IDENTITY_BYTES
    assert kwargs == dict(manifest=MANIFEST, observation=OBSERVATION, session=SESSION,
                          observed_at=GEN, requested=SCOPE)
    assert trading.calls == [dict(artifact_sha256=calendar().artifact_sha256, decision_time=GEN)]
    assert evidence == compose_egx_rank_binding(
        identity=identity(observed_at=GEN), calendar=calendar())
    assert evidence.identity_admission == 'INTERNAL_SNAPSHOT_VERIFIED'
    assert evidence.calendar_admission == 'NOT_ESTABLISHED'
    assert evidence.runtime_activation is False and evidence.live_money is False


def test_loaded_binding_feeds_unchanged_card_report_projection(binder):
    artifact = report()
    doc = project_egx_card_report(artifact, expected_sha256=sha256(artifact).hexdigest(),
                                  evidence=load(artifact=artifact), observed_at=GEN)
    assert doc['coverage']['requested'] == 5 and doc['coverage']['candidate_count'] == 2
    assert doc['live_money'] is False and doc['runtime_activation'] is False


def test_ranking_scope_reads_session_generation_and_sorted_tickers():
    artifact = report(records=[record('BBBB', 'CANDIDATE'), record('AAAA', 'NO_TRADE')])
    session, generated, tickers = ranking_scope(
        artifact, expected_sha256=sha256(artifact).hexdigest())
    assert (session, generated, tickers) == (SESSION, GEN, ('AAAA', 'BBBB'))


@pytest.mark.parametrize('artifact,expected', [
    (report(), '0' * 64),
    ('not bytes', None),
    (report(schema='other'), None),
    (report(generated_at='2026-11-04T06:39:32'), None),
    (report(generated_at=7), None),
    (report(based_on_session='03/11/2026'), None),
    (report(symbols={}), None),
    (report(records=[record('AAAA', 'CANDIDATE'), record('AAAA', 'NO_TRADE')]), None),
    (report(records=[record('a b', 'CANDIDATE')]), None),
    (b'{"schema":"egx-ranking-report-v1","schema":"egx-ranking-report-v1"}', None),
    (b'{"x":NaN}', None),
    (b'[' * 100000 + b']' * 100000, None),
])
def test_invalid_ranking_fails_closed_before_any_evidence_read(binder, artifact, expected):
    master, trading = FakeMaster(), FakeTrading()
    if expected is None and type(artifact) is bytes:
        expected = sha256(artifact).hexdigest()
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load_retained_egx_rank_binding(
            artifact, expected_sha256=expected or '0' * 64, identity_artifact=IDENTITY_BYTES,
            identity_manifest=MANIFEST, security_master=master, trading=trading,
            calendar_sha256=calendar().artifact_sha256)
    assert reason(excinfo) == 'RANKING_INVALID'
    assert master.calls == trading.calls == binder == []


def test_oversized_ranking_rejected(binder):
    artifact = b' ' * (module.MAX_BYTES + 1)
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(artifact=artifact)
    assert reason(excinfo) == 'RANKING_INVALID'


@pytest.mark.parametrize('failure,expected', [
    (KeyError('retained security master observation unavailable'),
     'IDENTITY_OBSERVATION_UNAVAILABLE'),
    (ValueError('security master observation integrity mismatch'), 'IDENTITY_REJECTED'),
])
def test_identity_observation_failures_fail_closed(binder, failure, expected):
    trading = FakeTrading()
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(master=FakeMaster(failure), trading=trading)
    assert reason(excinfo) == expected
    assert binder == [] and trading.calls == []


def test_identity_manifest_type_required(binder):
    master = FakeMaster()
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(master=master, manifest=MANIFEST.model_dump())
    assert reason(excinfo) == 'IDENTITY_REJECTED'
    assert master.calls == [] and binder == []


def test_identity_binder_rejection_fails_closed(monkeypatch):
    def reject(artifact, **kwargs):
        raise ValueError('admission unavailable at observation')

    monkeypatch.setattr(module, 'bind_observed_egid_identities', reject)
    trading = FakeTrading()
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(trading=trading)
    assert reason(excinfo) == 'IDENTITY_REJECTED'
    assert trading.calls == []


def test_identity_observed_after_ranking_generation_cannot_backdate(monkeypatch):
    def late(artifact, **kwargs):
        return identity(available_at=kwargs['observed_at'] + timedelta(seconds=1),
                        observed_at=kwargs['observed_at'])

    monkeypatch.setattr(module, 'bind_observed_egid_identities', late)
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load()
    assert reason(excinfo) == 'BINDING_REJECTED'


@pytest.mark.parametrize('failure,expected', [
    (KeyError('retained calendar observation unavailable'), 'CALENDAR_OBSERVATION_UNAVAILABLE'),
    (ValueError('calendar unavailable at decision'), 'CALENDAR_REJECTED'),
])
def test_calendar_observation_failures_fail_closed(binder, failure, expected):
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(trading=FakeTrading(failure))
    assert reason(excinfo) == expected


def test_calendar_digest_type_required(binder):
    trading = FakeTrading()
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(trading=trading, calendar_sha256=b'0' * 64)
    assert reason(excinfo) == 'CALENDAR_REJECTED'
    assert trading.calls == []


def test_calendar_for_other_session_rejected(binder):
    artifact = report(based_on_session='2026-11-02')
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(artifact=artifact)
    assert reason(excinfo) == 'BINDING_REJECTED'


# Real TradingRepository: retained calendar evidence must exist before generation.

def _calendar_rows():
    return [
        dict(market_date='2026-11-03', status='VERIFIED', opened_at='2026-11-03T10:00:00+02:00',
             closed_at='2026-11-03T14:30:00+02:00', data_verified_at='2026-11-03T15:00:00+02:00'),
        dict(market_date='2026-11-04', status='UNKNOWN', opened_at='2026-11-04T10:00:00+02:00',
             closed_at='2026-11-04T14:30:00+02:00'),
    ]


def _retained_calendar(tmp_path, monkeypatch, observed_at):
    clock = [observed_at]
    monkeypatch.setattr(repository_module, '_utc_now', lambda: clock[0].isoformat())
    database = Database(tmp_path / 'platform.db')
    database.initialize()
    repo = TradingRepository(database)
    for row in _calendar_rows():
        repo.save_market_session(MarketSession.model_validate(row))
    retained = repo.retain_calendar_observation(session=SESSION, through=SESSION + timedelta(days=1))
    clock[0] = GEN + timedelta(minutes=5)
    return repo, retained


def _observation_count(repo):
    with repo.database.connect() as connection:
        return connection.execute('SELECT COUNT(*) FROM calendar_observations').fetchone()[0]


def test_real_retained_calendar_before_generation_is_used_without_writes(
        tmp_path, monkeypatch, binder):
    repo, retained = _retained_calendar(tmp_path, monkeypatch,
                                        datetime(2026, 11, 4, 5, tzinfo=UTC))
    evidence = load(trading=repo, calendar_sha256=retained.artifact_sha256)
    assert evidence.calendar == retained
    assert evidence.evidence_available_at == retained.observed_at
    assert _observation_count(repo) == 1


def test_real_calendar_retained_after_generation_cannot_backdate(tmp_path, monkeypatch, binder):
    repo, retained = _retained_calendar(tmp_path, monkeypatch,
                                        GEN + timedelta(minutes=1))
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(trading=repo, calendar_sha256=retained.artifact_sha256)
    assert reason(excinfo) == 'CALENDAR_REJECTED'
    assert _observation_count(repo) == 1


def test_real_missing_calendar_observation_is_unavailable(tmp_path, monkeypatch, binder):
    repo, _ = _retained_calendar(tmp_path, monkeypatch, datetime(2026, 11, 4, 5, tzinfo=UTC))
    with pytest.raises(RetainedEvidenceError) as excinfo:
        load(trading=repo, calendar_sha256='f' * 64)
    assert reason(excinfo) == 'CALENDAR_OBSERVATION_UNAVAILABLE'


def test_error_is_value_error_with_fixed_reason():
    error = RetainedEvidenceError('RANKING_INVALID', 'detail')
    assert isinstance(error, ValueError)
    assert (error.reason, error.detail) == ('RANKING_INVALID', 'detail')
    assert json.dumps(list(EVIDENCE_REASONS))
