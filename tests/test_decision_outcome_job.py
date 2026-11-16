"""decision-outcome-job-v1: artificial retained inputs, disposable databases and directories only.

No scheduler, runtime producer, market coverage, fill, performance or edge
evidence is implied by these tests.
"""
from datetime import date, datetime, timedelta
import json
import re
from types import SimpleNamespace

import pytest

import app.decision_outcome_job as job
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_daily_evidence import DailyEvidenceLoadError
from app.decision_outcome_job import (
    CALENDAR_RETENTION, CALENDAR_SOURCE, CONTRACT_VERSION, EXIT_CODES, FAILURE_STAGES,
    JOB_REASONS, JOB_STATUSES, MAX_CALENDAR_DAYS, OutcomeJobError, _calendar_sha256, main,
    outcome_assumptions, run_egx_decision_outcome_job)
from app.decision_outcome_producer import (
    PRODUCTION_REASONS, DecisionOutcomeProductionError, EgxDailyEvidence,
    produce_egx_decision_outcomes)
from app.decision_outcome_store import load_retained_decision_outcomes
from app.domain.models import MarketSession
from app.scanner_v2_card_report_job import CardReportJobError
from app.storage import Database, TradingRepository
from tests.test_decision_outcome_producer import NOW, OTHER, decision, observe
from tests.test_decision_outcome_session_bars import (
    CAL, INSTRUMENT, RECEIVED, ROWS, ZERO, session)

FIRST, LAST = date(2026, 11, 4), date(2026, 11, 9)
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'corporate_actions': 'NOT_CHECKED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
SOURCES = [{'raw_path': 'fixture/COMI.EGX-D1.json'}]


@pytest.fixture
def host(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)
    db = Database(tmp_path / 'platform.db')
    db.initialize()
    trading = TradingRepository(db)
    for item in CAL:
        trading.save_market_session(item)
    observations, outcomes = tmp_path / 'observations', tmp_path / 'outcomes'
    observations.mkdir(mode=0o700)
    outcomes.mkdir(mode=0o700)
    return SimpleNamespace(db=db, trading=trading, raw_root=tmp_path / 'raw',
                           observations=observations, outcomes=outcomes)


@pytest.fixture
def fake_daily(monkeypatch):
    calls = []

    def fake(database, raw_root, raw_paths):
        calls.append((database, raw_root, raw_paths))
        return {'schema_version': 'decision-outcome-daily-evidence-v1',
                'daily': {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)},
                'sources': SOURCES, 'received_at_clock': 'INGESTION_MANIFEST_UNVERIFIED',
                'market_membership': 'NOT_ESTABLISHED', 'corporate_actions': 'NOT_CHECKED'}

    monkeypatch.setattr(job, 'load_egx_daily_evidence', fake)
    return calls


