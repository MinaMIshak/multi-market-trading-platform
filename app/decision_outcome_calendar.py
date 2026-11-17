"""Chain write-once retained EGX calendar observations into an outcome calendar.

Phase G/H groundwork (decision-outcome-calendar-chain-v1). The outcome job
currently reads current ``market_sessions`` rows, which are upserted (mutable),
so SYSTEM keeps the gap OUTCOME_CALENDAR_FROM_MUTABLE_SESSIONS. The repository
already retains write-once calendar observations (``calendar_observations``,
written by ``TradingRepository.retain_calendar_observation``): each one is the
exact JSON array of ``MarketSession`` rows from a VERIFIED completed session,
through explicit non-trading days, to the next session's UNKNOWN schedule,
observed before that next session opened.

This pure function rebuilds a contiguous ``MarketSession`` tuple, the input the
unchanged outcome producer and session-bar binding read, from a caller-supplied
set of such retained observations. It adds no calendar semantics of its own:

1. every observation is re-validated by the unchanged ``project_egx_calendar``
   with ``decision_time`` = its own ``observed_at`` (digest, contiguity, EGX
   weekend policy, hours, completed-session verification at observation, and
   observation before the next session opened), so a damaged or late observation is
   rejected exactly as the scanner path would reject it;
2. observations are ordered by session (input order does not matter); two for
   one session are refused (DUPLICATE_SESSION, never resolved by preference);
3. each observation's ``next_session`` must be the next observation's
   ``session`` (CHAIN_NOT_CONTIGUOUS otherwise), so no trading day is skipped;
4. the chain keeps each observation's VERIFIED completed session and its
   intervening non-trading rows; the UNKNOWN schedule row is superseded by the
   later observation's VERIFIED row for the same date (a changed schedule is
   listed in ``schedule_revisions``, not hidden); only the last observation's
   UNKNOWN row is kept, as the final pending day;
5. every observation must have been observed at or before ``as_of`` (the
   caller's evaluation clock), so no calendar knowledge from after the clock
   can shape an outcome path.

Calendar admission stays NOT_ESTABLISHED: a retained observation is evidence of
what the repository recorded, not an exchange-authoritative calendar. No IO,
database read, store, route, scheduler or caller; the outcome job still reads
mutable rows until a later change wires this in. Nothing is evaluated here.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json

from app.domain.models import MarketSession
from app.scanner_v2_calendar import project_egx_calendar

CONTRACT_VERSION = 'decision-outcome-calendar-chain-v1'
CHAIN_REASONS = ('CONFIG_INVALID', 'OBSERVATION_REJECTED', 'OBSERVED_AFTER_CLOCK',
                 'DUPLICATE_SESSION', 'CHAIN_NOT_CONTIGUOUS', 'CHAIN_TOO_LONG')
MAX_OBSERVATIONS = 400
MAX_CHAIN_DAYS = 400
MAX_DETAIL = 200
SCHEDULE_FIELDS = ('opened_at', 'first15_closed_at', 'closed_at')


class CalendarChainError(ValueError):
    """Chain refused; ``reason`` is one of CHAIN_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class RetainedCalendarObservation:
    """One ``calendar_observations`` row: exact bytes, digest, session and observation time."""

    artifact_sha256: str
    session: date
    artifact: bytes
    observed_at: datetime


def _aware(value):
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None)


