"""Artificial disposable databases only; no calendar admission or runtime evidence."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import sqlite3

import pytest

import app.storage.repository as repository_module
from app.decision_outcome_calendar import (
    MAX_OBSERVATIONS, RetainedCalendarObservation, chain_egx_calendar_observations)
from app.decision_outcome_calendar_loader import (
    CONTRACT_VERSION, LOAD_REASONS, MAX_ARTIFACT_BYTES, CalendarObservationLoadError,
    load_egx_calendar_observations)
from app.domain import MarketSession
from app.storage import Database, TradingRepository

A_ROWS = [
    dict(market_date='2026-11-05', status='VERIFIED', opened_at='2026-11-05T10:00:00+02:00',
         closed_at='2026-11-05T14:30:00+02:00', data_verified_at='2026-11-05T15:00:00+02:00'),
    dict(market_date='2026-11-06', status='WEEKEND'),
    dict(market_date='2026-11-07', status='WEEKEND'),
    dict(market_date='2026-11-08', status='UNKNOWN', opened_at='2026-11-08T10:00:00+02:00',
         closed_at='2026-11-08T14:30:00+02:00'),
]
B_ROWS = [
    dict(market_date='2026-11-08', status='VERIFIED', opened_at='2026-11-08T10:00:00+02:00',
         closed_at='2026-11-08T14:30:00+02:00', data_verified_at='2026-11-08T15:00:00+02:00'),
    dict(market_date='2026-11-09', status='UNKNOWN', opened_at='2026-11-09T10:00:00+02:00',
         closed_at='2026-11-09T14:30:00+02:00'),
]
A_AT = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
B_AT = datetime(2026, 11, 8, 16, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 9, 6, tzinfo=timezone.utc)


def _artifact(rows):
    # Same serialization as market_sessions.payload_json (repository _json).
    return ('[' + ','.join(
        json.dumps(MarketSession.model_validate(row).model_dump(mode='json'), ensure_ascii=False,
                   sort_keys=True, separators=(',', ':'))
        for row in rows) + ']').encode()


@pytest.fixture
def clock(monkeypatch):
    now = [A_AT]
    monkeypatch.setattr(repository_module, '_utc_now', lambda: now[0].isoformat())
    return now


@pytest.fixture
def retained(tmp_path, clock):
    """Two observations retained by the unchanged repository path: (repo, a, b)."""
    database = Database(tmp_path / 'platform.db')
    database.initialize()
    repo = TradingRepository(database)
    for row in A_ROWS:
        repo.save_market_session(MarketSession.model_validate(row))
    a = repo.retain_calendar_observation(session=date(2026, 11, 5), through=date(2026, 11, 8))
    clock[0] = B_AT
    for row in B_ROWS:
        repo.save_market_session(MarketSession.model_validate(row))
    b = repo.retain_calendar_observation(session=date(2026, 11, 8), through=date(2026, 11, 9))
    return repo, a.artifact_sha256, b.artifact_sha256


def _refused(database, digests, reason):
    with pytest.raises(CalendarObservationLoadError) as caught:
        load_egx_calendar_observations(database, digests)
    assert caught.value.reason == reason and reason in LOAD_REASONS
    return caught.value


def _tamper(repo, statement, *args):
    with repo.database.connect() as connection:
        connection.execute('DROP TRIGGER calendar_observations_no_update')
        connection.execute(statement, args)


def test_loads_named_rows_in_caller_order_for_the_unchanged_chain(retained):
    repo, a, b = retained
    result = load_egx_calendar_observations(repo.database, (b, a))
    observations = result['observations']
    assert observations == (
        RetainedCalendarObservation(artifact_sha256=b, session=date(2026, 11, 8),
                                    artifact=_artifact(B_ROWS), observed_at=B_AT),
        RetainedCalendarObservation(artifact_sha256=a, session=date(2026, 11, 5),
                                    artifact=_artifact(A_ROWS), observed_at=A_AT))
    assert sha256(_artifact(A_ROWS)).hexdigest() == a
    assert result == {
        'schema_version': CONTRACT_VERSION, 'market': 'EGX', 'observations': observations,
        'sources': [
            {'artifact_sha256': b, 'session': '2026-11-08',
             'observed_at': B_AT.isoformat(), 'artifact_bytes': len(_artifact(B_ROWS))},
            {'artifact_sha256': a, 'session': '2026-11-05',
             'observed_at': A_AT.isoformat(), 'artifact_bytes': len(_artifact(A_ROWS))}],
        'selection': 'CALLER_NAMED_DIGESTS', 'source': 'CALENDAR_OBSERVATIONS_RETAINED',
        'retention': 'WRITE_ONCE_RETAINED', 'observed_at_clock': 'REPOSITORY_CLOCK_UNVERIFIED',
        'calendar_validation': 'NOT_PERFORMED_BY_LOADER', 'admission': 'NOT_ESTABLISHED',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED'}
    chained = chain_egx_calendar_observations(observations, as_of=AS_OF)
    assert [s.market_date for s in chained['sessions']] == [
        date(2026, 11, d) for d in range(5, 10)]
    assert [s.status.value for s in chained['sessions']] == [
        'VERIFIED', 'WEEKEND', 'WEEKEND', 'VERIFIED', 'UNKNOWN']
    assert chained['report']['observations'][0]['artifact_sha256'] == a


def test_later_market_sessions_mutation_does_not_change_loaded_history(retained):
    repo, a, b = retained
    before = load_egx_calendar_observations(repo.database, (a, b))
    # Mutable rows are upserted; the loader never reads them.
    repo.save_market_session(MarketSession.model_validate(
        B_ROWS[1] | {'closed_at': '2026-11-09T13:30:00+02:00'}))
    with repo.database.connect() as connection:
        connection.execute("DELETE FROM market_sessions WHERE market_date = '2026-11-06'")
    assert load_egx_calendar_observations(repo.database, (a, b)) == before


def test_loading_writes_nothing(retained):
    repo, a, b = retained

    def snapshot():
        with repo.database.connect() as connection:
            return [tuple(row) for table in ('calendar_observations', 'market_sessions')
                    for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]

    before = snapshot()
    load_egx_calendar_observations(repo.database, (a, b))
    assert snapshot() == before


def test_unknown_digest_is_unavailable_and_nothing_is_returned(retained):
    repo, a, _ = retained
    error = _refused(repo.database, (a, '0' * 64), 'OBSERVATION_UNAVAILABLE')
    assert error.detail.startswith('0' * 64)


def test_tampered_bytes_are_rejected(retained):
    repo, a, b = retained
    _tamper(repo, 'UPDATE calendar_observations SET artifact = ? WHERE artifact_sha256 = ?',
            _artifact(B_ROWS[:1] + [B_ROWS[1] | {'closed_at': '2026-11-09T15:00:00+02:00'}]), b)
    assert 'hash mismatch' in _refused(repo.database, (a, b), 'OBSERVATION_REJECTED').detail


def test_text_artifact_is_rejected(retained):
    repo, a, _ = retained
    _tamper(repo, 'UPDATE calendar_observations SET artifact = CAST(artifact AS TEXT) '
                  'WHERE artifact_sha256 = ?', a)
    assert 'BLOB' in _refused(repo.database, (a,), 'OBSERVATION_REJECTED').detail


def test_oversized_artifact_is_rejected_before_hashing(retained):
    repo, a, _ = retained
    _tamper(repo, 'UPDATE calendar_observations SET artifact = ? WHERE artifact_sha256 = ?',
            b'[' + b' ' * MAX_ARTIFACT_BYTES + b']', a)
    assert 'size' in _refused(repo.database, (a,), 'OBSERVATION_REJECTED').detail


@pytest.mark.parametrize('observed_at', [
    '2026-11-05T16:00:00',          # naive
    'not-a-time',
])
def test_naive_or_unparsable_observation_time_is_rejected(retained, observed_at):
    repo, a, _ = retained
    _tamper(repo, 'UPDATE calendar_observations SET observed_at = ? WHERE artifact_sha256 = ?',
            observed_at, a)
    assert 'observed_at' in _refused(repo.database, (a,), 'OBSERVATION_REJECTED').detail


@pytest.mark.parametrize('session', ['20261105', '2026-11-5', 'garbage'])
def test_inexact_session_date_is_rejected(retained, session):
    repo, a, _ = retained
    _tamper(repo, 'UPDATE calendar_observations SET session = ? WHERE artifact_sha256 = ?',
            session, a)
    assert 'session' in _refused(repo.database, (a,), 'OBSERVATION_REJECTED').detail


def test_missing_database_file_is_refused_and_not_created(tmp_path):
    path = tmp_path / 'absent' / 'platform.db'
    _refused(Database(path), ('0' * 64,), 'DATABASE_UNAVAILABLE')
    assert not path.exists() and not path.parent.exists()


def test_missing_table_is_database_unavailable(tmp_path):
    path = tmp_path / 'uninitialized.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE unrelated (x INTEGER)')
    connection.commit()
    connection.close()
    assert path.is_file()
    _refused(Database(path), ('0' * 64,), 'DATABASE_UNAVAILABLE')


@pytest.mark.parametrize('digests', [
    ['0' * 64],
    (),
    ('0' * 63,),
    ('A' * 64,),
    ('0' * 64 + '\n',),
    (b'0' * 64,),
    ('0' * 64, '0' * 64),
    tuple(f'{i:064x}' for i in range(MAX_OBSERVATIONS + 1)),
])
def test_invalid_configuration_is_refused(retained, digests):
    repo, _, _ = retained
    _refused(repo.database, digests, 'CONFIG_INVALID')