@pytest.fixture
def spy_producer(monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return produce_egx_decision_outcomes(*args, **kwargs)

    monkeypatch.setattr(job, 'produce_egx_decision_outcomes', spy)
    return calls


def kwargs(host, **overrides):
    values = dict(db_path=str(host.db.path), raw_root=str(host.raw_root),
                  observation_directory=str(host.observations),
                  outcome_directory=str(host.outcomes),
                  daily_raw_paths=('fixture/COMI.EGX-D1.json',),
                  calendar_from=FIRST, calendar_through=LAST, assumptions=ZERO, now=NOW)
    values.update(overrides)
    return values


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def test_job_wires_calendar_daily_evidence_and_producer(host, fake_daily, spy_producer):
    (identity, _), = observe(host.observations)
    out = run_egx_decision_outcome_job(**kwargs(host))
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-job-v1'
    assert out['status'] == 'PRODUCED' and out['status'] in JOB_STATUSES
    assert {k: out[k] for k in SAFETY} == SAFETY
    # The daily loader got exactly the named paths, the raw root and the database.
    [(database, raw_root, paths)] = fake_daily
    assert type(database) is Database and str(database.path) == str(host.db.path)
    assert raw_root == str(host.raw_root) and paths == ('fixture/COMI.EGX-D1.json',)
    # The producer got every calendar date read through the unchanged accessor.
    [call] = spy_producer
    assert type(call['sessions']) is tuple
    assert [s.model_dump() for s in call['sessions']] == [s.model_dump() for s in CAL]
    assert call['assumptions'] == ZERO and call['now'] == NOW
    assert call['daily'] == {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)}
    calendar = out['calendar']
    assert calendar == {'from': '2026-11-04', 'through': '2026-11-09', 'days': 6,
                        'sessions_sha256': _calendar_sha256(call['sessions']),
                        'source': CALENDAR_SOURCE, 'retention': CALENDAR_RETENTION}
    assert re.fullmatch(r'[0-9a-f]{64}', calendar['sessions_sha256'])
    assert (CALENDAR_SOURCE, CALENDAR_RETENTION) == (
        'MARKET_SESSIONS_CURRENT_ROWS', 'MUTABLE_NOT_RETAINED')
    assert out['daily_evidence'] == {
        'schema_version': 'decision-outcome-daily-evidence-v1', 'sources': SOURCES,
        'received_at_clock': 'INGESTION_MANIFEST_UNVERIFIED',
        'market_membership': 'NOT_ESTABLISHED'}
    production = out['production']
    assert production['schema_version'] == 'decision-outcome-production-v1'
    (entry,) = production['outcomes']
    assert entry['observation_identity'] == identity
    assert (entry['status'], entry['outcome_status']) == ('RETAINED', 'CLOSED')
    assert entry['sessions_bound'] == ['2026-11-05', '2026-11-08', '2026-11-09']
    held = load_retained_decision_outcomes(str(host.outcomes), market='EGX', assumptions=ZERO)
    assert len(held) == 1 and held[0]['observation_identity'] == identity
    json.dumps(out, allow_nan=False)


def test_calendar_digest_is_deterministic_and_changes_with_the_rows():
    assert _calendar_sha256(CAL) == _calendar_sha256(tuple(CAL))
    changed = CAL[:4] + (session(8, 'CLOSED'),) + CAL[5:]
    assert _calendar_sha256(changed) != _calendar_sha256(CAL)


def test_rerun_keeps_first_outcome_and_writes_nothing(host, fake_daily):
    observe(host.observations)
    run_egx_decision_outcome_job(**kwargs(host))
    before = snapshot(host.outcomes)
    again = run_egx_decision_outcome_job(**kwargs(host, now=NOW + timedelta(days=2)))
    assert again['status'] == 'PRODUCED'
    assert again['production']['status_counts']['ALREADY_RETAINED'] == 1
    assert snapshot(host.outcomes) == before


def test_waiting_observations_are_not_rejections(host, fake_daily):
    observe(host.observations, decision(), decision(symbol='OTHR', instrument=OTHER))
    out = run_egx_decision_outcome_job(**kwargs(host))
    counts = out['production']['status_counts']
    assert counts['RETAINED'] == 1 and counts['AWAITING_DAILY_EVIDENCE'] == 1
    assert out['status'] == 'PRODUCED'


def test_rejected_observation_makes_the_run_visible(host, fake_daily):
    observe(host.observations)
    host.trading.save_market_session(session(8, 'UNKNOWN'))
    out = run_egx_decision_outcome_job(**kwargs(host))
    assert out['status'] == 'REJECTIONS_PRESENT'
    assert out['production']['status_counts']['BINDING_REJECTED'] == 1
    assert snapshot(host.outcomes) == []


def test_missing_calendar_date_refuses_before_reading_daily_evidence(host, fake_daily):
    observe(host.observations)
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host, calendar_through=date(2026, 11, 10)))
    assert (info.value.reason, info.value.stage, info.value.detail) == (
        'CALENDAR_INCOMPLETE', 'CALENDAR', '2026-11-10')
    assert fake_daily == [] and snapshot(host.outcomes) == []


