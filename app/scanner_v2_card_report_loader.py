"""Protected read-only loader for a host-retained Scanner v2 EGX card report.

The trusted host producer (not yet provisioned) publishes into one directory
named by ``EGX_SCANNER_V2_CARD_REPORT_DIR``:

- ``<sha256>.json``: exact ``scanner-v2-egx-card-report-v1`` bytes, named by
  their SHA-256 (content-addressed, never rewritten);
- ``current.json``: a small ``scanner-v2-card-report-pointer-v1`` document
  naming that SHA-256, atomically replaced on publication.

Every load re-reads both files without following symlinks and passes the exact
bytes to ``read_retained_egx_card_report``. Only a reader-accepted document is
returned; any other outcome is NOT_CONFIGURED or UNAVAILABLE with a fixed reason
and no partial content. Cards keep the state the producer computed at the
report's ``observed_at``; this loader does not relabel them. It separately
reports whether the retained freshness window and plan validity still hold at
load time, so a viewer is never shown an old report as current.

No route, producer, write, network or database access. The pointer hash is an
integrity check under a trusted host writer, not a signature. Nothing is
admitted, filled or promoted. LIVE_MONEY=false.
"""
from datetime import datetime, timezone
import json
import os
import re
import stat

from app.scanner_v2_card_report_reader import (
    MAX_BYTES, RetainedCardReportError, read_retained_egx_card_report)

ENV = 'EGX_SCANNER_V2_CARD_REPORT_DIR'
POINTER_NAME = 'current.json'
POINTER_SCHEMA = 'scanner-v2-card-report-pointer-v1'
RESPONSE_SCHEMA = 'scanner-v2-card-report-response-v1'
MAX_POINTER_BYTES = 4096
REASONS = (
    'NOT_CONFIGURED', 'CONFIG_INVALID', 'DIRECTORY_UNAVAILABLE', 'NOT_PUBLISHED',
    'POINTER_INVALID', 'REPORT_UNREADABLE', 'REPORT_REJECTED', 'REPORT_FROM_FUTURE')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)


class _Unavailable(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate pointer key')
        out[key] = value
    return out


def _reject_constant(name):
    raise ValueError('non-finite pointer number')


def _read_regular(dir_fd, name, limit, reason):
    """Read at most ``limit + 1`` bytes of a regular, non-symlink file."""
    try:
        fd = os.open(name, _OPEN_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise _Unavailable('NOT_PUBLISHED' if name == POINTER_NAME else reason) from None
    except OSError:
        raise _Unavailable(reason) from None
    try:
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise _Unavailable(reason)
            return source.read(limit + 1)
    except OSError:
        raise _Unavailable(reason) from None


def _pointer_sha256(payload):
    if len(payload) > MAX_POINTER_BYTES:
        raise _Unavailable('POINTER_INVALID')
    try:
        pointer = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique,
                             parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _Unavailable('POINTER_INVALID') from None
    if (type(pointer) is not dict or pointer.keys() != {'schema_version', 'report_sha256'}
            or pointer['schema_version'] != POINTER_SCHEMA
            or type(pointer['report_sha256']) is not str
            or _HEX64.fullmatch(pointer['report_sha256']) is None):
        raise _Unavailable('POINTER_INVALID')
    return pointer['report_sha256']


def card_current_at(card, fresh_until, now):
    """Whether a reader-accepted card's data window and plan still hold at ``now``."""
    plan = card['strategy_inference']['plan']
    flat_by = plan['flat_by']
    return (now < fresh_until and now < datetime.fromisoformat(plan['valid_until'])
            and (flat_by is None or now < datetime.fromisoformat(flat_by)))


def _load(directory, now):
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | getattr(os, 'O_CLOEXEC', 0))
    except OSError:
        raise _Unavailable('DIRECTORY_UNAVAILABLE') from None
    try:
        digest = _pointer_sha256(
            _read_regular(dir_fd, POINTER_NAME, MAX_POINTER_BYTES, 'POINTER_INVALID'))
        # Content-addressed: a pointer replaced mid-load still names exact bytes.
        payload = _read_regular(dir_fd, f'{digest}.json', MAX_BYTES, 'REPORT_UNREADABLE')
    finally:
        os.close(dir_fd)
    try:
        doc = read_retained_egx_card_report(payload, expected_sha256=digest)
    except RetainedCardReportError as exc:
        raise _Unavailable('REPORT_REJECTED', str(exc)) from None
    except RecursionError:
        raise _Unavailable('REPORT_REJECTED', 'retained report nesting too deep') from None
    # The reader has validated every timestamp below as timezone-aware ISO text.
    observed_at = datetime.fromisoformat(doc['observed_at'])
    if now < observed_at:
        raise _Unavailable('REPORT_FROM_FUTURE')
    fresh_until = datetime.fromisoformat(
        doc['provenance']['retained_evidence']['binding']['fresh_until'])
    current = sum(card_current_at(card, fresh_until, now) for card in doc['cards'])
    return digest, doc, ('WITHIN_RETAINED_WINDOW' if now < fresh_until else 'STALE_AT_LOAD'), current


def _response(status, reason, loaded_at, *, detail=None, digest=None, doc=None,
              freshness=None, current=None):
    return {
        'schema_version': RESPONSE_SCHEMA, 'status': status, 'reason': reason,
        'detail': detail, 'loaded_at': loaded_at.isoformat(), 'report_sha256': digest,
        'report_observed_at': None if doc is None else doc['observed_at'],
        'load_freshness': freshness, 'cards_current_at_load': current, 'report': doc,
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED'}


def load_retained_egx_card_report(*, environ=None, now=None):
    """Return a fail-closed response projection for the published card report.

    ``status`` is AVAILABLE only when the reader accepted the exact bytes named
    by the pointer. ``report`` is otherwise ``None``; ``detail`` carries only the
    reader's code-defined rejection text.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    configured = (os.environ if environ is None else environ).get(ENV)
    if not configured:
        return _response('NOT_CONFIGURED', 'NOT_CONFIGURED', now)
    if type(configured) is not str or not os.path.isabs(configured) or '\x00' in configured:
        return _response('UNAVAILABLE', 'CONFIG_INVALID', now)
    try:
        digest, doc, freshness, current = _load(configured, now)
    except _Unavailable as exc:
        return _response('UNAVAILABLE', exc.reason, now, detail=exc.detail)
    return _response('AVAILABLE', None, now, digest=digest, doc=doc,
                     freshness=freshness, current=current)
