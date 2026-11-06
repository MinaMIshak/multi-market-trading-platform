"""Artificial retained calendars; no source admission or runtime evidence."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json

import pytest

from app.scanner_v2_calendar import project_egx_calendar

AT = datetime(2026, 11, 5, 16, tzinfo=timezone.utc)


def rows():
    return [
        dict(market_date='2026-11-05', status='VERIFIED', opened_at='2026-11-05T10:00:00+02:00',
             closed_at='2026-11-05T14:30:00+02:00', data_verified_at='2026-11-05T15:00:00+02:00'),
        dict(market_date='2026-11-06', status='WEEKEND'),
        dict(market_date='2026-11-07', status='WEEKEND'),
        dict(market_date='2026-11-08', status='UNKNOWN', opened_at='2026-11-08T10:00:00+02:00',
             closed_at='2026-11-08T14:30:00+02:00'),
    ]


def project(records=None, payload=None, **kwargs):
    payload = json.dumps(rows() if records is None else records).encode() if payload is None else payload
    args = dict(expected_sha256=sha256(payload).hexdigest(), session=date(2026, 11, 5),
                observed_at=AT, decision_time=AT)
    args.update(kwargs)
    return project_egx_calendar(payload, **args)


def test_deterministic_immutable_window_without_admission():
    result = project()
    assert result == project()
    assert result.next_session == date(2026, 11, 8)
    assert result.admission == 'NOT_ESTABLISHED'
    assert result.live_money is False
    assert result.artifact_sha256 in result.evidence_id
    assert json.loads(result.model_dump_json())['market'] == 'EGX'
    with pytest.raises(ValueError):
        result.live_money = True


@pytest.mark.parametrize('index,changes', [
    (0, {'status': 'UNKNOWN'}), (0, {'data_verified_at': None}),
    (0, {'data_verified_at': '2026-11-06T15:00:00+02:00'}),
    (0, {'data_verified_at': '2026-11-05T13:00:00+02:00'}),
    (0, {'timezone': 'America/New_York'}), (0, {'market_date': '2026-11-04'}),
    (0, {'opened_at': None}), (0, {'closed_at': '2026-11-05T09:00:00+02:00'}),
    (0, {'closed_at': '2026-11-06T14:00:00+02:00'}),
    (0, {'first15_closed_at': '2026-11-05T15:00:00+02:00'}),
    (1, {'status': 'UNKNOWN'}), (1, {'opened_at': '2026-11-06T10:00:00+02:00'}),
    (1, {'data_verified_at': '2026-11-06T10:00:00+02:00'}),
    (2, {'market_date': '2026-11-06'}), (3, {'status': 'VERIFIED'}),
    (3, {'data_verified_at': '2026-11-08T15:00:00+02:00'}),
    (3, {'status': 'HOLIDAY'}), (3, {'opened_at': '2026-11-08T10:00:00'}),
    (3, {'live_money': True}),
])
def test_rejects_inconsistent_or_future_calendar(index, changes):
    records = rows()
    records[index].update(changes)
    with pytest.raises(ValueError):
        project(records)


@pytest.mark.parametrize('kwargs', [
    {'expected_sha256': '0' * 64}, {'session': '2026-11-05'},
    {'observed_at': AT.replace(tzinfo=None)},
    {'observed_at': datetime(2026, 11, 6, tzinfo=timezone.utc)},
    {'decision_time': datetime(2026, 11, 8, 8, tzinfo=timezone.utc)},
])
def test_integrity_and_observation_cutoff(kwargs):
    with pytest.raises(ValueError):
        project(**kwargs)


@pytest.mark.parametrize('payload', [b'{}', b'[]', b'[{}]', b' ' * (128 * 1024 + 1),
    b'[{"status":"UNKNOWN","status":"VERIFIED"}]', b'[NaN]', b'[1e999]'])
def test_bounded_strict_json(payload):
    with pytest.raises(ValueError):
        project(payload=payload)


def test_missing_intervening_day_and_extra_future_day_rejected():
    records = rows()
    del records[1]
    with pytest.raises(ValueError):
        project(records)
    records = rows()
    records.append(dict(market_date='2026-11-09', status='UNKNOWN'))
    with pytest.raises(ValueError):
        project(records)


def test_weekday_cannot_be_weekend_and_holiday_does_not_invent_admission():
    records = rows()
    records[-1] = dict(market_date='2026-11-08', status='WEEKEND')
    records.append(dict(market_date='2026-11-09', status='UNKNOWN',
                        opened_at='2026-11-09T10:00:00+02:00', closed_at='2026-11-09T14:30:00+02:00'))
    with pytest.raises(ValueError):
        project(records)
    records[-2]['status'] = 'HOLIDAY'
    assert project(records).admission == 'NOT_ESTABLISHED'
