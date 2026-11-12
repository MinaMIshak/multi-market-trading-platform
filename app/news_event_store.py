"""Write-once content-addressed store for retained news-event-v1 records.

Evidence enrichment only. A trusted host producer (not yet provisioned) calls
``retain_news_event`` with one ``NewsEvent`` and a host-provisioned directory;
readers call ``load_retained_news_events`` and then apply the unchanged
``select_point_in_time`` rule. Layout: one ``<identity>.json`` file per exact
record, holding the canonical bytes of ``app.news_event_record`` (so the file
name is the SHA-256 of its bytes and the record identity). There is no pointer:
every retained record is evidence and none is ever replaced or deleted.

Retention, under an exclusive non-blocking lock on the directory:

1. encodes the record and passes the exact bytes through the same
   ``read_retained_news_event`` gate readers use, before any write;
2. reads every retained record (any damaged or unexpected entry fails closed);
3. an already retained identical record is ALREADY_RETAINED (exact retry);
4. otherwise ``source.observed_at`` must lie within ``MAX_OBSERVATION_LAG`` at or
   before the store clock: a future observation is refused, and so is a
   backdated one, which would make the record look available to decisions that
   were taken before it was actually retained;
5. a different record for the same event and revision is refused (the
   point-in-time rule would fail closed on it);
6. writes the file once (temporary file, fsync, hard link without overwrite),
   fsyncs the directory and re-reads the exact bytes through the codec.

It never creates the directory, rewrites or deletes records, and does no
network or database IO. A digest is integrity under a trusted host writer, not a
signature. No scheduler, provider, route or caller exists. Nothing is admitted,
filled or promoted; trading_effect NONE; LIVE_MONEY=false.
"""
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import os
import re
import stat

from app.news_event import NewsEvent
from app.news_event_record import (
    MAX_BYTES, RetainedNewsEventError, encode_news_event, read_retained_news_event)
from app.scanner_v2_card_report_loader import _read_regular, _Unavailable
from app.scanner_v2_card_report_publisher import _fsync_dir, _unlink_quietly, _write_temp

RESULT_SCHEMA = 'news-event-retention-v1'
MAX_RECORDS = 10000
MAX_OBSERVATION_LAG = timedelta(minutes=5)
STORE_REASONS = (
    'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'DIRECTORY_UNSAFE', 'STORE_BUSY',
    'RECORD_REJECTED', 'STORE_INVALID', 'STORE_FULL', 'OBSERVED_IN_FUTURE',
    'OBSERVATION_BACKDATED', 'REVISION_CONFLICT', 'CONTENT_CONFLICT', 'WRITE_FAILED',
    'RETENTION_UNVERIFIED')
_RECORD_NAME = re.compile(r'([0-9a-f]{64})\.json')
# Leftover temporary files of an interrupted write (``_write_temp`` naming).
_TEMP_NAME = re.compile(r'\.publish-[0-9]+-[0-9a-f]{16}\.tmp')
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)


