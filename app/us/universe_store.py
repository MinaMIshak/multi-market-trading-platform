"""Write-once retained history of US-UNIVERSE-v1 summaries (us-universe-retention-v1).

``app.us.new_listing_retained.build_retained_new_listing_admissions`` needs an
*earlier* US-UNIVERSE-v1 document as its baseline, but ``app.us_run`` only
writes the member-less universe summary into ``us-ranking.json``, which every
run overwrites. The raw directory and scanner bytes are retained
content-addressed by ``collect_universe``; the provenance naming them is not.
This store keeps that provenance: one ``<sha256>.json`` file per exact summary,
holding canonical bytes (sorted keys, compact, ASCII, no NaN), so the file name
is the SHA-256 of its bytes and the record identity. Nothing is ever replaced
or deleted.

The retained summary is exactly the ``universe`` object ``us_run`` publishes:
``members`` is dropped (the new-listing composition re-derives membership from
the retained raw bytes and reproduces the recorded counts). The reader checks
structure only: exact field set, ``US-UNIVERSE-v1``, count consistency
(``eligible + excluded == scanner_rows``, floor present iff anything is
eligible) and exactly one provenance record for each Nasdaq Trader directory
URL and the TradingView scanner URL with a lowercase SHA-256 digest and an
aware ``retrieved_at``. It does not read raw bytes; the unchanged
``load_retained_directory_master`` / ``build_retained_new_listing_admissions``
re-verify them at use.

Retention, under an exclusive non-blocking lock on a host-provisioned directory
(self-contained directory, strict-JSON and write-once helpers mirroring the
other retained stores; only the scanner-v2 card-report bounded reader and
temp-file/fsync helpers are reused):

1. the summary is canonicalised and passed through the same reader gate
   before any write;
2. every retained record is read (damaged or unexpected entries fail closed);
3. an identical record is ALREADY_RETAINED (exact retry);
4. every ``retrieved_at`` must lie at or before the store clock
   (OBSERVED_IN_FUTURE) and within ``MAX_OBSERVATION_LAG`` of it
   (OBSERVATION_BACKDATED), so history cannot be reconstructed after the fact
   from rewritten provenance;
5. a different summary with identical provenance and size is
   PROVENANCE_CONFLICT (``build_universe`` is deterministic over those bytes);
6. the file is written once and re-read through the reader.

The observation time of a record is its latest ``retrieved_at``: the run's own
recorded clock, labelled RETAINED_PROVENANCE_RETRIEVED_AT_UNVERIFIED. A digest
is integrity under a trusted host writer, not a signature. No network, database,
raw-byte read, caller, job, scheduler or route; ``app.us_run``,
``app.us.universe`` and the new-listing modules are unchanged. Retention is not
membership, admission or trading eligibility. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
import re
import stat

from app.scanner_v2_card_report_loader import _read_regular, _Unavailable
from app.scanner_v2_card_report_publisher import _fsync_dir, _unlink_quietly, _write_temp
from app.us.universe import NASDAQ_LISTED_URL, OTHER_LISTED_URL, SCANNER_URL
from app.us.universe import VERSION as UNIVERSE_VERSION

RESULT_SCHEMA = "us-universe-retention-v1"
MAX_BYTES = 256 * 1024
OBSERVATION_CLOCK = "RETAINED_PROVENANCE_RETRIEVED_AT_UNVERIFIED"
MAX_RECORDS = 10000
# Declared, unvalidated: a run retains its universe soon after collecting it.
MAX_OBSERVATION_LAG = timedelta(hours=6)
STORE_REASONS = (
    "CONFIG_INVALID", "DIRECTORY_UNAVAILABLE", "DIRECTORY_UNSAFE", "STORE_BUSY",
    "RECORD_REJECTED", "STORE_INVALID", "STORE_FULL", "OBSERVED_IN_FUTURE",
    "OBSERVATION_BACKDATED", "PROVENANCE_CONFLICT", "CONTENT_CONFLICT", "WRITE_FAILED",
    "RETENTION_UNVERIFIED")
SUMMARY_FIELDS = frozenset((
    "version", "size", "eligible", "scanner_rows", "master_symbols", "excluded",
    "floor_avg_dollar_volume_30d", "rule", "provenance"))
EXCLUDED_FIELDS = frozenset(("NOT_IN_MASTER", "ETF_IN_MASTER"))
PROVENANCE_SOURCES = frozenset((
    ("nasdaqtrader", NASDAQ_LISTED_URL),
    ("nasdaqtrader", OTHER_LISTED_URL),
    ("tradingview_scanner", SCANNER_URL)))
PROVENANCE_REQUIRED = frozenset(("source_id", "url", "raw_sha256", "retrieved_at"))
PROVENANCE_OPTIONAL = frozenset(("last_modified", "etag", "date"))
_HEX64 = re.compile(r"[0-9a-f]{64}")
_INTEGER = re.compile(r"(0|[1-9][0-9]*)")
_RECORD_NAME = re.compile(r"([0-9a-f]{64})\.json")
# Leftover temporary files of an interrupted write (``_write_temp`` naming).
_TEMP_NAME = re.compile(r"\.publish-[0-9]+-[0-9a-f]{16}\.tmp")
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class RetainedUsUniverseError(ValueError):
    """Retained universe bytes failed closed; nothing from them may be used."""


class UsUniverseStoreError(RuntimeError):
    """Retention or load refused; ``reason`` is one of STORE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class RetainedUsUniverse:
    """One retained US-UNIVERSE-v1 summary and its recorded observation time."""

    identity: str
    observed_at: datetime
    summary: dict = field(repr=False)