def _utc(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _config(observations, as_of):
    if not _aware(as_of):
        raise CalendarChainError('CONFIG_INVALID', 'timezone-aware as_of required')
    if (type(observations) is not tuple or not 0 < len(observations) <= MAX_OBSERVATIONS
            or any(type(item) is not RetainedCalendarObservation for item in observations)):
        raise CalendarChainError(
            'CONFIG_INVALID',
            f'tuple of 1..{MAX_OBSERVATIONS} RetainedCalendarObservation required')
    for item in observations:
        if (type(item.artifact_sha256) is not str or type(item.session) is not date
                or type(item.artifact) is not bytes or not _aware(item.observed_at)):
            raise CalendarChainError('CONFIG_INVALID', 'exact observation field types required')


def _project(item):
    """Unchanged scanner-path validation at the observation's own time, plus its rows."""
    try:
        projection = project_egx_calendar(
            item.artifact, expected_sha256=item.artifact_sha256, session=item.session,
            observed_at=item.observed_at, decision_time=item.observed_at)
        # project_egx_calendar accepted these exact bytes as strict JSON rows.
        rows = tuple(MarketSession.model_validate(row) for row in json.loads(item.artifact))
    except (TypeError, ValueError) as exc:
        raise CalendarChainError(
            'OBSERVATION_REJECTED', f'{item.session.isoformat()}: {exc}'[:MAX_DETAIL]) from None
    if (projection.session != item.session or projection.artifact_sha256 != item.artifact_sha256
            or rows[0].market_date != item.session
            or rows[-1].market_date != projection.next_session):
        raise CalendarChainError('OBSERVATION_REJECTED', item.session.isoformat())
    return projection, rows


def _schedule(item):
    return tuple(getattr(item, name) for name in SCHEDULE_FIELDS)


def chain_egx_calendar_observations(observations, *, as_of) -> dict:
    """Return the chained ``MarketSession`` tuple and a JSON-ready report; pure.

    ``observations`` is a tuple of ``RetainedCalendarObservation`` read by the
    caller from the write-once ``calendar_observations`` table; ``as_of`` is the
    caller's aware evaluation clock. Refusals raise ``CalendarChainError``.
    """
    _config(observations, as_of)
    late = sorted(item.session for item in observations if item.observed_at > as_of)
    if late:
        raise CalendarChainError('OBSERVED_AFTER_CLOCK', late[0].isoformat())
    ordered = sorted(observations, key=lambda item: item.session)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.session == current.session:
            raise CalendarChainError('DUPLICATE_SESSION', current.session.isoformat())
    projected = [(item, *_project(item)) for item in ordered]

    sessions, revisions = [], []
    for index, (item, projection, rows) in enumerate(projected):
        if index:
            expected = projected[index - 1][1].next_session
            if item.session != expected:
                raise CalendarChainError(
                    'CHAIN_NOT_CONTIGUOUS',
                    f'expected {expected.isoformat()}, got {item.session.isoformat()}')
            # The earlier UNKNOWN schedule row is superseded by this VERIFIED row.
            if _schedule(projected[index - 1][2][-1]) != _schedule(rows[0]):
                revisions.append(item.session.isoformat())
        sessions.extend(rows if index == len(projected) - 1 else rows[:-1])
    if len(sessions) > MAX_CHAIN_DAYS:
        raise CalendarChainError('CHAIN_TOO_LONG', f'more than {MAX_CHAIN_DAYS} days')
    sessions = tuple(sessions)

    payload = '[' + ','.join(item.model_dump_json() for item in sessions) + ']'
    report = {
        'contract_version': CONTRACT_VERSION,
        'market': 'EGX',
        'first_date': sessions[0].market_date.isoformat(),
        'last_date': sessions[-1].market_date.isoformat(),
        'days': len(sessions),
        'sessions_sha256': sha256(payload.encode('utf-8')).hexdigest(),
        'observations': [
            {'artifact_sha256': item.artifact_sha256, 'session': item.session.isoformat(),
             'next_session': projection.next_session.isoformat(),
             'observed_at': _utc(item.observed_at)}
            for item, projection, _ in projected],
        'latest_observed_at': _utc(max(item.observed_at for item in ordered)),
        'as_of': _utc(as_of),
        'final_session': sessions[-1].market_date.isoformat(),
        'final_session_status': sessions[-1].status.value,
        'schedule_revisions': revisions,
        'source': 'CALENDAR_OBSERVATIONS_RETAINED',
        'retention': 'WRITE_ONCE_RETAINED',
        'admission': 'NOT_ESTABLISHED',
        'outcome': 'NOT_EVALUATED',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
    return {'sessions': sessions, 'report': report}
