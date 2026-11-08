"""Trusted host publisher for retained Scanner v2 EGX card reports.

This is the write side of ``app.scanner_v2_card_report_loader``. A trusted host
job calls ``publish_egx_card_report`` with the exact ranking bytes, the SHA-256
from a verified snapshot manifest and a ``RetainedEgxRankBinding``. The
publisher:

1. projects the report with the existing ``project_egx_card_report`` at its own
   clock (no caller-supplied observation time);
2. encodes it canonically and passes the exact bytes through the same
   ``read_retained_egx_card_report`` gate the route uses, before any write;
3. under an exclusive non-blocking lock on the host-provisioned directory, reads
   the currently published report through the loader's own read path and refuses
   a damaged publication, a clock regression or an older ranking generation;
4. writes ``<sha256>.json`` once (temporary file, fsync, hard link without
   overwrite; an existing file must hold identical bytes);
5. atomically replaces ``current.json`` and fsyncs the directory;
6. re-reads through the loader path and requires the new digest.

It never deletes or rewrites published report bytes, never creates the
directory and does no network or database IO. The pointer hash is integrity
under a trusted host writer, not a signature. There is no scheduler or runtime
caller in this repository yet, so ``feature_status()`` is unchanged. Nothing is
admitted, filled or promoted. LIVE_MONEY=false.
"""
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
import secrets
import stat

from app.scanner_v2_card_report import project_egx_card_report
from app.scanner_v2_card_report_loader import (
    POINTER_NAME, POINTER_SCHEMA, _load, _read_regular, _Unavailable)
from app.scanner_v2_card_report_reader import (
    MAX_BYTES, RetainedCardReportError, read_retained_egx_card_report)

RESULT_SCHEMA = 'scanner-v2-card-report-publication-v1'
PUBLICATION_REASONS = (
    'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'DIRECTORY_UNSAFE', 'PUBLICATION_BUSY',
    'PROJECTION_REJECTED', 'REPORT_REJECTED', 'EXISTING_PUBLICATION_INVALID',
    'CLOCK_REGRESSION', 'RANKING_REGRESSION', 'CONTENT_CONFLICT', 'WRITE_FAILED',
    'PUBLICATION_UNVERIFIED')
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)


class CardReportPublicationError(RuntimeError):
    """Publication refused or failed; ``reason`` is one of PUBLICATION_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def encode_card_report(document):
    """Canonical exact bytes for a card-report document (sorted keys, no NaN)."""
    return json.dumps(document, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _fsync_dir(dir_fd):
    os.fsync(dir_fd)


def _write_temp(dir_fd, payload):
    name = f'.publish-{os.getpid()}-{secrets.token_hex(8)}.tmp'
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC,
                 0o444, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, 'wb') as sink:
            sink.write(payload)
            sink.flush()
            os.fsync(sink.fileno())
    except BaseException:
        _unlink_quietly(dir_fd, name)
        raise
    return name


def _unlink_quietly(dir_fd, name):
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _retain_report(dir_fd, digest, payload):
    """Create ``<digest>.json`` without overwriting; existing bytes must match."""
    name = f'{digest}.json'
    temp = _write_temp(dir_fd, payload)
    try:
        try:
            os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        except FileExistsError:
            try:
                existing = _read_regular(dir_fd, name, MAX_BYTES, 'CONTENT_CONFLICT')
            except _Unavailable:
                raise CardReportPublicationError('CONTENT_CONFLICT') from None
            if existing != payload:
                raise CardReportPublicationError('CONTENT_CONFLICT')
    finally:
        _unlink_quietly(dir_fd, temp)
    _fsync_dir(dir_fd)


def _replace_pointer(dir_fd, digest):
    pointer = json.dumps({'schema_version': POINTER_SCHEMA, 'report_sha256': digest},
                         sort_keys=True).encode('utf-8')
    temp = _write_temp(dir_fd, pointer)
    try:
        os.replace(temp, POINTER_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        _unlink_quietly(dir_fd, temp)
        raise
    _fsync_dir(dir_fd)


def _existing(directory, now):
    """Return (digest, generated_at) of the current publication, or (None, None)."""
    try:
        digest, doc, _, _ = _load(directory, now)
    except _Unavailable as exc:
        if exc.reason == 'NOT_PUBLISHED':
            return None, None
        if exc.reason == 'REPORT_FROM_FUTURE':
            raise CardReportPublicationError('CLOCK_REGRESSION') from None
        raise CardReportPublicationError('EXISTING_PUBLICATION_INVALID', exc.reason) from None
    return digest, datetime.fromisoformat(doc['provenance']['generated_at'])


def _result(status, digest, document, previous):
    return {
        'schema_version': RESULT_SCHEMA, 'status': status, 'report_sha256': digest,
        'previous_report_sha256': previous, 'observed_at': document['observed_at'],
        'ranking_generated_at': document['provenance']['generated_at'],
        'card_count': len(document['cards']),
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


def publish_egx_card_report(directory, artifact, *, expected_sha256, evidence, now=None):
    """Publish one retained EGX card report into a host-provisioned directory.

    ``now`` is the producer clock (defaults to real UTC); it becomes the
    report's ``observed_at``. Returns a ``scanner-v2-card-report-publication-v1``
    result with status PUBLISHED or ALREADY_CURRENT. Any refusal raises
    ``CardReportPublicationError``; refusals detected before step 5 leave the
    current pointer unchanged.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    if type(directory) is not str or not os.path.isabs(directory) or '\x00' in directory:
        raise CardReportPublicationError('CONFIG_INVALID')

    try:
        document = project_egx_card_report(
            artifact, expected_sha256=expected_sha256, evidence=evidence, observed_at=now)
        payload = encode_card_report(document)
    except (ValueError, TypeError) as exc:
        raise CardReportPublicationError('PROJECTION_REJECTED', type(exc).__name__) from None
    digest = sha256(payload).hexdigest()
    try:
        accepted = read_retained_egx_card_report(payload, expected_sha256=digest)
    except (RetainedCardReportError, RecursionError) as exc:
        raise CardReportPublicationError('REPORT_REJECTED', str(exc)) from None
    # Byte comparison: tuples in the projection and lists from JSON encode alike.
    if encode_card_report(accepted) != payload:
        raise CardReportPublicationError('REPORT_REJECTED', 'canonical round trip differs')
    document = accepted

    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC)
    except OSError:
        raise CardReportPublicationError('DIRECTORY_UNAVAILABLE') from None
    try:
        info = os.fstat(dir_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise CardReportPublicationError('DIRECTORY_UNSAFE')
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CardReportPublicationError('PUBLICATION_BUSY') from None

        previous, previous_generated = _existing(directory, now)
        if previous == digest:
            return _result('ALREADY_CURRENT', digest, document, previous)
        if (previous_generated is not None and datetime.fromisoformat(
                document['provenance']['generated_at']) < previous_generated):
            raise CardReportPublicationError('RANKING_REGRESSION')

        try:
            _retain_report(dir_fd, digest, payload)
            _replace_pointer(dir_fd, digest)
        except OSError as exc:
            raise CardReportPublicationError('WRITE_FAILED', type(exc).__name__) from None

        try:
            verified, _, _, _ = _load(directory, now)
        except _Unavailable as exc:
            raise CardReportPublicationError('PUBLICATION_UNVERIFIED', exc.reason) from None
        if verified != digest:
            raise CardReportPublicationError('PUBLICATION_UNVERIFIED', 'pointer drift')
        return _result('PUBLISHED', digest, document, previous)
    finally:
        os.close(dir_fd)
