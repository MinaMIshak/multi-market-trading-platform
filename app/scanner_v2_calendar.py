"""Offline retained-calendar projection, not schedule admission or authority.

The trusted caller supplies snapshot bytes, their expected digest and the time
those exact bytes were observed. No mutable repository or weekly extrapolation
can establish a historical next-session schedule. Admission remains a separate
gate even when all structural and temporal checks pass.
"""
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from app.core.base_trading_calendar import BaseTradingCalendarPolicy
from app.domain.models import MarketSession
from app.domain.enums import MarketSessionStatus as Status
from app.scanner_v2_egx_rank import CAIRO, _unique_object, _invalid_constant
from app.strategies.contracts import Contract, aware


class CalendarProjection(Contract):
    schema_version: Literal['scanner-v2-egx-calendar-v1'] = 'scanner-v2-egx-calendar-v1'
    market: Literal['EGX'] = 'EGX'
    session: date
    next_session: date
    data_as_of: datetime
    next_session_open: datetime
    valid_until: datetime
    observed_at: datetime
    artifact_sha256: str
    evidence_id: str
    admission: Literal['NOT_ESTABLISHED'] = 'NOT_ESTABLISHED'
    live_money: Literal[False] = False


def project_egx_calendar(artifact, *, expected_sha256, session, observed_at, decision_time):
    """Project 2–32 contiguous MarketSession rows ending at the next open day.

JSON is an array of existing MarketSession serializations, not a new calendar
store. Future UNKNOWN rows can carry a schedule but cannot prove admission.
All intervening days need explicit non-trading status; weekends are checked
against the existing EGX policy. No freshness interval is invented here.
    """
    if type(artifact) is not bytes or len(artifact) > 128 * 1024:
        raise ValueError('bounded calendar bytes required')
    if type(expected_sha256) is not str or sha256(artifact).hexdigest() != expected_sha256:
        raise ValueError('calendar artifact hash mismatch')
    if type(session) is not date:
        raise ValueError('session date required')
    if aware(observed_at) > aware(decision_time):
        raise ValueError('calendar unavailable at decision')
    rows = json.loads(artifact, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    json.dumps(rows, allow_nan=False)
    if type(rows) is not list or not 2 <= len(rows) <= 32:
        raise ValueError('bounded contiguous calendar window required')
    sessions = []
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) - set(MarketSession.model_fields):
            raise ValueError('invalid calendar row')
        item = MarketSession.model_validate(row)
        if item.timezone != 'Africa/Cairo' or item.market_date != session + timedelta(days=index):
            raise ValueError('wrong market or noncontiguous calendar')
        for stamp in (item.opened_at, item.first15_closed_at, item.closed_at, item.data_verified_at):
            if stamp is not None:
                aware(stamp)
        if item.data_verified_at is not None and item.data_verified_at > observed_at:
            raise ValueError('verification unavailable at observation')
        sessions.append(item)
    previous, upcoming = sessions[0], sessions[-1]
    policy = BaseTradingCalendarPolicy()
    for item in sessions[1:-1]:
        if item.status not in (Status.WEEKEND, Status.HOLIDAY):
            raise ValueError('intervening day not explicitly nontrading')
        if item.status == Status.WEEKEND and policy.classify(item.market_date) != Status.WEEKEND:
            raise ValueError('invalid EGX weekend')
        if any(t is not None for t in (item.opened_at, item.first15_closed_at, item.closed_at)):
            raise ValueError('nontrading day contains trading hours')
    for item in (previous, upcoming):
        if policy.classify(item.market_date) == Status.WEEKEND:
            raise ValueError('EGX weekend cannot be trading window')
        if item.opened_at is None or item.closed_at is None:
            raise ValueError('explicit session hours required')
        if not item.opened_at < item.closed_at or any(
                t.astimezone(CAIRO).date() != item.market_date
                for t in (item.opened_at, item.closed_at)):
            raise ValueError('invalid session hours')
        if item.first15_closed_at is not None and not item.opened_at < item.first15_closed_at < item.closed_at:
            raise ValueError('invalid first-bar window')
    if (previous.status != Status.VERIFIED or previous.data_verified_at is None
            or not previous.closed_at <= previous.data_verified_at <= observed_at):
        raise ValueError('completed session unavailable at observation')
    if upcoming.status != Status.UNKNOWN or upcoming.data_verified_at is not None:
        raise ValueError('future schedule must not assert completed-session verification')
    if not observed_at <= decision_time < upcoming.opened_at:
        raise ValueError('next-session entry already opened')
    return CalendarProjection(session=session, next_session=upcoming.market_date,
        data_as_of=previous.closed_at, next_session_open=upcoming.opened_at,
        valid_until=upcoming.closed_at, observed_at=observed_at,
        artifact_sha256=expected_sha256, evidence_id=f'egx-calendar-sha256:{expected_sha256}')