def test_mismatched_or_foreign_calendar_rows_are_rejected(host, fake_daily):
    observe(host.observations)
    with host.db.connect() as connection:
        connection.execute('UPDATE market_sessions SET payload_json = ? WHERE market_date = ?',
                           (session(7, 'WEEKEND').model_dump_json(), '2026-11-06'))
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host))
    assert (info.value.reason, info.value.stage) == ('CALENDAR_ROW_REJECTED', 'CALENDAR')
    host.trading.save_market_session(session(6, 'WEEKEND'))
    host.trading.save_market_session(
        MarketSession(market_date=date(2026, 11, 7), status='WEEKEND', timezone='UTC'))
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host))
    assert (info.value.reason, info.value.detail) == ('CALENDAR_ROW_REJECTED', '2026-11-07')
    with host.db.connect() as connection:
        connection.execute('UPDATE market_sessions SET payload_json = ? WHERE market_date = ?',
                           ('{"not": "a session"}', '2026-11-07'))
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host))
    assert info.value.reason == 'CALENDAR_ROW_REJECTED'
    assert fake_daily == [] and snapshot(host.outcomes) == []


@pytest.mark.parametrize('field,value', [
    ('db_path', 'relative.db'), ('raw_root', 'raw'), ('observation_directory', 'observations'),
    ('outcome_directory', None), ('outcome_directory', '/x\x00y'),
    ('daily_raw_paths', ()), ('daily_raw_paths', ['fixture/COMI.EGX-D1.json']),
    ('daily_raw_paths', None), ('calendar_from', '2026-11-04'),
    ('calendar_through', datetime(2026, 11, 9)), ('calendar_from', date(2026, 11, 10)),
    ('calendar_from', LAST - timedelta(days=MAX_CALENDAR_DAYS)), ('assumptions', None),
    ('assumptions', OutcomeAssumptions(
        assumptions_version='fixture-zero', market='US', entry_slippage_bps=ZERO.entry_slippage_bps,
        exit_slippage_bps=ZERO.exit_slippage_bps, cost_bps_per_side=ZERO.cost_bps_per_side)),
])
def test_invalid_configuration_reads_and_writes_nothing(host, fake_daily, field, value):
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host, **{field: value}))
    assert (info.value.reason, info.value.stage) == ('CONFIG_INVALID', 'INPUT')
    assert info.value.reason in JOB_REASONS
    assert fake_daily == [] and snapshot(host.outcomes) == []


def test_longest_allowed_calendar_range_is_accepted_by_configuration(host, fake_daily):
    # Configuration accepts MAX_CALENDAR_DAYS dates; the fixture database lacks
    # most of them, so the calendar stage refuses rather than skipping gaps.
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(
            host, calendar_from=LAST - timedelta(days=MAX_CALENDAR_DAYS - 1)))
    assert (info.value.reason, info.value.stage) == ('CALENDAR_INCOMPLETE', 'CALENDAR')


def test_explicit_assumptions_are_exact_and_never_defaulted():
    built = outcome_assumptions(assumptions_version='egx-declared-1', entry_slippage_bps='10',
                                exit_slippage_bps='12.5', cost_bps_per_side='15')
    assert type(built) is OutcomeAssumptions and built.market == 'EGX'
    assert str(built.exit_slippage_bps) == '12.5' and built.basis == 'DECLARED_UNVALIDATED'
    for bad in (dict(entry_slippage_bps='NaN'), dict(exit_slippage_bps='abc'),
                dict(cost_bps_per_side='-1'), dict(entry_slippage_bps=None),
                dict(assumptions_version=''), dict(assumptions_version='has space')):
        values = dict(assumptions_version='egx-declared-1', entry_slippage_bps='0',
                      exit_slippage_bps='0', cost_bps_per_side='0')
        values.update(bad)
        with pytest.raises(OutcomeJobError) as info:
            outcome_assumptions(**values)
        assert info.value.reason == 'CONFIG_INVALID'


def test_missing_database_is_never_created(host, fake_daily, tmp_path):
    missing = tmp_path / 'absent' / 'platform.db'
    with pytest.raises(CardReportJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host, db_path=str(missing)))
    assert info.value.reason == 'DATABASE_UNAVAILABLE'
    assert not missing.exists() and not missing.parent.exists()
    assert fake_daily == [] and snapshot(host.outcomes) == []


