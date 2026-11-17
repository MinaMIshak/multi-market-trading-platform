"""Artificial retained calendar observations only; no admission or runtime evidence."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from app.decision_outcome_calendar import (
    CHAIN_REASONS, CONTRACT_VERSION, CalendarChainError, RetainedCalendarObservation,
    chain_egx_calendar_observations)
from app.domain.models import MarketSession

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
C_ROWS = [
    dict(market_date='2026-11-09', status='VERIFIED', opened_at='2026-11-09T10:00:00+02:00',
         closed_at='2026-11-09T14:30:00+02:00', data_verified_at='2026-11-09T15:00:00+02:00'),
    dict(market_date='2026-11-10', status='UNKNOWN', opened_at='2026-11-10T10:00:00+02:00',
         closed_at='2026-11-10T14:30:00+02:00'),
]
A_AT = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)
B_AT = datetime(2026, 11, 8, 16, tzinfo=timezone.utc)
C_AT = datetime(2026, 11, 9, 16, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 10, 6, tzinfo=timezone.utc)


def _artifact(rows):
    # Same serialization as market_sessions.payload_json (repository _json).
    return ('[' + ','.join(
        json.dumps(MarketSession.model_validate(row).model_dump(mode='json'), ensure_ascii=False,
                   sort_keys=True, separators=(',', ':'))
        for row in rows) + ']').encode()


def _observation(rows, observed_at, session=None):
    data = _artifact(rows)
    return RetainedCalendarObservation(
        artifact_sha256=sha256(data).hexdigest(),
        session=session or date.fromisoformat(rows[0]['market_date']),
        artifact=data, observed_at=observed_at)


A, B, C = _observation(A_ROWS, A_AT), _observation(B_ROWS, B_AT), _observation(C_ROWS, C_AT)


def _refused(observations, reason, as_of=AS_OF):
    with pytest.raises(CalendarChainError) as caught:
        chain_egx_calendar_observations(observations, as_of=as_of)
    assert caught.value.reason == reason and reason in CHAIN_REASONS
    return caught.value


def test_chain_rebuilds_contiguous_sessions_from_retained_observations():
    result = chain_egx_calendar_observations((A, B, C), as_of=AS_OF)
    sessions, report = result['sessions'], result['report']
    assert type(sessions) is tuple and all(type(s) is MarketSession for s in sessions)
    assert [s.market_date for s in sessions] == [date(2026, 11, d) for d in range(5, 11)]
    assert [s.status.value for s in sessions] == [
        'VERIFIED', 'WEEKEND', 'WEEKEND', 'VERIFIED', 'VERIFIED', 'UNKNOWN']
    # The UNKNOWN schedule rows are superseded by the later VERIFIED rows.
    assert sessions[3] == MarketSession.model_validate(B_ROWS[0])
    assert sessions[4] == MarketSession.model_validate(C_ROWS[0])
    assert sessions[-1] == MarketSession.model_validate(C_ROWS[-1])
    payload = '[' + ','.join(s.model_dump_json() for s in sessions) + ']'
    assert report == {
        'contract_version': CONTRACT_VERSION, 'market': 'EGX',
        'first_date': '2026-11-05', 'last_date': '2026-11-10', 'days': 6,
        'sessions_sha256': sha256(payload.encode('utf-8')).hexdigest(),
        'observations': [
            {'artifact_sha256': A.artifact_sha256, 'session': '2026-11-05',
             'next_session': '2026-11-08', 'observed_at': '2026-11-05T16:00:00Z'},
            {'artifact_sha256': B.artifact_sha256, 'session': '2026-11-08',
             'next_session': '2026-11-09', 'observed_at': '2026-11-08T16:00:00Z'},
            {'artifact_sha256': C.artifact_sha256, 'session': '2026-11-09',
             'next_session': '2026-11-10', 'observed_at': '2026-11-09T16:00:00Z'}],
        'latest_observed_at': '2026-11-09T16:00:00Z', 'as_of': '2026-11-10T06:00:00Z',
        'final_session': '2026-11-10', 'final_session_status': 'UNKNOWN',
        'schedule_revisions': [],
        'source': 'CALENDAR_OBSERVATIONS_RETAINED', 'retention': 'WRITE_ONCE_RETAINED',
        'admission': 'NOT_ESTABLISHED', 'outcome': 'NOT_EVALUATED',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED'}
    json.dumps(report, allow_nan=False)


def test_input_order_does_not_change_the_chain():
    assert (chain_egx_calendar_observations((C, A, B), as_of=AS_OF)
            == chain_egx_calendar_observations((A, B, C), as_of=AS_OF))


def test_single_observation_is_its_own_window():
    result = chain_egx_calendar_observations((A,), as_of=A_AT)
    assert result['sessions'] == tuple(MarketSession.model_validate(r) for r in A_ROWS)
    assert result['report']['final_session_status'] == 'UNKNOWN'
    assert result['report']['latest_observed_at'] == '2026-11-05T16:00:00Z'


def test_changed_schedule_is_listed_and_the_later_verified_row_is_used():
    early_close = [B_ROWS[0] | {'closed_at': '2026-11-08T13:30:00+02:00',
                                'data_verified_at': '2026-11-08T14:00:00+02:00'}, B_ROWS[1]]
    revised = _observation(early_close, B_AT)
    result = chain_egx_calendar_observations((A, revised, C), as_of=AS_OF)
    assert result['report']['schedule_revisions'] == ['2026-11-08']
    assert result['sessions'][3] == MarketSession.model_validate(early_close[0])


def test_missing_link_is_refused_never_skipped():
    error = _refused((A, C), 'CHAIN_NOT_CONTIGUOUS')
    assert error.detail == 'expected 2026-11-08, got 2026-11-09'


def test_two_observations_for_one_session_are_refused_not_preferred():
    later_schedule = _observation(A_ROWS[:-1] + [A_ROWS[-1] | {
        'closed_at': '2026-11-08T14:00:00+02:00'}], A_AT + timedelta(minutes=5))
    assert later_schedule.artifact_sha256 != A.artifact_sha256
    _refused((A, later_schedule, B), 'DUPLICATE_SESSION')
    _refused((A, A), 'DUPLICATE_SESSION')


def test_observation_after_the_clock_is_refused():
    _refused((A, B, C), 'OBSERVED_AFTER_CLOCK', as_of=C_AT - timedelta(seconds=1))
    # Exactly at the clock is available.
    assert chain_egx_calendar_observations((A, B, C), as_of=C_AT)['report']['days'] == 6


def test_tampered_bytes_and_wrong_session_are_rejected():
    tampered = replace(B, artifact=_artifact([B_ROWS[0], B_ROWS[1] | {
        'closed_at': '2026-11-09T15:00:00+02:00'}]))
    assert 'hash mismatch' in _refused((A, tampered), 'OBSERVATION_REJECTED').detail
    _refused((replace(B, session=date(2026, 11, 7)),), 'OBSERVATION_REJECTED')


@pytest.mark.parametrize('observed_at', [
    # At the next session's open: the observation could not be a pre-open schedule.
    datetime(2026, 11, 9, 8, tzinfo=timezone.utc),
    # Before the completed session was verified (15:00 Cairo = 13:00 UTC).
    datetime(2026, 11, 8, 12, tzinfo=timezone.utc),
])
def test_observation_invalid_at_its_own_time_is_rejected(observed_at):
    _refused((A, replace(B, observed_at=observed_at)), 'OBSERVATION_REJECTED')


def test_intervening_trading_day_without_status_is_rejected():
    bad = [A_ROWS[0], A_ROWS[1] | {'status': 'UNKNOWN'}, A_ROWS[2], A_ROWS[3]]
    _refused((_observation(bad, A_AT),), 'OBSERVATION_REJECTED', as_of=A_AT)


@pytest.mark.parametrize('observations,as_of', [
    ([A, B], AS_OF),
    ((), AS_OF),
    ((A, 'B'), AS_OF),
    ((A,), AS_OF.replace(tzinfo=None)),
    ((A,), '2026-11-10T06:00:00Z'),
    ((replace(A, observed_at=A_AT.replace(tzinfo=None)),), AS_OF),
    ((replace(A, session='2026-11-05'),), AS_OF),
    ((replace(A, artifact=bytearray(A.artifact)),), AS_OF),
])
def test_invalid_configuration_is_refused(observations, as_of):
    _refused(observations, 'CONFIG_INVALID', as_of=as_of)
