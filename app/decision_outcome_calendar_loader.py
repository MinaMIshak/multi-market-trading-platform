"""Load caller-named write-once EGX calendar observations for the outcome calendar chain.

Phase G/H groundwork (decision-outcome-calendar-observations-v1). The pure
chain ``app.decision_outcome_calendar.chain_egx_calendar_observations`` takes a
tuple of ``RetainedCalendarObservation`` (the columns of the write-once
``calendar_observations`` table written by
``TradingRepository.retain_calendar_observation``), but ``TradingRepository``
only exposes a projection getter for one digest, not the exact row. This
read-only loader is the retained-evidence source for that input; it adds no
calendar semantics, chain or job logic:

1. the caller names exact ``artifact_sha256`` values (lowercase hex); nothing
   is searched for, defaulted, ranged or chosen as "latest";
2. each named row is read from ``calendar_observations`` only; the mutable
   ``market_sessions`` table is never read;
3. the exact artifact must be a BLOB within ``MAX_ARTIFACT_BYTES`` whose
   SHA-256 equals the named digest, ``session`` an exact ISO date and
   ``observed_at`` an exact ISO timestamp with a UTC offset (naive refused).

Any failure refuses the whole load with one fixed reason (no partial tuple is
returned). Observations are returned in the caller's order; contiguity,
duplicate sessions, EGX weekend/hours/verification rules and the evaluation
clock are checked by the unchanged chain (which re-runs the unchanged
``project_egx_calendar`` per observation), not here.

``observed_at`` is the repository's own recorded clock at retention, labelled
REPOSITORY_CLOCK_UNVERIFIED (it is not independently attested). Calendar
admission stays NOT_ESTABLISHED. Read only: the database is never created,
initialized, upgraded or written by this module (the caller supplies an
existing ``app.storage.Database``; a missing file is refused before any
connection). The only caller is the host job ``app.decision_outcome_job``
(decision-outcome-job-v2, not scheduled), which chains the loaded observations
at its own clock. No scheduler or route.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from app.decision_outcome_calendar import MAX_OBSERVATIONS, RetainedCalendarObservation

CONTRACT_VERSION = 'decision-outcome-calendar-observations-v1'
LOAD_REASONS = ('CONFIG_INVALID', 'DATABASE_UNAVAILABLE', 'OBSERVATION_UNAVAILABLE',
                'OBSERVATION_REJECTED')
# retain_calendar_observation retains at most 32 market_sessions rows (a
# 1..31-day window); 64 KiB is far above that and bounds what is read.
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_DETAIL = 200
_DIGEST = re.compile(r'[0-9a-f]{64}')


class CalendarObservationLoadError(RuntimeError):
    """Load refused; ``reason`` is one of LOAD_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _fail(reason, digest, why):
    raise CalendarObservationLoadError(reason, f'{digest}: {why}'[:MAX_DETAIL])


def _digests(artifact_digests):
    if (type(artifact_digests) is not tuple
            or not 0 < len(artifact_digests) <= MAX_OBSERVATIONS
            or any(type(d) is not str or not _DIGEST.fullmatch(d) for d in artifact_digests)):
        raise CalendarObservationLoadError(
            'CONFIG_INVALID',
            f'tuple of 1..{MAX_OBSERVATIONS} lowercase hex SHA-256 digests required')
    if len(set(artifact_digests)) != len(artifact_digests):
        raise CalendarObservationLoadError('CONFIG_INVALID', 'duplicate digest')
    return artifact_digests


def _read(database, digests):
    """Exact rows for the named digests from one connection; never writes."""
    # Database.connect creates a missing file; refuse first so nothing is created.
    path = getattr(database, 'path', None)
    try:
        exists = isinstance(path, Path) and path.is_file()
    except OSError:
        exists = False
    if not exists:
        raise CalendarObservationLoadError('DATABASE_UNAVAILABLE', 'existing database file required')
    try:
        connection = database.connect()
        try:
            return [connection.execute(
                'SELECT artifact_sha256, session, artifact, observed_at '
                'FROM calendar_observations WHERE artifact_sha256 = ?', (digest,)).fetchone()
                for digest in digests]
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        raise CalendarObservationLoadError('DATABASE_UNAVAILABLE') from None


def _session(digest, text):
    try:
        value = date.fromisoformat(text) if type(text) is str else None
    except ValueError:
        value = None
    if value is None or value.isoformat() != text:
        _fail('OBSERVATION_REJECTED', digest, 'exact ISO session date required')
    return value


def _observed_at(digest, text):
    try:
        value = datetime.fromisoformat(text) if type(text) is str else None
    except ValueError:
        value = None
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        _fail('OBSERVATION_REJECTED', digest, 'timezone-aware observed_at required')
    return value


def _observation(digest, row):
    if row is None:
        _fail('OBSERVATION_UNAVAILABLE', digest, 'no retained calendar observation')
    if row['artifact_sha256'] != digest:
        _fail('OBSERVATION_REJECTED', digest, 'row digest mismatch')
    artifact = row['artifact']
    if type(artifact) is not bytes:
        _fail('OBSERVATION_REJECTED', digest, 'artifact must be a BLOB')
    if not 0 < len(artifact) <= MAX_ARTIFACT_BYTES:
        _fail('OBSERVATION_REJECTED', digest, f'artifact size outside 1..{MAX_ARTIFACT_BYTES}')
    if sha256(artifact).hexdigest() != digest:
        _fail('OBSERVATION_REJECTED', digest, 'artifact hash mismatch')
    return RetainedCalendarObservation(
        artifact_sha256=digest, session=_session(digest, row['session']), artifact=artifact,
        observed_at=_observed_at(digest, row['observed_at']))


def load_egx_calendar_observations(database, artifact_digests) -> dict:
    """Return ``{'observations': (RetainedCalendarObservation, ...), 'sources': [...]}``.

    ``database`` is an existing ``app.storage.Database`` (read only);
    ``artifact_digests`` is a tuple of exact ``calendar_observations``
    digests. ``observations`` is in the given order and is the input of the
    unchanged ``chain_egx_calendar_observations``; ``sources`` lists one
    provenance entry per digest in the same order. Refusals raise
    ``CalendarObservationLoadError``.
    """
    digests = _digests(artifact_digests)
    rows = _read(database, digests)
    observations = tuple(_observation(digest, row) for digest, row in zip(digests, rows))
    return {
        'schema_version': CONTRACT_VERSION,
        'market': 'EGX',
        'observations': observations,
        'sources': [
            {'artifact_sha256': item.artifact_sha256, 'session': item.session.isoformat(),
             'observed_at': row['observed_at'], 'artifact_bytes': len(item.artifact)}
            for item, row in zip(observations, rows)],
        'selection': 'CALLER_NAMED_DIGESTS',
        'source': 'CALENDAR_OBSERVATIONS_RETAINED',
        'retention': 'WRITE_ONCE_RETAINED',
        'observed_at_clock': 'REPOSITORY_CLOCK_UNVERIFIED',
        'calendar_validation': 'NOT_PERFORMED_BY_LOADER',
        'admission': 'NOT_ESTABLISHED',
        'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED',
    }