def test_real_daily_loader_refuses_unknown_raw_path(host):
    observe(host.observations)
    with pytest.raises(DailyEvidenceLoadError) as info:
        run_egx_decision_outcome_job(**kwargs(host))
    assert info.value.reason == 'INGESTION_UNAVAILABLE'
    assert snapshot(host.outcomes) == []


def test_production_refusal_propagates(host, fake_daily):
    with pytest.raises(DecisionOutcomeProductionError) as info:
        run_egx_decision_outcome_job(**kwargs(host, outcome_directory=str(host.observations)))
    assert info.value.reason == 'CONFIG_INVALID' and info.value.reason in PRODUCTION_REASONS
    assert snapshot(host.outcomes) == []


def _cli(host, **overrides):
    values = dict(db_path=str(host.db.path), raw_root=str(host.raw_root),
                  observations=str(host.observations), outcomes=str(host.outcomes),
                  first='2026-11-04', last='2026-11-09', entry='0', exit='0', cost='0')
    values.update(overrides)
    return ['--db-path', values['db_path'], '--raw-root', values['raw_root'],
            '--observation-dir', values['observations'], '--outcome-dir', values['outcomes'],
            '--daily-raw-path', 'fixture/COMI.EGX-D1.json',
            '--calendar-from', values['first'], '--calendar-through', values['last'],
            '--assumptions-version', 'fixture-zero', '--entry-slippage-bps', values['entry'],
            '--exit-slippage-bps', values['exit'], '--cost-bps-per-side', values['cost']]


def _clock(monkeypatch, now):
    # Always wrap the unchanged producer, so repeated calls replace the clock.
    monkeypatch.setattr(job, 'produce_egx_decision_outcomes',
                        lambda *a, **k: produce_egx_decision_outcomes(*a, **{**k, 'now': now}))


def test_cli_success_rejection_and_failure_exit_codes(host, fake_daily, capsys, monkeypatch):
    observe(host.observations)
    _clock(monkeypatch, NOW)
    host.trading.save_market_session(session(8, 'UNKNOWN'))
    assert main(_cli(host)) == EXIT_CODES['REJECTIONS_PRESENT'] == 2
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'REJECTIONS_PRESENT' and out['live_money'] is False
    assert snapshot(host.outcomes) == []
    host.trading.save_market_session(CAL[4])
    assert main(_cli(host)) == EXIT_CODES['PRODUCED'] == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'PRODUCED' and out['production']['status_counts']['RETAINED'] == 1
    # The CLI builds exactly the fixture assumptions from explicit strings.
    assert out['production']['assumptions'] == ZERO.model_dump(mode='json')
    assert set(EXIT_CODES) == set(JOB_STATUSES)


def test_cli_reports_fixed_failure_stage(host, fake_daily, capsys, monkeypatch):
    cases = (
        (dict(entry='NaN'), ('INPUT', 'CONFIG_INVALID')),
        (dict(first='04/11/2026'), ('INPUT', 'CONFIG_INVALID')),
        (dict(db_path='relative.db'), ('INPUT', 'CONFIG_INVALID')),
        (dict(last='2026-11-10'), ('CALENDAR', 'CALENDAR_INCOMPLETE')),
        (dict(outcomes=str(host.observations)), ('PRODUCTION', 'CONFIG_INVALID')),
    )
    for change, (stage, reason) in cases:
        assert main(_cli(host, **change)) == EXIT_CODES['FAILED'] == 1
        out = json.loads(capsys.readouterr().out)
        assert (out['status'], out['stage'], out['reason']) == ('FAILED', stage, reason), change
        assert {k: out[k] for k in SAFETY} == SAFETY
    monkeypatch.undo()
    assert main(_cli(host)) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('DAILY_EVIDENCE', 'INGESTION_UNAVAILABLE')
    assert set(FAILURE_STAGES) == {'INPUT', 'CALENDAR', 'DAILY_EVIDENCE', 'PRODUCTION'}
    assert snapshot(host.outcomes) == []