def _fail(reason):
    raise RetainedUsUniverseError(reason)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail("duplicate JSON key")
        out[key] = value
    return out


def _constant(name):
    _fail("non-finite JSON number")


def canonical_bytes(summary) -> bytes:
    return json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _count(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        _fail(f"{name} must be an int >= {minimum}")
    return value


def _retrieved_at(value):
    if type(value) is not str:
        _fail("retrieved_at must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("retrieved_at is not ISO 8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("retrieved_at must be timezone-aware")
    return parsed


def _check_provenance(records) -> list:
    if type(records) is not list or len(records) != len(PROVENANCE_SOURCES):
        _fail(f"provenance must be a list of {len(PROVENANCE_SOURCES)} records")
    seen, times = set(), []
    for record in records:
        if type(record) is not dict or not PROVENANCE_REQUIRED <= set(record) <= (
                PROVENANCE_REQUIRED | PROVENANCE_OPTIONAL):
            _fail("provenance record field set mismatch")
        if any(type(record[key]) is not str for key in record):
            _fail("provenance values must be strings")
        source = (record["source_id"], record["url"])
        if source not in PROVENANCE_SOURCES or source in seen:
            _fail(f"unexpected or duplicate provenance source {source!r}")
        seen.add(source)
        if _HEX64.fullmatch(record["raw_sha256"]) is None:
            _fail("raw_sha256 must be a lowercase SHA-256 hex digest")
        times.append(_retrieved_at(record["retrieved_at"]))
    return times


def _check_summary(summary) -> datetime:
    """Structural gate; returns the observation time (latest retrieval)."""
    if type(summary) is not dict or set(summary) != SUMMARY_FIELDS:
        _fail("retained universe field set mismatch")
    if summary["version"] != UNIVERSE_VERSION:
        _fail(f"retained universe must be {UNIVERSE_VERSION}")
    _count(summary["size"], "size", 1)
    _count(summary["master_symbols"], "master_symbols", 1)
    eligible = _count(summary["eligible"], "eligible")
    rows = _count(summary["scanner_rows"], "scanner_rows")
    excluded = summary["excluded"]
    if type(excluded) is not dict or set(excluded) != EXCLUDED_FIELDS:
        _fail("excluded field set mismatch")
    if eligible + sum(_count(excluded[key], f"excluded {key}") for key in sorted(excluded)) != rows:
        _fail("eligible plus excluded must equal scanner_rows")
    floor = summary["floor_avg_dollar_volume_30d"]
    if eligible == 0:
        if floor is not None:
            _fail("floor must be null when nothing is eligible")
    elif type(floor) is not str or _INTEGER.fullmatch(floor) is None:
        _fail("floor must be a non-negative integer string when members exist")
    if type(summary["rule"]) is not str or not summary["rule"]:
        _fail("rule must be a non-empty string")
    return max(_check_provenance(summary["provenance"]))


def read_retained_us_universe(payload, *, expected_sha256) -> RetainedUsUniverse:
    """Validate exact retained bytes; return the summary and its observation time."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        _fail("retained universe must be bounded bytes")
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        _fail("invalid expected_sha256")
    if sha256(payload).hexdigest() != expected_sha256:
        _fail("retained universe hash mismatch")
    try:
        summary = json.loads(payload.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail("retained universe is not strict JSON")
    except ValueError as exc:  # duplicate key or non-finite number from the hooks
        _fail(str(exc))
    if type(summary) is not dict:
        _fail("retained universe is not a JSON object")
    observed_at = _check_summary(summary)
    if canonical_bytes(summary) != payload:
        _fail("retained universe is not canonical")
    return RetainedUsUniverse(identity=expected_sha256, observed_at=observed_at, summary=summary)


def _open_directory(directory):
    """Absolute, non-symlink directory that is not group/world-writable."""
    if type(directory) is not str or not os.path.isabs(directory) or "\x00" in directory:
        raise UsUniverseStoreError("CONFIG_INVALID")
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC)
    except OSError:
        raise UsUniverseStoreError("DIRECTORY_UNAVAILABLE") from None
    try:
        info = os.fstat(dir_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise UsUniverseStoreError("DIRECTORY_UNSAFE")
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def _write_once(dir_fd, identity, payload):
    """Create ``<identity>.json`` without overwriting; existing bytes must match."""
    name = f"{identity}.json"
    temp = _write_temp(dir_fd, payload)
    try:
        try:
            os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
        except FileExistsError:
            try:
                existing = _read_regular(dir_fd, name, MAX_BYTES, "CONTENT_CONFLICT")
            except _Unavailable:
                raise UsUniverseStoreError("CONTENT_CONFLICT") from None
            if existing != payload:
                raise UsUniverseStoreError("CONTENT_CONFLICT")
    finally:
        _unlink_quietly(dir_fd, temp)
    _fsync_dir(dir_fd)


def _read_record(dir_fd, identity):
    try:
        payload = _read_regular(dir_fd, f"{identity}.json", MAX_BYTES, "RECORD_UNREADABLE")
    except _Unavailable as exc:
        raise UsUniverseStoreError("STORE_INVALID", exc.reason) from None
    try:
        return payload, read_retained_us_universe(payload, expected_sha256=identity)
    except (RetainedUsUniverseError, RecursionError) as exc:
        raise UsUniverseStoreError("STORE_INVALID", str(exc)) from None


def _scan(dir_fd) -> dict:
    """Every retained record keyed by identity; unexpected entries fail closed."""
    try:
        names = os.listdir(dir_fd)
    except OSError:
        raise UsUniverseStoreError("DIRECTORY_UNAVAILABLE") from None
    identities = []
    for name in names:
        match = _RECORD_NAME.fullmatch(name)
        if match is not None:
            identities.append(match.group(1))
        elif _TEMP_NAME.fullmatch(name) is None:
            raise UsUniverseStoreError("STORE_INVALID", "unexpected directory entry")
    if len(identities) > MAX_RECORDS:
        raise UsUniverseStoreError("STORE_INVALID", "too many records")
    return {identity: _read_record(dir_fd, identity)[1] for identity in sorted(identities)}


def load_retained_us_universes(directory) -> tuple[RetainedUsUniverse, ...]:
    """Every retained summary, ordered by (observed_at, identity).

    Fails closed (``UsUniverseStoreError``) if any entry in the directory is
    damaged or unexpected; partial content is never returned. Callers must
    still select a baseline by their own explicit ``as_of`` and policy window;
    an empty result is not proof that no earlier universe existed.
    """
    dir_fd = _open_directory(directory)
    try:
        records = _scan(dir_fd)
    finally:
        os.close(dir_fd)
    return tuple(sorted(records.values(), key=lambda held: (held.observed_at, held.identity)))


def _aware(now):
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError("now must be a timezone-aware datetime")
    return now


def _slot(summary):
    return canonical_bytes(summary["provenance"]), summary["size"]


def _result(status, record, retained_count):
    return {
        "schema_version": RESULT_SCHEMA, "status": status, "identity": record.identity,
        "universe_version": UNIVERSE_VERSION, "size": record.summary["size"],
        "observed_at": record.observed_at.isoformat(), "observation_clock": OBSERVATION_CLOCK,
        "members_retained": False, "retained_count": retained_count,
        "membership_interpretation": "NOT_ESTABLISHED", "trading_eligibility": "NOT_ESTABLISHED",
        "trading_effect": "NONE", "mode": "SHADOW", "live_money": False,
        "execution_allowed": False, "runtime_activation": False, "promotion": "NOT_AUTHORIZED"}


def retain_us_universe(directory, universe, *, now=None):
    """Retain one US-UNIVERSE-v1 summary write-once in a host-provisioned directory.

    ``universe`` is the ``collect_universe`` document (``members`` is dropped)
    or its member-less summary; ``now`` is the store clock (defaults to real
    UTC). Returns a ``us-universe-retention-v1`` result with status RETAINED or
    ALREADY_RETAINED. Any refusal raises ``UsUniverseStoreError``; refusals
    before the write leave the directory unchanged.
    """
    now = _aware(now)
    if type(universe) is not dict:
        raise UsUniverseStoreError("RECORD_REJECTED", "universe must be a dict")
    summary = {key: value for key, value in universe.items() if key != "members"}
    try:
        payload = canonical_bytes(summary)
        digest = sha256(payload).hexdigest()
        accepted = read_retained_us_universe(payload, expected_sha256=digest)
    except (ValueError, TypeError, RecursionError) as exc:
        raise UsUniverseStoreError("RECORD_REJECTED", type(exc).__name__) from None

    dir_fd = _open_directory(directory)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UsUniverseStoreError("STORE_BUSY") from None

        retained = _scan(dir_fd)
        if digest in retained:
            return _result("ALREADY_RETAINED", retained[digest], len(retained))

        retrieved = [_retrieved_at(record["retrieved_at"]) for record in accepted.summary["provenance"]]
        if max(retrieved) > now:
            raise UsUniverseStoreError("OBSERVED_IN_FUTURE")
        if now - min(retrieved) > MAX_OBSERVATION_LAG:
            raise UsUniverseStoreError("OBSERVATION_BACKDATED")
        if any(_slot(held.summary) == _slot(accepted.summary) for held in retained.values()):
            raise UsUniverseStoreError("PROVENANCE_CONFLICT")
        if len(retained) >= MAX_RECORDS:
            raise UsUniverseStoreError("STORE_FULL")

        try:
            _write_once(dir_fd, digest, payload)
        except OSError as exc:
            raise UsUniverseStoreError("WRITE_FAILED", type(exc).__name__) from None

        try:
            stored, verified = _read_record(dir_fd, digest)
        except UsUniverseStoreError as exc:
            raise UsUniverseStoreError("RETENTION_UNVERIFIED", exc.detail) from None
        if stored != payload or verified.summary != accepted.summary:
            raise UsUniverseStoreError("RETENTION_UNVERIFIED", "retained bytes differ")
        return _result("RETAINED", verified, len(retained) + 1)
    finally:
        os.close(dir_fd)
