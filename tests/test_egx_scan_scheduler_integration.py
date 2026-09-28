"""Real policy/SQLite integration; scan itself is a classification fixture."""
from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import patch

from app.core import MarketSessionOrchestrator
from app.core.schedule import CalendarTruth, CheckpointName, ScheduledCheckpoint
from app.storage import Database
from app.storage.scheduler_repository import SchedulerRepository
from app.egx_scan_dispatch import dispatch_scan
from app.egx_scan import scan_egx_scope


def test_scan_claim_is_durable_and_calendar_gated(tmp_path):
    database = Database(tmp_path / 'platform.db')
    database.initialize()
    repository = SchedulerRepository(database)
    orchestrator = MarketSessionOrchestrator()
    orchestrator.policy.checkpoints.append(ScheduledCheckpoint(
        name=CheckpointName.EGX_SCAN_PRIMARY, at=time(18, 30),
        max_lateness_minutes=15, requires_verified_trading_day=True))
    now = datetime(2026, 9, 24, 18, 30, tzinfo=ZoneInfo('Africa/Cairo'))
    config = SimpleNamespace(symbols=('FIXTURE',), sources={}, source_errors={}, scope_reference='fixture')
    with patch('app.egx_scan_dispatch.load_scan_configuration', return_value=config), patch(
            'app.egx_scan_dispatch.scan_egx_scope',
            wraps=scan_egx_scope) as scan:
        for truth in (CalendarTruth.UNVERIFIED, CalendarTruth.VERIFIED_TRADING_DAY):
            evaluation = orchestrator.evaluate(now=now, market_date=now.date(),
                                               calendar_truth=truth, completed_jobs=set())
            repository.sync_evaluation(evaluation)
            result = dispatch_scan(evaluation=evaluation, repository=repository,
                                   database=database, data_root=tmp_path,
                                   config_path=tmp_path / 'config.json',
                                   history_path=tmp_path / 'history.json')
            if truth == CalendarTruth.UNVERIFIED:
                assert result is None
                scan.assert_not_called()
            else:
                assert result['succeeded'] is True
        # A fresh repository instance observes persisted success and cannot rerun.
        assert dispatch_scan(evaluation=evaluation, repository=SchedulerRepository(database),
                             database=database, data_root=tmp_path,
                             config_path=tmp_path / 'config.json',
                             history_path=tmp_path / 'history.json') is None
        scan.assert_called_once()


def test_security_master_scope_classifies_full_universe_without_evidence(tmp_path):
    """Opt-in universe scope over a real security master and ledger: every
    stored equity is requested, none is verified without launch evidence."""
    import json
    from uuid import uuid4
    from app.data.security_master import CanonicalInstrument, InstrumentType
    from app.storage.security_master_repository import SecurityMasterRepository

    database = Database(tmp_path / 'platform.db')
    database.initialize()
    SecurityMasterRepository(database).replace_provider_snapshot(provider='fixture', instruments=[
        CanonicalInstrument(instrument_id=uuid4(), instrument_type=kind, canonical_ticker=ticker,
                            source_provider='fixture', source_symbol_code=ticker + '.EGX',
                            source_sha256='a' * 64)
        for ticker, kind in (('FWRY', InstrumentType.EQUITY), ('COMI', InstrumentType.EQUITY),
                             ('EGX30', InstrumentType.INDEX))])
    repository = SchedulerRepository(database)
    orchestrator = MarketSessionOrchestrator()
    orchestrator.policy.checkpoints.append(ScheduledCheckpoint(
        name=CheckpointName.EGX_SCAN_PRIMARY, at=time(18, 30),
        max_lateness_minutes=15, requires_verified_trading_day=True))
    now = datetime(2026, 9, 24, 18, 30, tzinfo=ZoneInfo('Africa/Cairo'))
    evaluation = orchestrator.evaluate(now=now, market_date=now.date(),
                                       calendar_truth=CalendarTruth.VERIFIED_TRADING_DAY,
                                       completed_jobs=set())
    repository.sync_evaluation(evaluation)
    history = tmp_path / 'history.json'
    with patch('app.egx_scan._verify') as verify:
        result = dispatch_scan(evaluation=evaluation, repository=repository,
                               database=database, data_root=tmp_path, config_path=None,
                               history_path=history, scope='security_master')
    verify.assert_not_called()
    assert result == dict(checkpoint='EGX_SCAN_PRIMARY', succeeded=True, requested=2, scanned=0)
    written = json.loads(history.read_text())
    assert written['scope_reference'] == 'security-master-equity-universe'
    assert [t['symbol'] for t in written['symbols']] == ['COMI', 'FWRY']
    assert {t['status'] for t in written['symbols']} == {'EVIDENCE_BLOCKED'}
