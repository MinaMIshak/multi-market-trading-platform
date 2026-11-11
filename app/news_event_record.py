"""Exact retained bytes for news-event-v1 records; evidence enrichment only.

This is the byte codec a future retained store for ``app.news_event.NewsEvent``
must use. It is pure: no file, network or database IO, no provider, route,
scheduler or caller.

- ``encode_news_event`` returns the canonical bytes of one record (sorted keys,
  compact separators, ASCII, no NaN). These are exactly the bytes hashed by
  ``NewsEvent.identity``, so the SHA-256 of the retained bytes IS the record
  identity and can serve as its content address.
- ``read_retained_news_event`` accepts only bounded bytes whose SHA-256 equals
  the digest recorded by the trusted producer, that are strict JSON (no
  duplicate keys, no NaN/Infinity), that validate as an exact news-event-v1
  record (extra fields, naive timestamps, LIVE_MONEY=true or any trading effect
  fail) and that re-encode to the identical bytes. Any failure raises
  ``RetainedNewsEventError``; callers must treat the record as unavailable and
  never use partial content.

Timestamps keep their original offsets. Point-in-time use is unchanged: decoded
records still go through ``select_point_in_time``. A digest is an integrity
check, not a signature. Nothing is admitted, filled or promoted;
LIVE_MONEY=false.
"""
from hashlib import sha256
import json
import re

from pydantic import ValidationError

from app.news_event import NewsEvent

MAX_BYTES = 256 * 1024
_HEX64 = re.compile(r'[0-9a-f]{64}')


class RetainedNewsEventError(ValueError):
    """Retained news-event bytes failed closed; nothing from them may be used."""


def _fail(reason):
    raise RetainedNewsEventError(reason)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail('duplicate JSON key')
        out[key] = value
    return out


def _constant(name):
    _fail('non-finite JSON number')


def _canonical(document) -> bytes:
    # Must stay identical to app.news_event._digest serialization.
    return json.dumps(document, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def encode_news_event(event) -> bytes:
    """Canonical retained bytes of one exact, revalidated ``NewsEvent``."""
    if type(event) is not NewsEvent:
        raise ValueError('exact NewsEvent required')
    event = NewsEvent.model_validate(event.model_dump(mode='python'))
    payload = _canonical(event.model_dump(mode='json'))
    if len(payload) > MAX_BYTES:
        raise ValueError('news event record exceeds retained size bound')
    if sha256(payload).hexdigest() != event.identity:
        raise ValueError('canonical bytes do not match record identity')
    return payload


def read_retained_news_event(payload, *, expected_sha256) -> NewsEvent:
    """Validate exact retained bytes; return the ``NewsEvent`` they encode."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        _fail('retained record must be bounded bytes')
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        _fail('invalid expected_sha256')
    if sha256(payload).hexdigest() != expected_sha256:
        _fail('retained record hash mismatch')
    try:
        document = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail('retained record is not strict JSON')
    if type(document) is not dict:
        _fail('retained record is not a JSON object')
    try:
        event = NewsEvent.model_validate_json(payload)
    except ValidationError:
        _fail('retained record failed news-event-v1 validation')
    try:
        canonical = encode_news_event(event)
    except ValueError:
        _fail('retained record cannot be re-encoded')
    if canonical != payload:
        _fail('retained record is not canonical')
    if event.identity != expected_sha256:
        _fail('retained record identity mismatch')
    return event
