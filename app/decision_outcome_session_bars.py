"""Bind retained EGX daily bars and sessions into outcome session bars.

Phase G/H groundwork (decision-outcome-session-bars-v1). The outcome evaluator
``evaluate_decision_outcome`` accepts caller-supplied ``PaperReplayBar`` tuples
and a caller-asserted ``path_complete_through_at``. This pure function is the
first binding of those inputs to retained evidence rather than to an arbitrary
caller list. It composes existing contracts only:

- ``CanonicalDailyBar`` rows of ONE canonical daily artifact (one instrument,
  provider, snapshot and raw source digest) together with the time those exact
  daily bytes were received (the ingestion manifest ``received_at``);
- a contiguous window of retained ``MarketSession`` rows (Africa/Cairo), the
  same serialization ``project_egx_calendar`` reads;
- one retained decision-observation-v1 record, re-read from its exact bytes.

It returns the bars and the completeness time for the unchanged evaluator plus
a JSON-ready binding report. Nothing is evaluated, written or admitted here.

Completeness (fail closed with ValueError): the path is complete through
``complete`` = the later of the daily-bytes receipt and every in-path session's
verification time. The calendar must start on or before the detection date and
end on the Cairo date of ``complete``, so no trading day can be silently absent.
Every session opening at or after detection is in the path and must be either
explicitly non-trading (WEEKEND per the EGX policy, or HOLIDAY; no hours, no
bar), VERIFIED and closed by ``complete`` (exactly one VALID_EXECUTABLE bar,
received after the session close), or, on the final day only, not yet closed at
``complete`` (no bar). Any other state, a missing or quarantined bar, a bar on a
non-session day, a bar after the calendar, or a duplicate date is rejected; a
gap is never skipped, because skipping a session would silently change holding
counts and stop/target ordering. Rows dated before detection are look-back
history and are ignored.

Availability rule AVAILABLE_AT_PATH_COMPLETION_V1: every bound bar is labelled
available at ``complete`` (never earlier than its session close or the receipt),
so no bar can be used before the whole path was known. Prices are the canonical
VALID_EXECUTABLE OHLC (provider_adjusted_close_reference is never used).
Corporate actions are NOT checked and calendar admission stays NOT_ESTABLISHED.
The receipt time is caller-asserted. No IO, store, route, scheduler or caller.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import datetime, timedelta, timezone

from app.core.base_trading_calendar import BaseTradingCalendarPolicy
from app.data.daily_canonical import CanonicalDailyBar, DailyBarSemanticClass
from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time, read_retained_decision_observation
from app.decision_outcome import MAX_BARS
from app.domain.enums import MarketSessionStatus as Status
from app.domain.models import MarketSession
from app.paper.replay_models import PaperReplayBar
from app.scanner_v2_egx_rank import CAIRO

CONTRACT_VERSION = 'decision-outcome-session-bars-v1'
VENUE_ID = 'XCAI'
MAX_CALENDAR_DAYS = 2 * MAX_BARS
MAX_DAILY_ROWS = 4000
AVAILABILITY_RULE = 'AVAILABLE_AT_PATH_COMPLETION_V1'
NONTRADING = (Status.WEEKEND, Status.HOLIDAY)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'timezone-aware {name} required')
    return value.astimezone(timezone.utc)


def _rows(rows, record):
    if type(rows) is not tuple or not 0 < len(rows) <= MAX_DAILY_ROWS or any(
            type(row) is not CanonicalDailyBar for row in rows):
        raise ValueError(f'nonempty tuple of at most {MAX_DAILY_ROWS} exact CanonicalDailyBar required')
    # Revalidate: model_copy/model_construct bypass checks.
    rows = tuple(CanonicalDailyBar.model_validate(row.model_dump(mode='python')) for row in rows)
    if len({(r.instrument_id, r.canonical_symbol, r.source_provider, r.source_snapshot_date,
             r.source_sha256) for r in rows}) != 1:
        raise ValueError('daily rows must come from one canonical artifact')
    first = rows[0]
    if (str(first.instrument_id), first.canonical_symbol) != (record['instrument_id'], record['symbol']):
        raise ValueError('daily rows instrument or symbol mismatch')
    by_date = {}
    for row in rows:
        if row.market_date in by_date:
            raise ValueError('duplicate daily market_date')
        by_date[row.market_date] = row
    return first, by_date


def _sessions(sessions):
    if type(sessions) is not tuple or not 0 < len(sessions) <= MAX_CALENDAR_DAYS or any(
            type(item) is not MarketSession for item in sessions):
        raise ValueError(f'nonempty tuple of at most {MAX_CALENDAR_DAYS} exact MarketSession required')
    sessions = tuple(MarketSession.model_validate(item.model_dump(mode='python')) for item in sessions)
    for index, item in enumerate(sessions):
        if item.timezone != 'Africa/Cairo':
            raise ValueError('EGX Africa/Cairo sessions required')
        if index and item.market_date != sessions[index - 1].market_date + timedelta(days=1):
            raise ValueError('calendar window must be contiguous')
    return sessions


def _hours(item):
    if item.opened_at is None or item.closed_at is None:
        return None
    opened, closed = _aware(item.opened_at, 'opened_at'), _aware(item.closed_at, 'closed_at')
    if not opened < closed or any(t.astimezone(CAIRO).date() != item.market_date
                                  for t in (opened, closed)):
        raise ValueError('invalid session hours')
    return opened, closed


def bind_egx_outcome_session_bars(identity, record, rows, sessions, *, received_at) -> dict:
    """Return bars, completeness time and a binding report for one observation; pure."""
    if type(record) is not dict:
        raise ValueError('decision-observation-v1 record dict required')
    try:
        payload = canonical_bytes(record)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('record is not canonical JSON data') from exc
    record = read_retained_decision_observation(payload, expected_sha256=identity)
    if record['market'] != 'EGX':
        raise ValueError('EGX observation required')
    detected = _time(record['detected_at'], 'detected_at').astimezone(timezone.utc)
    received = _aware(received_at, 'received_at')
    if received < detected:
        raise ValueError('daily evidence received before detection')
    source, by_date = _rows(rows, record)
    if source.source_snapshot_date > received.astimezone(CAIRO).date():
        raise ValueError('daily snapshot dated after receipt')
    sessions = _sessions(sessions)
    if sessions[0].market_date > detected.astimezone(CAIRO).date():
        raise ValueError('calendar starts after detection')

    hours = {item.market_date: _hours(item) for item in sessions}
    in_path = [item for item in sessions
               if not (hours[item.market_date] and hours[item.market_date][0] < detected)
               and item.market_date >= detected.astimezone(CAIRO).date()]
    complete = received
    for item in in_path:
        if item.status == Status.VERIFIED and item.data_verified_at is not None:
            complete = max(complete, _aware(item.data_verified_at, 'data_verified_at'))
    final_date = complete.astimezone(CAIRO).date()
    if sessions[-1].market_date != final_date:
        raise ValueError('calendar must end on the path-completion date')

    policy, bars, nontrading, pending = BaseTradingCalendarPolicy(), [], [], None
    for item in in_path:
        day, span, row = item.market_date, hours[item.market_date], by_date.get(item.market_date)
        if item.status in NONTRADING:
            if any(t is not None for t in (item.opened_at, item.first15_closed_at, item.closed_at)):
                raise ValueError('nontrading day contains trading hours')
            if item.status == Status.WEEKEND and policy.classify(day) != Status.WEEKEND:
                raise ValueError('invalid EGX weekend')
            if row is not None:
                raise ValueError('daily bar on nontrading day')
            nontrading.append(day.isoformat())
            continue
        if (item.status != Status.VERIFIED and span is not None and span[1] > complete
                and day == final_date):
            if row is not None:
                raise ValueError('daily bar for session not closed at path completion')
            pending = day.isoformat()
            continue
        if item.status != Status.VERIFIED or span is None or item.data_verified_at is None:
            raise ValueError('session state not established for outcome path')
        opened, closed = span
        if not closed <= _aware(item.data_verified_at, 'data_verified_at') <= complete:
            raise ValueError('session verification out of order')
        if closed > received:
            raise ValueError('daily evidence does not cover completed session')
        if row is None:
            raise ValueError('missing daily bar for verified session')
        if row.semantic_class != DailyBarSemanticClass.VALID_EXECUTABLE:
            raise ValueError('quarantined daily bar in outcome path')
        bars.append(PaperReplayBar(
            instrument_id=record['instrument_id'], venue_id=VENUE_ID, symbol=record['symbol'],
            session_id=f'{VENUE_ID}:{day.isoformat()}', market_date=day, session_sequence=1,
            interval_start_utc=opened, interval_end_utc=closed, available_at_utc=complete,
            open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume,
            source_id=row.source_sha256,
            provenance_id=(f'{row.source_provider}:{row.source_snapshot_date.isoformat()}'
                           f':row-{row.source_row_number}')))
    if len(bars) > MAX_BARS:
        raise ValueError(f'more than {MAX_BARS} sessions in outcome path')
    # Look-back rows (before the detection date, or a session that opened before
    # detection) are ignored; every other row was matched to a path day above.
    path_dates = {item.market_date for item in in_path}
    for day in by_date:
        if day > final_date:
            raise ValueError('daily bar after calendar window')
        if (day >= detected.astimezone(CAIRO).date() and day not in path_dates
                and not (hours.get(day) and hours[day][0] < detected)):
            raise ValueError('daily bar outside the calendar window')

    report = {
        'contract_version': CONTRACT_VERSION,
        'observation_identity': identity,
        'decision_id': record['decision_id'],
        'market': 'EGX',
        'symbol': record['symbol'],
        'instrument_id': record['instrument_id'],
        'venue_id': VENUE_ID,
        'detected_at': record['detected_at'],
        'daily_source': {
            'source_provider': source.source_provider,
            'source_snapshot_date': source.source_snapshot_date.isoformat(),
            'source_sha256': source.source_sha256,
            'received_at': _utc(received),
            'received_at_clock': 'CALLER_ASSERTED_UNVERIFIED',
        },
        'calendar': {
            'first_date': sessions[0].market_date.isoformat(),
            'last_date': sessions[-1].market_date.isoformat(),
            'days': len(sessions),
            'admission': 'NOT_ESTABLISHED',
        },
        'path_complete_through_at': _utc(complete),
        'sessions_bound': [bar.market_date.isoformat() for bar in bars],
        'nontrading_days': nontrading,
        'pending_session': pending,
        'granularity': 'D1_SESSION',
        'availability_rule': AVAILABILITY_RULE,
        'price_basis': 'CANONICAL_DAILY_VALID_EXECUTABLE_OHLC',
        'corporate_actions': 'NOT_CHECKED',
        'outcome': 'NOT_EVALUATED',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
    return {'bars': tuple(bars), 'path_complete_through_at': complete, 'report': report}
