"""decision-outcome-job-v2: artificial retained inputs, disposable databases and directories only.

No scheduler, runtime producer, calendar admission, market coverage, fill,
performance or edge evidence is implied by these tests.
"""
from datetime import datetime, timedelta, timezone
import json
import re
from types import SimpleNamespace

import pytest

import app.decision_outcome_job as job
import app.storage.repository as repository_module
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_calendar import (
    CHAIN_REASONS, MAX_OBSERVATIONS, CalendarChainError, chain_egx_calendar_observations)
from app.decision_outcome_calendar_loader import (
    LOAD_REASONS, CalendarObservationLoadError, load_egx_calendar_observations)
from app.decision_outcome_daily_evidence import DailyEvidenceLoadError
from app.decision_outcome_job import (
    CALENDAR_RETENTION, CALENDAR_SOURCE, CONTRACT_VERSION, EXIT_CODES, FAILURE_STAGES,
    JOB_REASONS, JOB_STATUSES, OutcomeJobError, main, outcome_assumptions,
    run_egx_decision_outcome_job)
from app.decision_outcome_producer import (
    PRODUCTION_REASONS, DecisionOutcomeProductionError, EgxDailyEvidence,
    produce_egx_decision_outcomes)
from app.decision_outcome_store import load_retained_decision_outcomes
from app.scanner_v2_card_report_job import CardReportJobError
from app.storage import Database, TradingRepository
from tests.test_decision_outcome_producer import NOW, OTHER, decision, observe
from tests.test_decision_outcome_session_bars import (
    INSTRUMENT, RECEIVED, ROWS, ZERO, session)

