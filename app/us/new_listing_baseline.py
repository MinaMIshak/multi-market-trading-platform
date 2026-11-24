"""Retained Nasdaq Trader directory baseline for US-NEW-LISTING-v1 (us-directory-baseline-v1).

``app.us.new_listings.build_new_listing_admissions`` needs a *baseline* master
observed a policy window earlier. ``app.us_run.collect_universe`` already
retains each directory file content-addressed (``store_raw`` under
``<data_root>/context/raw/nasdaqtrader/<sha256>.txt``) and records its
provenance (URL, ``raw_sha256``, ``retrieved_at``) in the US-UNIVERSE-v1
document. This module re-reads exactly those retained bytes for one earlier
universe document and reconstructs the master the run parsed, so the baseline
comes from retained evidence instead of an unverifiable caller-supplied dict.

Read-only and fail closed. It refuses when:

* the universe document is not US-UNIVERSE-v1 or lacks exactly one
  ``nasdaqtrader`` provenance record for each directory URL;
* a digest is malformed, the retained file is missing, not a regular file, a
  symlink, or its bytes do not hash to the recorded digest;
* a retrieval time is naive, unparseable, after the caller's ``as_of`` (no
  look-ahead) or the two retrievals are further apart than the declared skew;
* the re-parsed master size differs from the recorded ``master_symbols``.

The observation time is the later of the two recorded retrievals (the master
is complete only once both files were retrieved). That clock is the run's own
recorded provenance, not independently verified, and is labelled so. Bytes are
decoded exactly as ``collect_universe`` decodes them. No network, writes,
store, job or caller; ``app.us.universe`` and ``app.us.new_listings`` are
unchanged. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.us.universe import NASDAQ_LISTED_URL, OTHER_LISTED_URL, UniverseError, parse_master
from app.us.universe import VERSION as NORMAL_UNIVERSE_VERSION

VERSION = "us-directory-baseline-v1"
SOURCE_ID = "nasdaqtrader"
DIRECTORY_URLS = (NASDAQ_LISTED_URL, OTHER_LISTED_URL)
DEFAULT_MAX_RETRIEVAL_SKEW_SECONDS = 900
DECODE_BASIS = "UTF8_REPLACE_AS_COLLECT_UNIVERSE"
OBSERVATION_CLOCK = "RETAINED_PROVENANCE_RETRIEVED_AT_UNVERIFIED"
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RetainedDirectoryMaster:
    """A baseline master reconstructed from retained directory bytes."""

    master: dict = field(repr=False)
    observed_at: datetime
    evidence: dict


def _aware(value, label: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise UniverseError(f"{label} must be an aware datetime")
    return value


def _retrieved_at(record: dict, url: str) -> datetime:
    text = record.get("retrieved_at")
    if type(text) is not str:
        raise UniverseError(f"retrieved_at for {url} must be an ISO string")
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise UniverseError(f"retrieved_at for {url} is not ISO 8601") from None
    return _aware(value, f"retrieved_at for {url}")


def _directory_records(universe) -> list:
    if type(universe) is not dict or universe.get("version") != NORMAL_UNIVERSE_VERSION:
        raise UniverseError(f"baseline universe must be a {NORMAL_UNIVERSE_VERSION} document")
    records = universe.get("provenance")
    if type(records) is not list or any(type(record) is not dict for record in records):
        raise UniverseError("baseline universe provenance must be a list of records")
    directory = [record for record in records if record.get("source_id") == SOURCE_ID]
    by_url = {}
    for record in directory:
        url = record.get("url")
        if url not in DIRECTORY_URLS:
            raise UniverseError(f"unexpected {SOURCE_ID} provenance url {url!r}")
        if url in by_url:
            raise UniverseError(f"duplicate {SOURCE_ID} provenance for {url}")
        by_url[url] = record
    missing = [url for url in DIRECTORY_URLS if url not in by_url]
    if missing:
        raise UniverseError(f"baseline universe lacks {SOURCE_ID} provenance for {missing}")
    return [by_url[url] for url in DIRECTORY_URLS]


def _read_retained(raw_folder: Path, digest, url: str) -> bytes:
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        raise UniverseError(f"raw_sha256 for {url} is not a lowercase SHA-256 hex digest")
    path = raw_folder / f"{digest}.txt"
    if path.is_symlink() or not path.is_file():
        raise UniverseError(f"retained directory file for {url} is missing or not a regular file")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise UniverseError(f"retained directory file for {url} does not match its recorded digest")
    return payload


def load_retained_directory_master(data_root, universe: dict, *, as_of: datetime,
                                   max_retrieval_skew_seconds: int = DEFAULT_MAX_RETRIEVAL_SKEW_SECONDS
                                   ) -> RetainedDirectoryMaster:
    """Reconstruct the Nasdaq Trader master retained for one US-UNIVERSE-v1 document.

    ``data_root`` is the absolute US data root the run stored raw evidence in;
    ``universe`` is the retained universe document (with ``provenance``);
    ``as_of`` is the aware decision time: evidence retrieved after it is refused.
    """
    as_of = _aware(as_of, "as_of")
    if type(max_retrieval_skew_seconds) is not int or not 0 <= max_retrieval_skew_seconds <= 86400:
        raise UniverseError("max_retrieval_skew_seconds must be an int in [0, 86400]")
    if not isinstance(data_root, (str, Path)):
        raise UniverseError("data_root must be a str or Path")
    root = Path(data_root)
    if not root.is_absolute():
        raise UniverseError("data_root must be absolute")
    records = _directory_records(universe)
    recorded_symbols = universe.get("master_symbols")
    if type(recorded_symbols) is not int or recorded_symbols < 1:
        raise UniverseError("baseline universe master_symbols must be a positive int")

    retrieved = [_retrieved_at(record, record["url"]) for record in records]
    if any(moment > as_of for moment in retrieved):
        raise UniverseError("baseline directory evidence was retrieved after as_of")
    if abs((retrieved[0] - retrieved[1]).total_seconds()) > max_retrieval_skew_seconds:
        raise UniverseError("baseline directory retrievals are further apart than the declared skew")

    raw_folder = root / "context" / "raw" / SOURCE_ID
    payloads = [_read_retained(raw_folder, record.get("raw_sha256"), record["url"]) for record in records]
    master = parse_master(*(payload.decode("utf-8", "replace") for payload in payloads))
    if len(master) != recorded_symbols:
        raise UniverseError(f"retained directory master has {len(master)} symbols, universe recorded "
                            f"{recorded_symbols}")

    observed_at = max(retrieved)
    evidence = {
        "version": VERSION,
        "universe_version": NORMAL_UNIVERSE_VERSION,
        "source_id": SOURCE_ID,
        "files": [{"url": record["url"], "raw_sha256": record["raw_sha256"],
                   "retrieved_at": moment.isoformat()} for record, moment in zip(records, retrieved)],
        "master_symbols": len(master),
        "observed_at": observed_at.isoformat(),
        "as_of": as_of.isoformat(),
        "max_retrieval_skew_seconds": max_retrieval_skew_seconds,
        "observation_clock": OBSERVATION_CLOCK,
        "decode_basis": DECODE_BASIS,
        "membership_interpretation": "NOT_ESTABLISHED",
        "trading_effect": "NONE",
        "mode": "SHADOW",
        "live_money": False,
    }
    return RetainedDirectoryMaster(master=master, observed_at=observed_at, evidence=evidence)
