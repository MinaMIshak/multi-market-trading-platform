"""ENGINEERING_FIXTURE / NOT MARKET EVIDENCE / NOT A REAL SIGNAL.

All artifacts live in pytest tmp_path; the suite's autouse socket block applies.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from app.data.provider import ProviderResponse
from app.data.quota import VerifiedQuotaCost
from app.data.raw_store import ImmutableRawStore
from app.data.security_master import CanonicalInstrument, InstrumentType
from app.paper import swing_launch, shadow_producer, shadow_collection, shadow_freeze, shadow_ledger
from app.paper.swing_launch import SwingLaunchInput, LaunchBlocked, refresh_once, run_signal
from app.paper.shadow_facts import ForwardSessionFact, SESSION_FIELDS
from app.paper.shadow_records import ShadowEvidenceReference
from app.research.historical_pit import HistoricalSessionRecord, SESSION_EVIDENCE_FIELDS, DAILY_EVIDENCE_FIELDS
from app.storage import Database
from app.storage.reference_repository import ReferenceRepository
from app.storage.security_master_repository import SecurityMasterRepository
from app.ui.shadow_input import _decode, read_shadow_input
from app.ui.shadow import load_shadow_watchlist
from tests import test_shadow_collection as fixture_packages
from tests.test_m3_point_in_time import ingest, review
from tools.paper_shadow_launch import main, configuration_status, local_path

LABEL = 'ENGINEERING_FIXTURE / NOT MARKET EVIDENCE / NOT A REAL SIGNAL'
ID = UUID(int=1)


class EngineeringProvider:
    name = 'fixture'

    def __init__(self, records):
        self.records, self.calls = records, []

    def fetch_daily_bars(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.dumps(self.records).encode()
        return ProviderResponse(payload=payload, filename=hashlib.sha256(payload).hexdigest()+'.json',
                                source_uri='fixture://'+LABEL.replace(' ', '_'),
                                record_count=len(self.records), metadata={'label': LABEL})


def package(at, fields, digest='a'*64, byte_size=10, manifest=None):
    ref = ShadowEvidenceReference(evidence_id=digest, artifact_sha256=digest,
                                 available_at=at-timedelta(minutes=2),
                                 source_authority=LABEL, source_locator=('fixture://'+LABEL.replace(' ', '_')
                                                                       if manifest else 'fixture://engineering'))
    preliminary = fixture_packages.package('a', ref, tuple(sorted(fields)))
    # Rebuild content identities after binding actual engineering raw bytes.
    raw = preliminary.raw_receipt.model_copy(update={'byte_size': byte_size,
        'local_received_at': manifest.received_at if manifest else preliminary.raw_receipt.local_received_at})
    evidence = preliminary.evidence.model_copy(update={'subject_receipt_id': raw.identity})
    review_ = preliminary.review.model_copy(update={'subject_receipt_id': raw.identity,
                                                  'availability_evidence_id': evidence.identity,
                                                  'reviewed_at': at-timedelta(minutes=1)})
    return preliminary.model_copy(update={'raw_receipt': raw, 'evidence': evidence, 'review': review_})


@pytest.fixture
def launch(tmp_path, monkeypatch, request):
    at = datetime.now(timezone.utc) + timedelta(minutes=2)
    monkeypatch.setattr(fixture_packages, 'AT', at)
    for module in (swing_launch, shadow_producer, shadow_collection, shadow_freeze, shadow_ledger):
        monkeypatch.setattr(module, '_now', lambda: at)
    day = at.date() - timedelta(days=1)
    start = day - timedelta(days=259)
    db = Database(tmp_path / 'engineering.db')
    db.initialize()
    root = tmp_path / 'engineering-data'
    root.mkdir()
    SecurityMasterRepository(db).replace_provider_snapshot(provider='fixture', instruments=[
        CanonicalInstrument(instrument_id=ID, instrument_type=InstrumentType.EQUITY,
                            canonical_ticker='COMI', source_provider='fixture',
                            source_symbol_code='COMI.EGX', source_sha256='a'*64)])
    shared = package(at, set(SESSION_EVIDENCE_FIELDS) | SESSION_FIELDS)
    records = tuple(HistoricalSessionRecord(
        market_date=start+timedelta(days=i),
        market_state='NON_SESSION' if i == 260 else 'TRADING_SESSION',
        instrument_state='NOT_APPLICABLE' if i == 260 else 'EXPECTED_OBSERVATION', evidence=shared,
    ) for i in range(262))
    def clock(d):
        opens = datetime.combine(d, datetime.min.time(), timezone.utc) + timedelta(hours=8)
        return ForwardSessionFact(market='EGX', market_date=d, calendar_mic='XCAI', state='OPEN',
                                  opens_at=opens, closes_at=opens+timedelta(hours=4),
                                  evidence_package_id=shared.identity)
    source = SwingLaunchInput(schema_version='swing-paper-launch-v1', symbol='COMI', instrument_id=ID,
                             decision_status='WATCH', planning_rule='SWING-V1-Q03-v1', history_start=start,
                             sessions=records, signal_session=clock(day),
                             entry_session=clock(day+timedelta(days=2)),
                             clock_packages=(shared,), daily_package=None)
    bars = [dict(date=str(start+timedelta(days=i)), open=100+i, high=101+i, low=99+i,
                 close=100+i, adjusted_close=100+i, volume=10000) for i in range(260)]
    # Latest close is above the previous high: unoptimized deterministic engineering trend.
    bars[-1].update(close=361, high=362)
    if getattr(request, 'param', None) == 'no_signal':
        for bar in bars:
            bar.update(open=100, high=101, low=99, close=100, adjusted_close=100)
    if getattr(request, 'param', None) == 'long_source':
        bars = [dict(date=str(start-timedelta(days=140-i)), open=1000+i,
                     high=1001+i, low=999+i, close=1000+i,
                     adjusted_close=1000+i, volume=10000) for i in range(140)] + bars
    provider = EngineeringProvider(bars)
    refresh_source = source
    if getattr(request, 'param', None) == 'long_source':
        earlier = tuple(HistoricalSessionRecord(
            market_date=start-timedelta(days=140-i), market_state='TRADING_SESSION',
            instrument_state='EXPECTED_OBSERVATION', evidence=shared,
        ) for i in range(140))
        refresh_source = source.model_copy(update={
            'history_start': start-timedelta(days=140), 'sessions': (*earlier, *records),
        })
    refreshed = refresh_once(db, root, refresh_source, provider=provider, cost=VerifiedQuotaCost(1, LABEL))
    assert refreshed['operation'] == 'REFRESH_COMPLETED_SIGNAL_NOT_RUN'
    refs = ReferenceRepository(db, ImmutableRawStore(root/'raw'))
    for bar in bars:
        doc = dict(contract='egx-universe-v1', market='EGX', effective_date=bar['date'],
                   published_at=(at-timedelta(days=400)).isoformat(), complete=True,
                   members=[dict(instrument_id=str(ID), symbol='COMI', eligible=True)])
        m = ingest(refs, doc)
        review(refs, m, doc['contract'])
    actions = dict(contract='egx-actions-v1', instrument_id=str(ID), coverage_start=bars[0]['date'],
                   coverage_end=str(day), published_at=(at-timedelta(days=400)).isoformat(),
                   complete=True, actions=[])
    m = ingest(refs, actions)
    review(refs, m, actions['contract'])
    with db.connect() as con:
        raw_path = con.execute("SELECT raw_path FROM data_ingestions WHERE asset_type='DAILY_BARS'").fetchone()[0]
    manifest = refs.ingestions.get_manifest_by_raw_path(raw_path)
    review(refs, manifest, 'egx-daily-semantic-v1')
    daily_package = package(at, DAILY_EVIDENCE_FIELDS, manifest.sha256, manifest.byte_size, manifest)
    source = source.model_copy(update={'daily_package': daily_package})
    destination = tmp_path/'engineering-collection'
    for name, value in [('EGX_DB_PATH', db.path), ('EGX_DATA_ROOT', root), ('EGX_SHADOW_DIRECTORY', destination)]:
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv('EODHD_API_TOKEN_FILE', raising=False)
    path = tmp_path/'engineering-input.json'
    path.write_text(source.model_dump_json())
    return db, root, source, destination, path, provider, at


@pytest.mark.parametrize('launch', ['long_source'], indirect=True)
@pytest.mark.parametrize('damage', [None, 'short_window', 'missing_session', 'ledger'])
def test_operational_window_preserves_full_source_binding(launch, damage):
    from app.strategies.eod import evaluate_swing_series

    db, root, source, directory, _, provider, _ = launch
    assert len(provider.records) == 400
    if damage == 'short_window':
        source = source.model_copy(update={
            'history_start': source.history_start + timedelta(days=1),
            'sessions': source.sessions[1:],
        })
    elif damage == 'missing_session':
        first = source.sessions[0].model_copy(update={
            'market_state': 'NON_SESSION', 'instrument_state': 'NOT_APPLICABLE',
        })
        source = source.model_copy(update={'sessions': (first, *source.sessions[1:])})
    elif damage == 'ledger':
        with db.connect() as con:
            con.execute("UPDATE daily_canonical_artifacts SET sha256=?", ('f'*64,))
    if damage:
        with pytest.raises(LaunchBlocked) as error:
            swing_launch.prepare_signal(db, root, source)
        assert error.value.status == ('EVIDENCE_BLOCKED' if damage == 'ledger' else 'DATA_INSUFFICIENT')
        return

    _, candidate, plan, data = swing_launch.prepare_signal(db, root, source)
    assert len(data.rows) == len(data.split_adjusted) == 260
    assert data.rows[0].market_date == source.history_start
    assert data.rows[-1].market_date == source.signal_session.market_date
    expected = evaluate_swing_series(
        closes=tuple(float(b['close']) for b in provider.records[-260:]),
        highs=tuple(float(b['high']) for b in provider.records[-260:]),
        config=swing_launch.swing_config(),
    )
    evidence = json.loads(candidate.evidence_json)
    assert evidence['fast_ema'] == expected.fast_ema
    assert evidence['slow_ema'] == expected.slow_ema
    assert evidence['source_audit_id'] == data.audit_id
    assert evidence['operational_history_window'] == {
        'history_start': str(source.history_start),
        'signal_date': str(source.signal_session.market_date), 'bar_count': 260,
    }
    assert plan.entry_reference == data.rows[-1].close
    assert run_signal(db, root, source, directory, publish=True)['status'] == 'PUBLISHED_PAPER_SIGNAL'
    with db.connect() as con:
        assert con.execute('SELECT record_count FROM daily_canonical_artifacts').fetchone()[0] == 400


def test_nonpublishing_verification_returns_scope_identity(launch):
    db, root, source, directory, _, _, _ = launch
    result = run_signal(db, root, source, directory, publish=False)
    assert result['operation'] == 'VERIFIED_SIGNAL_NOT_PUBLISHED'
    assert result['symbol'] == source.symbol
    assert result['market'] == 'EGX'
    assert not directory.exists()


@pytest.mark.parametrize('status, expected', [
    ('DATA_STALE', 'DATA_STALE'), ('DATA_INSUFFICIENT', 'NOT_READY'),
    ('EVIDENCE_BLOCKED', 'EVIDENCE_BLOCKED'),
])
def test_scanner_adapter_preserves_launch_rejection_status(monkeypatch, status, expected):
    from app.egx_scan import ScanBlocked, _verify

    def reject(*args, **kwargs):
        raise LaunchBlocked(status, 'reviewed admission reason')

    monkeypatch.setattr(swing_launch, 'run_signal', reject)
    with pytest.raises(ScanBlocked) as caught:
        _verify(None, '/unused', None)
    assert caught.value.status == expected
    assert str(caught.value) == 'reviewed admission reason'


def test_reviewed_daily_evidence_selects_exact_current_artifact(launch, capsys):
    db, root, source, _, path, provider, _ = launch

    # A second legitimately validated edition may coexist for the same
    # symbol/signal date. It must not replace or demote the original ledger row.
    alternate = EngineeringProvider(provider.records)
    alternate.name = 'fixture-alternate'

    refreshed = refresh_once(
        db,
        root,
        source,
        provider=alternate,
        cost=VerifiedQuotaCost(1, LABEL),
    )
    assert refreshed['operation'] == 'REFRESH_COMPLETED_SIGNAL_NOT_RUN'

    with db.connect() as con:
        current = con.execute(
            """
            SELECT a.artifact_id, a.provider, a.newest_market_date
            FROM daily_canonical_artifacts a
            WHERE a.canonical_symbol='COMI'
              AND a.status='VALIDATED'
              AND a.newest_market_date=?
            ORDER BY a.provider
            """,
            (source.signal_session.market_date.isoformat(),),
        ).fetchall()

    assert len(current) == 2
    assert {row['provider'] for row in current} == {
        'fixture',
        'fixture-alternate',
    }

    # The reviewed daily package was created from the original 'fixture'
    # ingestion. Verify must select that exact provenance rather than reject
    # merely because another validated edition exists.
    assert main(['verify', '--input', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'NOT_RUN'
    assert result['market_data'] == 'FRESH'


def test_refresh_pit_swing_planning_admission_publication_ui(launch, monkeypatch, capsys):
    db, root, source, directory, path, provider, at = launch
    assert len(provider.calls) == 1
    assert _decode(SwingLaunchInput, json.loads(path.read_text())) == source
    assert main(['verify', '--input', str(path)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified['market_data'] == 'FRESH' and verified['status'] == 'NOT_RUN'
    assert not directory.exists()
    assert main(['publish', '--input', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'PUBLISHED_PAPER_SIGNAL'
    state = load_shadow_watchlist(directory, read_shadow_input(directory))
    assert state['available'] and state['execution'] is None
    saved = json.loads((directory/'strategy-source.json').read_text())
    selection = saved['selections'][0]
    candidate, admission = selection['candidate'], selection['admission']
    assert candidate['strategy_id'] == 'SWING' and candidate['strategy_version'] == '1'
    assert json.loads(candidate['evidence_json'])['config'] == swing_launch.swing_config().model_dump()
    assert (candidate['validation_status'], candidate['execution_allowed']) == ('UNVALIDATED', False)
    assert admission['decision_status'] == 'WATCH' and admission['paper_quantity'] == 0
    assert admission['paper_risk_status'] == 'NOT_EVALUATED'
    assert admission['entry_low'] == '359.195' and admission['entry_high'] == '362.805'
    assert admission['stop'] == '350.17' and admission['targets'] == ['382.66']
    context = json.loads(admission['context'][0])
    assert context['trade_plan']['target_2'] is context['trade_plan']['target_3'] is None
    assert context['trade_plan']['valid_until'] == source.entry_session.closes_at.isoformat().replace('+00:00', 'Z')
    assert context['planning_rule']['maximum_holding_eligible_sessions'] == 10
    assert context['planning_rule']['session_end_at'] is None
    assert LABEL in (directory/'input.json').read_text()
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert main(['publish', '--input', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'EVIDENCE_BLOCKED'
    assert before == {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert main(['ui']) == 0
    assert json.loads(capsys.readouterr().out)['available']
    from app.main import shadow
    api = shadow()
    assert api['available'] and api['empirical_validation'] == 'NOT YET VALIDATED'
    assert api['freshness'] == 'NOT ESTABLISHED / FROZEN RECORD ONLY'
    assert len(provider.calls) == 1
    with db.connect() as con:
        assert con.execute('SELECT used_units FROM automatic_quota').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM signals').fetchone()[0] == 0


@pytest.mark.parametrize('damage,expected', [
    ('stale', 'DATA_STALE'), ('insufficient', 'DATA_INSUFFICIENT'),
    ('index_identity', 'EVIDENCE_BLOCKED'), ('unknown_identity', 'EVIDENCE_BLOCKED'),
    ('identity', 'EVIDENCE_BLOCKED'), ('session', 'EVIDENCE_BLOCKED'),
    ('daily_package', 'EVIDENCE_BLOCKED'), ('review', 'EVIDENCE_BLOCKED'),
    ('calendar_gap', 'EVIDENCE_BLOCKED'), ('tamper', 'EVIDENCE_BLOCKED'),
    ('late', 'EVIDENCE_BLOCKED'), ('preclose', 'EVIDENCE_BLOCKED'),
])
def test_rejections(launch, damage, expected, monkeypatch, capsys):
    db, root, source, directory, path, _, at = launch
    if damage in ('stale', 'insufficient', 'identity', 'review', 'index_identity', 'unknown_identity'):
        with db.connect() as con:
            if damage == 'stale':
                con.execute("UPDATE daily_canonical_artifacts SET newest_market_date='2000-01-01'")
            elif damage == 'insufficient':
                con.execute('DELETE FROM daily_canonical_sources')
            elif damage == 'identity':
                con.execute('DELETE FROM instrument_aliases')
            elif damage in ('index_identity', 'unknown_identity'):
                con.execute('UPDATE canonical_instruments SET instrument_type=?',
                            ('INDEX' if damage == 'index_identity' else 'UNKNOWN',))
            else:
                con.execute("UPDATE data_ingestions SET status='REJECTED' WHERE asset_type='SECURITY_MASTER'")
    elif damage == 'tamper':
        # The launch consumer must not read canonical artifact files directly.
        # Corrupt the trusted validation ledger instead and require fail-closed
        # disagreement with the independently materialized PIT rows.
        with db.connect() as con:
            con.execute(
                "UPDATE daily_canonical_artifacts SET sha256=?",
                ('0' * 64,),
            )
    elif damage == 'daily_package':
        source = source.model_copy(update={'daily_package': None})
    elif damage in ('session', 'calendar_gap'):
        source = source.model_copy(update={'sessions': source.sessions[:-1] if damage == 'session' else source.sessions[1:]})
    elif damage == 'late':
        monkeypatch.setattr(swing_launch, '_now', lambda: source.entry_session.opens_at)
    else:
        monkeypatch.setattr(swing_launch, '_now', lambda: source.signal_session.closes_at)
    path.write_text(source.model_dump_json())
    assert main(['publish', '--input', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == expected
    assert not directory.exists()


def test_missing_config_no_creation_no_provider(tmp_path, monkeypatch, capsys):
    for name in ('EGX_DB_PATH', 'EGX_DATA_ROOT', 'EGX_SHADOW_DIRECTORY', 'EODHD_API_TOKEN_FILE'):
        monkeypatch.delenv(name, raising=False)
    assert main(['publish']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'CONFIG_MISSING'
    assert main(['status']) == 2
    state = json.loads(capsys.readouterr().out)
    assert state['market_data'] == 'UNKNOWN'
    assert all(state['configuration'][k] == 'MISSING' for k in ('EGX_DB_PATH', 'EGX_DATA_ROOT'))
    assert not list(tmp_path.iterdir())


def test_explicit_provider_authorization_required(launch, capsys, monkeypatch):
    _, _, _, _, path, provider, _ = launch
    # Only a fake nonsecret file; production token contents are never inspected.
    token = path.parent/'engineering-token'
    token.write_text('ENGINEERING_FIXTURE_NOT_A_TOKEN')
    token.chmod(0o600)
    monkeypatch.setenv('EODHD_API_TOKEN_FILE', str(token))
    monkeypatch.setattr('tools.paper_shadow_launch._operator_token', lambda: pytest.fail('token must not be read'))
    assert configuration_status()['EODHD_API_TOKEN_FILE'] == 'SET'
    assert main(['refresh', '--input', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'CONFIG_MISSING'
    assert main(['refresh', '--input', str(path), '--authorize-provider-call']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'EVIDENCE_BLOCKED'
    assert len(provider.calls) == 1


def test_planning_decimal_context_and_no_float_shortcut(launch, monkeypatch):
    db, root, source, directory, _, _, _ = launch
    original = swing_launch.SwingEngine.evaluate
    def poisoned(self, **kwargs):
        return original(self, **kwargs).model_copy(update={'entry_reference': 999999.0})
    monkeypatch.setattr(swing_launch.SwingEngine, 'evaluate', poisoned)
    with localcontext() as context:
        context.prec = 4
        result = run_signal(db, root, source, directory, publish=True)
    assert result['status'] == 'PUBLISHED_PAPER_SIGNAL'
    saved = json.loads((directory/'strategy-source.json').read_text())
    assert saved['selections'][0]['admission']['entry_low'] == '359.195'
    with pytest.raises(ValueError, match='Decimal'):
        swing_launch.build_q03_plan(canonical_close=361.0, symbol='COMI', instrument_id=ID,
                                   decision_at=swing_launch._now(), entry_session=source.entry_session,
                                   source_audit_id='fixture')


def test_software_failure_is_not_ready(launch, monkeypatch, capsys):
    _, _, _, directory, path, _, _ = launch
    def fail(*args, **kwargs):
        raise RuntimeError('NEVER_PRINT_THIS_SECRET_SENTINEL')
    monkeypatch.setattr(swing_launch.SwingEngine, 'evaluate', fail)
    assert main(['publish', '--input', str(path)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'SOFTWARE_ERROR'
    assert 'SENTINEL' not in output and not directory.exists()


@pytest.mark.parametrize('path', ['/opt/egx-trading-platform/platform.db', '/opt/egx-runtime-secrets/token', '/var/run/docker.sock'])
def test_boundary_rejected_lexically(path):
    with pytest.raises(LaunchBlocked):
        local_path(path)


@pytest.mark.parametrize('launch', ['no_signal'], indirect=True)
def test_ready_no_signal_from_real_engine(launch, capsys):
    _, _, _, directory, path, provider, _ = launch
    assert main(['publish', '--input', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'READY_NO_SIGNAL' and result['market_data'] == 'FRESH'
    assert not directory.exists() and len(provider.calls) == 1


def test_short_history_refresh_rejected(launch):
    db, root, source, directory, _, provider, _ = launch
    short = EngineeringProvider(provider.records[-49:])
    from app.data.daily_refresh_job import DailyRefreshJobError
    from app.data.daily_refresh_admission import DailyRefreshAdmissionError
    with pytest.raises(DailyRefreshJobError) as error:
        refresh_once(db, root, source, provider=short, cost=VerifiedQuotaCost(1, LABEL))
    assert isinstance(error.value.__cause__, DailyRefreshAdmissionError)
    assert 'insufficient' in str(error.value.__cause__)
    assert not directory.exists()


def test_partial_publication_not_success(launch, monkeypatch, capsys):
    _, _, _, directory, path, _, _ = launch
    def fail(*args, **kwargs):
        raise RuntimeError('ENGINEERING_FIXTURE interrupted publication')
    monkeypatch.setattr(shadow_producer, 'append_candidate_event', fail)
    assert main(['publish', '--input', str(path)]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'SOFTWARE_ERROR'
    assert not (directory/'input.json').exists()
    assert main(['publish', '--input', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'EVIDENCE_BLOCKED'


@pytest.mark.parametrize('field,value', [
    ('entry_band', Decimal('0.01')), ('stop_fraction', Decimal('0.04')),
    ('target_fraction', Decimal('0.07')), ('maximum_holding_eligible_sessions', 11),
])
def test_q03_frozen_rule_rejects_alternatives(field, value):
    with pytest.raises(ValueError):
        swing_launch.Q03Rule(**{field: value})


def test_operator_reference_import_preserves_bytes_without_auto_review(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / 'engineering.db')
    db.initialize()
    root = tmp_path / 'engineering-data'
    root.mkdir()
    for key, value in [('EGX_DB_PATH', db.path), ('EGX_DATA_ROOT', root),
                       ('EGX_SHADOW_DIRECTORY', tmp_path / 'engineering-shadow')]:
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('EODHD_API_TOKEN_FILE', raising=False)
    document = dict(contract='egx-universe-v1', market='EGX', effective_date='2020-01-01',
                    published_at='2020-01-01T00:00:00Z', complete=True,
                    members=[dict(instrument_id=str(ID), symbol='COMI', eligible=True)])
    payload = json.dumps(document, indent=2).encode()
    path = tmp_path / 'ENGINEERING_FIXTURE_NOT_MARKET_EVIDENCE.json'
    path.write_bytes(payload)
    assert main(['import-reference', '--input', str(path), '--provider', 'engineering-fixture',
                 '--source-uri', 'fixture://NOT_A_REAL_SIGNAL']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'NOT_RUN'
    assert result['sha256'] == hashlib.sha256(payload).hexdigest()
    refs = ReferenceRepository(db, ImmutableRawStore(root / 'raw'))
    with db.connect() as con:
        row = con.execute('SELECT raw_path FROM data_ingestions WHERE ingestion_id=?',
                          (result['ingestion_id'],)).fetchone()
        assert con.execute("SELECT COUNT(*) FROM reference_artifacts WHERE contract='egx-source-review-v1'").fetchone()[0] == 0
    manifest = refs.ingestions.get_manifest_by_raw_path(row[0])
    assert refs.raw_store.read_verified(manifest) == payload
    with pytest.raises(ValueError, match='validation evidence unavailable'):
        refs.require_review(manifest, 'egx-universe-v1', as_of=datetime.now(timezone.utc))


@pytest.mark.parametrize('symbol', ['FIXTURE', 'OTHER.A', 'TEST_1', 'A-B'])
def test_broad_symbol_transport_keeps_identity_admission(launch, symbol):
    db, root, source, directory, _, _, _ = launch
    document = source.model_dump(mode='json') | {'symbol': symbol}
    expanded = _decode(SwingLaunchInput, document)
    assert expanded.symbol == symbol
    with pytest.raises(LaunchBlocked, match='security-master identity unavailable'):
        run_signal(db, root, expanded, directory, publish=False)
    assert not directory.exists()


@pytest.mark.parametrize('symbol', ['comi', ' COMI', 'COMI\n', 'A/B', '', 'A' * 65, 1])
def test_launch_symbol_spelling_is_strict(launch, symbol):
    _, _, source, _, _, _, _ = launch
    with pytest.raises(ValueError):
        _decode(SwingLaunchInput, source.model_dump(mode='json') | {'symbol': symbol})


def test_new_equity_refresh_blocks_without_provider_mapping(launch):
    db, root, source, directory, _, provider, _ = launch
    SecurityMasterRepository(db).replace_provider_snapshot(provider='fixture', instruments=[
        CanonicalInstrument(instrument_id=ID, instrument_type=InstrumentType.EQUITY,
                            canonical_ticker='FIXTURE', source_provider='fixture',
                            source_symbol_code='fixture-explicit-code', source_sha256='a'*64)])
    expanded = _decode(SwingLaunchInput, source.model_dump(mode='json') | {'symbol': 'FIXTURE'})
    calls_before = len(provider.calls)
    with pytest.raises(LaunchBlocked, match='reviewed provider refresh mapping unavailable'):
        refresh_once(db, root, expanded, provider=provider, cost=VerifiedQuotaCost(1, LABEL))
    assert len(provider.calls) == calls_before
    assert not directory.exists()
    with pytest.raises(LaunchBlocked, match='admitted daily history missing') as caught:
        run_signal(db, root, expanded, directory, publish=False)
    assert caught.value.status == 'DATA_INSUFFICIENT'


def test_explicit_refresh_mapping_rejects_wrong_alias_before_runtime(launch, monkeypatch):
    from app.data.daily_refresh_job import DailyRefreshTarget
    db, root, source, _, _, provider, _ = launch
    def forbidden_runtime(**kwargs):
        pytest.fail('unbound alias must not construct refresh runtime')
    monkeypatch.setattr(swing_launch, 'build_daily_refresh_runtime', forbidden_runtime)
    calls_before = len(provider.calls)
    with pytest.raises(LaunchBlocked, match='alias unavailable'):
        refresh_once(db, root, source, provider=provider, cost=VerifiedQuotaCost(1, LABEL),
                     target=DailyRefreshTarget('COMI', 'UNREGISTERED'))
    assert len(provider.calls) == calls_before


def test_explicit_refresh_mapping_accepts_registered_alias(launch):
    from app.data.daily_refresh_job import DailyRefreshTarget
    db, root, source, _, _, provider, _ = launch
    result = refresh_once(db, root, source, provider=provider, cost=VerifiedQuotaCost(1, LABEL),
                          target=DailyRefreshTarget('COMI', 'COMI.EGX'))
    assert result['operation'] == 'REFRESH_COMPLETED_SIGNAL_NOT_RUN'
    assert provider.calls[-1]['symbol'] == 'COMI.EGX'