UTC = timezone.utc
# Repository clock at each retention: after the completed session was verified
# (13:00 UTC) and before the next session opened (08:00 UTC next trading day).
A_AT = datetime(2026, 11, 5, 16, tzinfo=UTC)
B_AT = datetime(2026, 11, 8, 16, tzinfo=UTC)
C_AT = datetime(2026, 11, 9, 13, 30, tzinfo=UTC)   # before RECEIVED and NOW
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'corporate_actions': 'NOT_CHECKED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
SOURCES = [{'raw_path': 'fixture/COMI.EGX-D1.json'}]


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Three calendar observations retained by the unchanged repository path."""
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)
    clock = [A_AT]
    monkeypatch.setattr(repository_module, '_utc_now', lambda: clock[0].isoformat())
    db = Database(tmp_path / 'platform.db')
    db.initialize()
    trading = TradingRepository(db)

    def retain(at, rows, first, through):
        clock[0] = at
        for item in rows:
            trading.save_market_session(item)
        return trading.retain_calendar_observation(
            session=first.market_date, through=through.market_date).artifact_sha256

    a_rows = (session(5), session(6, 'WEEKEND'), session(7, 'WEEKEND'), session(8, 'UNKNOWN'))
    a = retain(A_AT, a_rows, a_rows[0], a_rows[-1])
    b = retain(B_AT, (session(8), session(9, 'UNKNOWN')), session(8), session(9))
    c = retain(C_AT, (session(9), session(10, 'UNKNOWN')), session(9), session(10))
    observations, outcomes = tmp_path / 'observations', tmp_path / 'outcomes'
    observations.mkdir(mode=0o700)
    outcomes.mkdir(mode=0o700)
    return SimpleNamespace(db=db, trading=trading, raw_root=tmp_path / 'raw',
                           observations=observations, outcomes=outcomes, digests=(a, b, c))


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
                  calendar_observations=host.digests, assumptions=ZERO, now=NOW)
    values.update(overrides)
    return values


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def calendar_rows(host):
    with host.db.connect() as connection:
        return [tuple(row) for table in ('calendar_observations', 'market_sessions')
                for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]


def test_job_wires_retained_calendar_daily_evidence_and_producer(host, fake_daily, spy_producer):
    (identity, _), = observe(host.observations)
    before = calendar_rows(host)
    out = run_egx_decision_outcome_job(**kwargs(host))
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-job-v2'
    assert out['status'] == 'PRODUCED' and out['status'] in JOB_STATUSES
    assert {k: out[k] for k in SAFETY} == SAFETY
    # The daily loader got exactly the named paths, the raw root and the database.
    [(database, raw_root, paths)] = fake_daily
    assert type(database) is Database and str(database.path) == str(host.db.path)
    assert raw_root == str(host.raw_root) and paths == ('fixture/COMI.EGX-D1.json',)
    # The producer got exactly the unchanged chain of the named retained
    # observations at the job clock, and the same clock.
    expected = chain_egx_calendar_observations(
        load_egx_calendar_observations(host.db, host.digests)['observations'], as_of=NOW)
    [call] = spy_producer
    assert type(call['sessions']) is tuple and call['sessions'] == expected['sessions']
    assert [s.market_date.day for s in call['sessions']] == [5, 6, 7, 8, 9, 10]
    assert [s.status.value for s in call['sessions']] == [
        'VERIFIED', 'WEEKEND', 'WEEKEND', 'VERIFIED', 'VERIFIED', 'UNKNOWN']
    assert call['assumptions'] == ZERO and call['now'] == NOW
    assert call['daily'] == {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)}
    calendar = out['calendar']
    a, b, c = host.digests
    assert calendar['chain'] == expected['report']
    assert calendar['chain']['as_of'] == '2026-11-09T15:00:00Z'
    assert [o['artifact_sha256'] for o in calendar['chain']['observations']] == [a, b, c]
    assert re.fullmatch(r'[0-9a-f]{64}', calendar['chain']['sessions_sha256'])
    assert [s['artifact_sha256'] for s in calendar['sources']] == [a, b, c]
    assert {k: v for k, v in calendar.items() if k not in ('chain', 'sources')} == {
        'observations_schema_version': 'decision-outcome-calendar-observations-v1',
        'selection': 'CALLER_NAMED_DIGESTS', 'observed_at_clock': 'REPOSITORY_CLOCK_UNVERIFIED',
        'source': CALENDAR_SOURCE, 'retention': CALENDAR_RETENTION,
        'admission': 'NOT_ESTABLISHED'}
    assert (CALENDAR_SOURCE, CALENDAR_RETENTION) == (
        'CALENDAR_OBSERVATIONS_RETAINED', 'WRITE_ONCE_RETAINED')
    assert out['daily_evidence'] == {
        'schema_version': 'decision-outcome-daily-evidence-v1', 'sources': SOURCES,
        'received_at_clock': 'INGESTION_MANIFEST_UNVERIFIED',
        'market_membership': 'NOT_ESTABLISHED'}
    production = out['production']
    assert production['schema_version'] == 'decision-outcome-production-v1'
    (entry,) = production['outcomes']
    assert entry['observation_identity'] == identity
    assert (entry['status'], entry['outcome_status']) == ('RETAINED', 'CLOSED')
    # The producer's receipt-date window trims the pending 2026-11-10 row.
    assert entry['sessions_bound'] == ['2026-11-05', '2026-11-08', '2026-11-09']
    held = load_retained_decision_outcomes(str(host.outcomes), market='EGX', assumptions=ZERO)
    assert len(held) == 1 and held[0]['observation_identity'] == identity
    # The job writes no database row (calendar tables unchanged).
    assert calendar_rows(host) == before
    json.dumps(out, allow_nan=False)


def test_named_order_does_not_change_the_calendar(host, fake_daily):
    observe(host.observations)
    a, b, c = host.digests
    first = run_egx_decision_outcome_job(**kwargs(host))['calendar']['chain']
    assert run_egx_decision_outcome_job(
        **kwargs(host, calendar_observations=(c, a, b)))['calendar']['chain'] == first


def test_later_market_sessions_mutation_cannot_change_the_outcome_calendar(host, fake_daily):
    # v1 read these upserted rows; v2 reads only the named retained observations.
    observe(host.observations)
    host.trading.save_market_session(session(8, 'UNKNOWN'))
    with host.db.connect() as connection:
        connection.execute("DELETE FROM market_sessions WHERE market_date = '2026-11-06'")
    out = run_egx_decision_outcome_job(**kwargs(host))
    assert out['status'] == 'PRODUCED'
    assert out['production']['status_counts']['RETAINED'] == 1


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
    # Without the 2026-11-09 observation the chain ends on an UNKNOWN Monday,
    # although the daily bytes were received after its close: binding refuses.
    observe(host.observations)
    a, b, _ = host.digests
    out = run_egx_decision_outcome_job(**kwargs(host, calendar_observations=(a, b)))
    assert out['calendar']['chain']['final_session_status'] == 'UNKNOWN'
    assert out['status'] == 'REJECTIONS_PRESENT'
    assert out['production']['status_counts']['BINDING_REJECTED'] == 1
    assert snapshot(host.outcomes) == []


def _calendar_refused(host, fake_daily, error_type, reasons, **overrides):
    with pytest.raises(error_type) as info:
        run_egx_decision_outcome_job(**kwargs(host, **overrides))
    assert info.value.reason in reasons
    # Refused before any daily evidence is read or outcome written.
    assert fake_daily == [] and snapshot(host.outcomes) == []
    return info.value


def test_unknown_calendar_observation_refuses_before_reading_daily_evidence(host, fake_daily):
    observe(host.observations)
    error = _calendar_refused(host, fake_daily, CalendarObservationLoadError, LOAD_REASONS,
                              calendar_observations=host.digests + ('0' * 64,))
    assert error.reason == 'OBSERVATION_UNAVAILABLE'


def test_missing_link_in_named_observations_is_refused_never_skipped(host, fake_daily):
    observe(host.observations)
    a, _, c = host.digests
    error = _calendar_refused(host, fake_daily, CalendarChainError, CHAIN_REASONS,
                              calendar_observations=(a, c))
    assert (error.reason, error.detail) == (
        'CHAIN_NOT_CONTIGUOUS', 'expected 2026-11-08, got 2026-11-09')


def test_calendar_observed_after_the_job_clock_is_refused(host, fake_daily):
    observe(host.observations)
    error = _calendar_refused(host, fake_daily, CalendarChainError, CHAIN_REASONS,
                              now=C_AT - timedelta(seconds=1))
    assert (error.reason, error.detail) == ('OBSERVED_AFTER_CLOCK', '2026-11-09')


def test_tampered_retained_calendar_is_refused(host, fake_daily):
    observe(host.observations)
    _, b, _ = host.digests
    with host.db.connect() as connection:
        connection.execute('DROP TRIGGER calendar_observations_no_update')
        connection.execute(
            "UPDATE calendar_observations SET artifact = CAST('[]' AS BLOB) "
            'WHERE artifact_sha256 = ?', (b,))
    error = _calendar_refused(host, fake_daily, CalendarObservationLoadError, LOAD_REASONS)
    assert error.reason == 'OBSERVATION_REJECTED'


@pytest.mark.parametrize('field,value', [
    ('db_path', 'relative.db'), ('raw_root', 'raw'), ('observation_directory', 'observations'),
    ('outcome_directory', None), ('outcome_directory', '/x\x00y'),
    ('daily_raw_paths', ()), ('daily_raw_paths', ['fixture/COMI.EGX-D1.json']),
    ('daily_raw_paths', None),
    ('calendar_observations', ()), ('calendar_observations', None),
    ('calendar_observations', ['0' * 64]), ('calendar_observations', ('A' * 64,)),
    ('calendar_observations', ('0' * 63,)), ('calendar_observations', (b'0' * 64,)),
    ('calendar_observations', ('0' * 64, '0' * 64)),
    ('calendar_observations', tuple(f'{i:064x}' for i in range(MAX_OBSERVATIONS + 1))),
    ('assumptions', None),
    ('assumptions', OutcomeAssumptions(
        assumptions_version='fixture-zero', market='US', entry_slippage_bps=ZERO.entry_slippage_bps,
        exit_slippage_bps=ZERO.exit_slippage_bps, cost_bps_per_side=ZERO.cost_bps_per_side)),
    ('now', NOW.replace(tzinfo=None)), ('now', '2026-11-09T15:00:00Z'),
])
def test_invalid_configuration_reads_and_writes_nothing(host, fake_daily, field, value):
    with pytest.raises(OutcomeJobError) as info:
        run_egx_decision_outcome_job(**kwargs(host, **{field: value}))
    assert (info.value.reason, info.value.stage) == ('CONFIG_INVALID', 'INPUT')
    assert info.value.reason in JOB_REASONS
    assert fake_daily == [] and snapshot(host.outcomes) == []


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


def _cli(host, digests=None, **overrides):
    values = dict(db_path=str(host.db.path), raw_root=str(host.raw_root),
                  observations=str(host.observations), outcomes=str(host.outcomes),
                  entry='0', exit='0', cost='0')
    values.update(overrides)
    argv = ['--db-path', values['db_path'], '--raw-root', values['raw_root'],
            '--observation-dir', values['observations'], '--outcome-dir', values['outcomes'],
            '--daily-raw-path', 'fixture/COMI.EGX-D1.json',
            '--assumptions-version', 'fixture-zero', '--entry-slippage-bps', values['entry'],
            '--exit-slippage-bps', values['exit'], '--cost-bps-per-side', values['cost']]
    for digest in host.digests if digests is None else digests:
        argv += ['--calendar-observation', digest]
    return argv


def _clock(monkeypatch, now):
    # One job clock for both the calendar chain and the producer.
    monkeypatch.setattr(job, '_utc_now', lambda: now)


def test_cli_success_rejection_and_failure_exit_codes(host, fake_daily, capsys, monkeypatch):
    observe(host.observations)
    _clock(monkeypatch, NOW)
    a, b, _ = host.digests
    assert main(_cli(host, digests=(a, b))) == EXIT_CODES['REJECTIONS_PRESENT'] == 2
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'REJECTIONS_PRESENT' and out['live_money'] is False
    assert snapshot(host.outcomes) == []
    assert main(_cli(host)) == EXIT_CODES['PRODUCED'] == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'PRODUCED' and out['production']['status_counts']['RETAINED'] == 1
    assert out['calendar']['chain']['as_of'] == '2026-11-09T15:00:00Z'
    assert out['production']['produced_at'] == '2026-11-09T15:00:00Z'
    # The CLI builds exactly the fixture assumptions from explicit strings.
    assert out['production']['assumptions'] == ZERO.model_dump(mode='json')
    assert set(EXIT_CODES) == set(JOB_STATUSES)


def test_cli_reports_fixed_failure_stage(host, fake_daily, capsys, monkeypatch):
    _clock(monkeypatch, NOW)
    a, _, c = host.digests
    cases = (
        (dict(entry='NaN'), None, ('INPUT', 'CONFIG_INVALID')),
        (dict(db_path='relative.db'), None, ('INPUT', 'CONFIG_INVALID')),
        ({}, ('A' * 64,), ('INPUT', 'CONFIG_INVALID')),
        ({}, (a, '0' * 64), ('CALENDAR', 'OBSERVATION_UNAVAILABLE')),
        ({}, (a, c), ('CALENDAR', 'CHAIN_NOT_CONTIGUOUS')),
        (dict(outcomes=str(host.observations)), None, ('PRODUCTION', 'CONFIG_INVALID')),
    )
    for change, digests, (stage, reason) in cases:
        assert main(_cli(host, digests=digests, **change)) == EXIT_CODES['FAILED'] == 1
        out = json.loads(capsys.readouterr().out)
        assert (out['status'], out['stage'], out['reason']) == ('FAILED', stage, reason), change
        assert out['schema_version'] == CONTRACT_VERSION
        assert {k: out[k] for k in SAFETY} == SAFETY
    # Restore the real daily loader (undo also restores the clocks; the
    # calendar observations are already retained).
    monkeypatch.undo()
    _clock(monkeypatch, NOW)
    assert main(_cli(host)) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('DAILY_EVIDENCE', 'INGESTION_UNAVAILABLE')
    assert set(FAILURE_STAGES) == {'INPUT', 'CALENDAR', 'DAILY_EVIDENCE', 'PRODUCTION'}
    assert snapshot(host.outcomes) == []