class NewsEventStoreError(RuntimeError):
    """Retention or load refused; ``reason`` is one of STORE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _aware(now):
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    return now


def _open_directory(directory):
    if type(directory) is not str or not os.path.isabs(directory) or '\x00' in directory:
        raise NewsEventStoreError('CONFIG_INVALID')
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC)
    except OSError:
        raise NewsEventStoreError('DIRECTORY_UNAVAILABLE') from None
    try:
        info = os.fstat(dir_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise NewsEventStoreError('DIRECTORY_UNSAFE')
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def _read_record(dir_fd, identity):
    """Exact codec-accepted record named ``identity``; anything else fails closed."""
    try:
        payload = _read_regular(dir_fd, f'{identity}.json', MAX_BYTES, 'RECORD_UNREADABLE')
    except _Unavailable as exc:
        raise NewsEventStoreError('STORE_INVALID', exc.reason) from None
    try:
        return payload, read_retained_news_event(payload, expected_sha256=identity)
    except (RetainedNewsEventError, RecursionError) as exc:
        raise NewsEventStoreError('STORE_INVALID', str(exc)) from None


def _scan(dir_fd):
    """Every retained record keyed by identity; unexpected entries fail closed."""
    try:
        names = os.listdir(dir_fd)
    except OSError:
        raise NewsEventStoreError('DIRECTORY_UNAVAILABLE') from None
    identities = []
    for name in names:
        match = _RECORD_NAME.fullmatch(name)
        if match is not None:
            identities.append(match.group(1))
        elif _TEMP_NAME.fullmatch(name) is None:
            raise NewsEventStoreError('STORE_INVALID', 'unexpected directory entry')
    if len(identities) > MAX_RECORDS:
        raise NewsEventStoreError('STORE_INVALID', 'too many records')
    return {identity: _read_record(dir_fd, identity)[1] for identity in sorted(identities)}


def load_retained_news_events(directory, *, market) -> tuple[NewsEvent, ...]:
    """All retained records of one market, ordered by identity.

    Fails closed (``NewsEventStoreError``) if any entry in the directory is
    damaged or unexpected; partial content is never returned. The result is raw
    retained evidence: callers must still apply ``select_point_in_time`` with an
    explicit decision time. An empty result is not proof that no event exists.
    """
    if market not in ('EGX', 'US'):
        raise ValueError('unknown market')
    dir_fd = _open_directory(directory)
    try:
        records = _scan(dir_fd)
    finally:
        os.close(dir_fd)
    return tuple(event for event in records.values() if event.market == market)


def _result(status, event, retained_count):
    return {
        'schema_version': RESULT_SCHEMA, 'status': status, 'identity': event.identity,
        'dedup_key': event.dedup_key, 'market': event.market,
        'revision': event.source.revision,
        'observed_at': event.source.observed_at.isoformat(),
        'retained_count': retained_count,
        'trading_effect': 'NONE', 'mode': 'SHADOW', 'live_money': False,
        'execution_allowed': False, 'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED'}


def _write_once(dir_fd, identity, payload):
    """Create ``<identity>.json`` without overwriting; existing bytes must match."""
    name = f'{identity}.json'
    temp = _write_temp(dir_fd, payload)
    try:
        try:
            os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        except FileExistsError:
            try:
                existing = _read_regular(dir_fd, name, MAX_BYTES, 'CONTENT_CONFLICT')
            except _Unavailable:
                raise NewsEventStoreError('CONTENT_CONFLICT') from None
            if existing != payload:
                raise NewsEventStoreError('CONTENT_CONFLICT')
    finally:
        _unlink_quietly(dir_fd, temp)
    _fsync_dir(dir_fd)


def retain_news_event(directory, event, *, now=None):
    """Retain one exact ``NewsEvent`` write-once in a host-provisioned directory.

    ``now`` is the store clock (defaults to real UTC). Returns a
    ``news-event-retention-v1`` result with status RETAINED or
    ALREADY_RETAINED. Any refusal raises ``NewsEventStoreError``; refusals
    before the write leave the directory unchanged.
    """
    now = _aware(now)
    try:
        payload = encode_news_event(event)
        digest = sha256(payload).hexdigest()
        accepted = read_retained_news_event(payload, expected_sha256=digest)
    except (ValueError, TypeError, RecursionError) as exc:
        raise NewsEventStoreError('RECORD_REJECTED', type(exc).__name__) from None

    dir_fd = _open_directory(directory)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NewsEventStoreError('STORE_BUSY') from None

        retained = _scan(dir_fd)
        if digest in retained:
            return _result('ALREADY_RETAINED', accepted, len(retained))

        observed_at = accepted.source.observed_at
        if observed_at > now:
            raise NewsEventStoreError('OBSERVED_IN_FUTURE')
        if now - observed_at > MAX_OBSERVATION_LAG:
            raise NewsEventStoreError('OBSERVATION_BACKDATED')
        slot = (accepted.dedup_key, accepted.source.revision)
        if any((held.dedup_key, held.source.revision) == slot for held in retained.values()):
            raise NewsEventStoreError('REVISION_CONFLICT')
        if len(retained) >= MAX_RECORDS:
            raise NewsEventStoreError('STORE_FULL')

        try:
            _write_once(dir_fd, digest, payload)
        except OSError as exc:
            raise NewsEventStoreError('WRITE_FAILED', type(exc).__name__) from None

        try:
            stored, verified = _read_record(dir_fd, digest)
        except NewsEventStoreError as exc:
            raise NewsEventStoreError('RETENTION_UNVERIFIED', exc.detail) from None
        if stored != payload or verified.identity != digest:
            raise NewsEventStoreError('RETENTION_UNVERIFIED', 'retained bytes differ')
        return _result('RETAINED', accepted, len(retained) + 1)
    finally:
        os.close(dir_fd)
