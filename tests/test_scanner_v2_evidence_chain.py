"""End-to-end offline chain on one disposable database with controlled clocks.

security-master refresh (real admission receipt) -> evidence-retention job ->
EGX ranking bytes -> card-report job -> protected loader.

Every component is the unchanged repository implementation; only wall clocks are
replaced so the ordering refresh < retention < ranking generation < publication
< next-session open is explicit. Artificial EGID records, calendar rows and
ranking only: no scheduler, runtime producer, admission, market coverage or edge
evidence is implied.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

import app.data.ingestion_repository as ingestion_module
import app.data.raw_store as raw_store_module
import app.storage.repository as repository_module
import app.storage.security_master_repository as master_module
from app.domain import MarketSession
from app.scanner_v2_card_report_job import run_egx_card_report_job
from app.scanner_v2_card_report_loader import ENV, load_retained_egx_card_report
from app.scanner_v2_evidence_retention_job import run_egx_evidence_retention_job
from app.scanner_v2_retained_evidence import RetainedEvidenceError
from app.storage import TradingRepository
from tests.test_scanner_v2_egx_rank import GENERATED, report
from tests.test_security_master_refresh_runtime import FakeEGIDProvider, build, egid_record

UTC = timezone.utc
SESSION = '2026-11-03'
THROUGH = '2026-11-04'
TICKERS = ('AAAA', 'BBBB', 'CCCC', 'DDDD', 'EEEE')  # the ranking fixture's scope
CODES = {t: f'EGTEST{n:06d}' for n, t in enumerate(TICKERS, start=1)}
REFRESH_AT = datetime(2026, 11, 3, 17, tzinfo=UTC)   # after the 13:00Z verification
RETAIN_AT = datetime(2026, 11, 4, 5, tzinfo=UTC)     # before ranking generation
GEN = datetime.fromisoformat(GENERATED)              # 2026-11-04T06:39:32.4276Z
PUBLISH_AT = datetime(2026, 11, 4, 7, tzinfo=UTC)    # before the 08:00Z next open
SAFETY = {'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
          'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


def _calendar_rows():
    return [
        dict(market_date='2026-11-03', status='VERIFIED', opened_at='2026-11-03T10:00:00+02:00',
             closed_at='2026-11-03T14:30:00+02:00', data_verified_at='2026-11-03T15:00:00+02:00'),
        dict(market_date='2026-11-04', status='UNKNOWN', opened_at='2026-11-04T10:00:00+02:00',
             closed_at='2026-11-04T14:30:00+02:00'),
    ]


@pytest.fixture
def clock(monkeypatch):
    current = [REFRESH_AT]

    class ControlledDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = current[0]
            return value if tz is None else value.astimezone(tz)

    def utc_now():
        return current[0].isoformat()

    monkeypatch.setattr(raw_store_module, 'datetime', ControlledDatetime)
    for module in (ingestion_module, repository_module, master_module):
        monkeypatch.setattr(module, '_utc_now', utc_now)
    return current


@pytest.fixture
def host(tmp_path, clock):
    tmp_path.chmod(0o700)
    database, runtime = build(tmp_path)  # raw store at <tmp>/data/raw
    records = [egid_record(symbol_code=CODES[t], reuters=f'{t}.CA') for t in TICKERS]
    result = runtime.job.run(provider=FakeEGIDProvider(records),
                             snapshot_date=date.fromisoformat(SESSION))
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT raw_path FROM data_ingestions WHERE provider = 'egid' AND sha256 = ?",
            (result.artifact_sha256,)).fetchall()
    assert len(rows) == 1
    trading = TradingRepository(database)
    for row in _calendar_rows():
        trading.save_market_session(MarketSession.model_validate(row))
    ranking = tmp_path / 'egx-ranking.json'
    artifact = report()
    ranking.write_bytes(artifact)
    out = tmp_path / 'cards'
    out.mkdir()
    out.chmod(0o700)
    return SimpleNamespace(db=database, raw_root=tmp_path / 'data' / 'raw',
                           identity_raw_path=rows[0]['raw_path'], result=result,
                           ranking=ranking, artifact=artifact, out=out)


def retain(host):
    return run_egx_evidence_retention_job(
        db_path=str(host.db.path), raw_root=str(host.raw_root), session=SESSION,
        through=THROUGH, identity_raw_path=host.identity_raw_path)


def publish(host, calendar_sha256):
    return run_egx_card_report_job(
        db_path=str(host.db.path), raw_root=str(host.raw_root),
        ranking_path=str(host.ranking), ranking_sha256=sha256(host.artifact).hexdigest(),
        identity_raw_path=host.identity_raw_path, calendar_sha256=calendar_sha256,
        directory=str(host.out), now=PUBLISH_AT)


def observation_counts(host):
    with host.db.connect() as connection:
        return tuple(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in ('security_master_observations', 'calendar_observations'))


def test_real_admission_flows_through_retention_into_served_card_report(host, clock):
    clock[0] = RETAIN_AT
    retained = retain(host)
    assert retained['status'] == 'RETAINED'
    assert {k: retained[k] for k in SAFETY} == SAFETY
    assert retained['identity_artifact_sha256'] == host.result.artifact_sha256
    assert datetime.fromisoformat(retained['identity_observed_at']) == RETAIN_AT
    assert datetime.fromisoformat(retained['calendar_observed_at']) == RETAIN_AT
    assert retained['calendar_admission'] == 'NOT_ESTABLISHED'
    assert observation_counts(host) == (1, 1)

    clock[0] = PUBLISH_AT
    published = publish(host, retained['calendar_sha256'])
    assert published['status'] == 'PUBLISHED'
    assert {k: published[k] for k in SAFETY} == SAFETY
    assert observation_counts(host) == (1, 1)  # the card job only reads evidence

    served = load_retained_egx_card_report(environ={ENV: str(host.out)}, now=PUBLISH_AT)
    assert served['status'] == 'AVAILABLE'
    assert served['report_sha256'] == published['publication']['report_sha256']
    doc = served['report']
    assert {k: doc[k] for k in SAFETY} == SAFETY
    assert datetime.fromisoformat(doc['observed_at']) == PUBLISH_AT

    provenance = doc['provenance']
    assert provenance['artifact_sha256'] == sha256(host.artifact).hexdigest()
    assert provenance['session'] == SESSION
    assert provenance['identity_admission'] == 'INTERNAL_SNAPSHOT_VERIFIED'
    assert provenance['calendar_admission'] == 'NOT_ESTABLISHED'
    # Availability is the latest retained observation, never the ranking time.
    assert datetime.fromisoformat(provenance['evidence_available_at']) == RETAIN_AT
    evidence = provenance['retained_evidence']
    assert evidence['calendar']['artifact_sha256'] == retained['calendar_sha256']
    assert evidence['identity']['receipt_sha256'] == retained['identity_receipt_sha256']
    assert evidence['identity']['identity']['artifact_sha256'] == host.result.artifact_sha256
    assert evidence['binding']['instrument_ids'] == [
        [t, str(uuid5(NAMESPACE_URL, f'egx://instrument/egid/{CODES[t]}'))] for t in TICKERS]

    coverage = doc['coverage']
    assert coverage['requested'] == len(TICKERS)
    assert coverage['authoritative_universe_size'] is None
    assert coverage['identity_exclusions'] == []
    # Only the confirmed champion classes (CANDIDATE / STRONG_CANDIDATE) become cards.
    assert [r['symbol'] for r in coverage['symbols'] if r['status'] == 'RESEARCH_CANDIDATE'] == [
        'AAAA', 'BBBB', 'EEEE']
    assert coverage['candidate_count'] == len(doc['cards']) == 3
    assert sum(doc['card_state_counts'].values()) == 3
    assert doc['lifecycle_evidence']['status'] == 'NOT_VERIFIED'
    assert all(card['execution_allowed'] is False for card in doc['cards'])


def test_chain_reruns_keep_first_evidence_and_publication(host, clock):
    clock[0] = RETAIN_AT
    first = retain(host)
    # A rerun after ranking generation keeps the first (pre-generation)
    # observations; it neither moves evidence time forward nor adds rows.
    clock[0] = GEN + timedelta(minutes=5)
    again = retain(host)
    assert again['calendar_sha256'] == first['calendar_sha256']
    assert again['identity_observed_at'] == first['identity_observed_at']
    assert again['calendar_observed_at'] == first['calendar_observed_at']
    assert observation_counts(host) == (1, 1)
    clock[0] = PUBLISH_AT
    published = publish(host, first['calendar_sha256'])
    assert published['status'] == 'PUBLISHED'
    rerun = publish(host, first['calendar_sha256'])
    assert rerun['status'] == 'ALREADY_CURRENT'
    assert rerun['publication']['report_sha256'] == published['publication']['report_sha256']


def test_evidence_retained_after_ranking_generation_is_never_backdated(host, clock):
    clock[0] = GEN + timedelta(minutes=5)  # retained late, still before next open
    retained = retain(host)
    assert retained['status'] == 'RETAINED'
    clock[0] = PUBLISH_AT
    with pytest.raises(RetainedEvidenceError) as info:
        publish(host, retained['calendar_sha256'])
    assert info.value.reason == 'IDENTITY_REJECTED'
    assert list(host.out.iterdir()) == []
    assert observation_counts(host) == (1, 1)


def test_card_job_without_retention_publishes_nothing(host, clock):
    clock[0] = PUBLISH_AT
    with pytest.raises(RetainedEvidenceError) as info:
        publish(host, 'c' * 64)
    assert info.value.reason == 'IDENTITY_OBSERVATION_UNAVAILABLE'
    assert list(host.out.iterdir()) == []
    assert observation_counts(host) == (0, 0)
