"""Artificial disposable databases only; no schedule admission or runtime evidence."""
from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

import app.storage.repository as repository_module
from app.domain import MarketSession
from app.storage import Database, TradingRepository

SESSION = date(2026, 11, 5)
THROUGH = date(2026, 11, 8)
AT = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
NEXT_OPEN = datetime(2026, 11, 8, 8, tzinfo=timezone.utc)


def rows():
    return [
        dict(market_date='2026-11-05', status='VERIFIED', opened_at='2026-11-05T10:00:00+02:00',
             closed_at='2026-11-05T14:30:00+02:00', data_verified_at='2026-11-05T15:00:00+02:00'),
        dict(market_date='2026-11-06', status='WEEKEND'),
        dict(market_date='2026-11-07', status='WEEKEND'),
        dict(market_date='2026-11-08', status='UNKNOWN', opened_at='2026-11-08T10:00:00+02:00',
             closed_at='2026-11-08T14:30:00+02:00'),
    ]


@pytest.fixture
def clock(monkeypatch):
    now = [AT]
    monkeypatch.setattr(repository_module, '_utc_now', lambda: now[0].isoformat())
    return now


@pytest.fixture
def repo(tmp_path, clock):
    database = Database(tmp_path / 'platform.db')
    database.initialize()
    repository = TradingRepository(database)
    for row in rows():
        repository.save_market_session(MarketSession.model_validate(row))
    return repository


def count(repo):
    with repo.database.connect() as connection:
        return connection.execute('SELECT COUNT(*) FROM calendar_observations').fetchone()[0]


def test_retains_exact_committed_window_without_admission(repo):
    result = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert result.session == SESSION and result.next_session == THROUGH
    assert result.observed_at == AT
    assert result.next_session_open == NEXT_OPEN
    assert result.admission == 'NOT_ESTABLISHED' and result.live_money is False
    assert result.evidence_id == f'egx-calendar-sha256:{result.artifact_sha256}'
    assert repo.get_calendar_observation(artifact_sha256=result.artifact_sha256,
                                         decision_time=AT) == result
    assert count(repo) == 1


def test_exact_retry_preserves_first_observation_time(repo, clock):
    first = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    clock[0] = AT + timedelta(hours=3)
    again = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert again.artifact_sha256 == first.artifact_sha256
    assert again.observed_at == first.observed_at == AT
    assert count(repo) == 1


def test_later_session_mutation_does_not_rewrite_retained_history(repo, clock):
    first = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    changed = rows()[-1] | {'closed_at': '2026-11-08T15:00:00+02:00'}
    repo.save_market_session(MarketSession.model_validate(changed))
    clock[0] = AT + timedelta(hours=1)
    second = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert second.artifact_sha256 != first.artifact_sha256
    assert second.observed_at == AT + timedelta(hours=1)
    retained = repo.get_calendar_observation(artifact_sha256=first.artifact_sha256,
                                             decision_time=clock[0])
    assert retained == first
    assert retained.valid_until != second.valid_until
    assert count(repo) == 2


def test_incomplete_window_is_rejected_without_retention(repo):
    with repo.database.connect() as connection:
        connection.execute("DELETE FROM market_sessions WHERE market_date = '2026-11-06'")
    with pytest.raises(ValueError, match='incomplete calendar window'):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert count(repo) == 0


@pytest.mark.parametrize('session,through', [
    (SESSION, SESSION),
    (SESSION, SESSION - timedelta(days=1)),
    (SESSION, SESSION + timedelta(days=32)),
    ('2026-11-05', THROUGH),
    (datetime(2026, 11, 5, tzinfo=timezone.utc), THROUGH),
])
def test_bounded_date_arguments_required(repo, session, through):
    with pytest.raises(ValueError, match='bounded calendar dates required'):
        repo.retain_calendar_observation(session=session, through=through)
    assert count(repo) == 0


@pytest.mark.parametrize('now', [NEXT_OPEN, NEXT_OPEN + timedelta(minutes=1),
                                 datetime(2026, 11, 5, 12, tzinfo=timezone.utc)])
def test_observation_at_or_after_next_open_or_before_verification_is_rejected(repo, clock, now):
    clock[0] = now
    with pytest.raises(ValueError):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert count(repo) == 0


def test_structurally_invalid_committed_window_is_not_retained(repo):
    repo.save_market_session(MarketSession.model_validate(rows()[1] | {'status': 'UNKNOWN'}))
    with pytest.raises(ValueError):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert count(repo) == 0


def test_lookup_rejects_unknown_digest_and_future_decision(repo):
    result = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    with pytest.raises(KeyError):
        repo.get_calendar_observation(artifact_sha256='0' * 64, decision_time=AT)
    with pytest.raises(ValueError, match='future calendar decision time'):
        repo.get_calendar_observation(artifact_sha256=result.artifact_sha256,
                                      decision_time=AT + timedelta(seconds=1))
    with pytest.raises(ValueError):
        repo.get_calendar_observation(artifact_sha256=result.artifact_sha256,
                                      decision_time=AT.replace(tzinfo=None))
    with pytest.raises(ValueError):
        repo.get_calendar_observation(artifact_sha256=result.artifact_sha256,
                                      decision_time=AT - timedelta(seconds=1))


@pytest.mark.parametrize('statement', [
    "UPDATE calendar_observations SET observed_at = '2026-11-05T00:00:00+00:00'",
    'DELETE FROM calendar_observations',
    "INSERT INTO calendar_observations SELECT artifact_sha256, session, artifact, observed_at "
    'FROM calendar_observations',
])
def test_retained_observations_are_immutable(repo, statement):
    repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    with pytest.raises(sqlite3.IntegrityError):
        with repo.database.connect() as connection:
            connection.execute(statement)
    assert count(repo) == 1


def test_corrupted_retained_bytes_fail_closed(repo):
    result = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    with repo.database.connect() as connection:
        connection.execute('DROP TRIGGER calendar_observations_no_update')
        connection.execute("UPDATE calendar_observations SET artifact = CAST('[]' AS BLOB)")
    with pytest.raises(ValueError, match='hash mismatch'):
        repo.get_calendar_observation(artifact_sha256=result.artifact_sha256, decision_time=AT)
    with pytest.raises(ValueError, match='integrity mismatch'):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)


def test_explicit_upgrade_from_v11_adds_table_without_backfill(repo):
    with repo.database.connect() as connection:
        connection.execute('DROP TABLE calendar_observations')
        connection.execute("UPDATE schema_meta SET value = '11' WHERE key = 'schema_version'")
    with pytest.raises(RuntimeError, match='upgrade required'):
        repo.database.initialize()
    repo.database.initialize(allow_upgrade=True)
    assert repo.database.schema_version() == 12
    assert count(repo) == 0
    assert repo.get_market_session(SESSION).status.value == 'VERIFIED'


def test_row_identity_mismatch_is_rejected_before_retention(repo):
    with repo.database.connect() as connection:
        connection.execute("UPDATE market_sessions SET status = 'HOLIDAY' WHERE market_date = '2026-11-06'")
    with pytest.raises(ValueError, match='calendar row identity mismatch'):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert count(repo) == 0


def test_corrupted_retained_session_fails_closed(repo):
    result = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    with repo.database.connect() as connection:
        connection.execute('DROP TRIGGER calendar_observations_no_update')
        connection.execute("UPDATE calendar_observations SET session = '2026-11-04'")
    with pytest.raises(ValueError, match='noncontiguous calendar'):
        repo.get_calendar_observation(artifact_sha256=result.artifact_sha256, decision_time=AT)
    with pytest.raises(ValueError):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)


def test_retry_after_next_open_leaves_retained_observation_unchanged(repo, clock):
    first = repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    clock[0] = NEXT_OPEN
    with pytest.raises(ValueError, match='already opened'):
        repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    assert count(repo) == 1
    assert repo.get_calendar_observation(artifact_sha256=first.artifact_sha256,
                                         decision_time=AT) == first


@pytest.mark.parametrize('statement', [
    'UPDATE calendar_observations SET session = session',
    'DELETE FROM calendar_observations',
    'INSERT INTO calendar_observations SELECT * FROM calendar_observations',
])
def test_upgrade_installs_immutability_guards(repo, statement):
    with repo.database.connect() as connection:
        connection.execute('DROP TABLE calendar_observations')
        connection.execute("UPDATE schema_meta SET value = '11' WHERE key = 'schema_version'")
    repo.database.initialize(allow_upgrade=True)
    repo.retain_calendar_observation(session=SESSION, through=THROUGH)
    with pytest.raises(sqlite3.IntegrityError, match='immutable calendar observation'):
        with repo.database.connect() as connection:
            connection.execute(statement)
    assert count(repo) == 1
