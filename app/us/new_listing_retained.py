"""US-NEW-LISTING-v1 from retained evidence only (us-retained-new-listing-v1).

``app.us.new_listings.build_new_listing_admissions`` is pure: it trusts the
masters, scanner rows, normal universe and observation clocks its caller
passes. ``app.us.new_listing_baseline.load_retained_directory_master`` rebuilds
one master from retained directory bytes. This module composes the two so that
*every* input is rebuilt from the bytes ``app.us_run.collect_universe`` already
stored content-addressed and the provenance it recorded in two US-UNIVERSE-v1
documents (a baseline and a current one; with or without ``members``, so the
member-less ``universe`` summary in ``us-ranking.json`` is accepted):

* baseline and current masters through the unchanged loader;
* current scanner rows from ``<data_root>/context/raw/tradingview_scanner/
  <sha256>.json`` (exactly one ``tradingview_scanner`` record for
  ``SCANNER_URL``; digest, regular file, byte hash and the recorded
  ``scanner_rows`` count checked; parsed with the unchanged ``parse_scanner``);
* the normal universe by re-running the unchanged ``build_universe`` with the
  recorded ``size`` and refusing unless every recorded summary field (and the
  member tickers, when recorded) is reproduced exactly.

The scanner retrieval must not be after ``as_of`` and must lie within the
declared skew of the current directory observation. The current observation
time is the latest of the three current retrievals. The builder's own document
is returned unchanged under ``admissions`` (its ``observation_clock`` still
says CALLER_SUPPLIED_UNVERIFIED because the builder cannot know); this wrapper
records that those clocks came from retained provenance, which is itself the
run's own unverified record.

Read-only and fail closed: no network, writes, store, job, route, scheduler or
``us_run`` caller. Admission still implies no trading eligibility. PAPER/SHADOW
only; LIVE_MONEY=false.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from app.us.new_listing_baseline import DEFAULT_MAX_RETRIEVAL_SKEW_SECONDS, load_retained_directory_master
from app.us.new_listings import build_new_listing_admissions
from app.us.universe import SCANNER_URL, UniverseError, build_universe, parse_scanner

VERSION = "us-retained-new-listing-v1"
SCANNER_SOURCE_ID = "tradingview_scanner"
OBSERVATION_CLOCK = "RETAINED_PROVENANCE_RETRIEVED_AT_UNVERIFIED"
REPRODUCED_FIELDS = ("version", "size", "eligible", "scanner_rows", "master_symbols", "excluded",
                     "floor_avg_dollar_volume_30d")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _scanner_record(universe) -> dict:
    records = universe.get("provenance") if type(universe) is dict else None
    if type(records) is not list or any(type(record) is not dict for record in records):
        raise UniverseError("current universe provenance must be a list of records")
    scanner = [record for record in records if record.get("source_id") == SCANNER_SOURCE_ID]
    if len(scanner) != 1:
        raise UniverseError(f"current universe must have exactly one {SCANNER_SOURCE_ID} provenance record")
    if scanner[0].get("url") != SCANNER_URL:
        raise UniverseError(f"unexpected {SCANNER_SOURCE_ID} provenance url {scanner[0].get('url')!r}")
    return scanner[0]


def _retrieved_at(record: dict) -> datetime:
    text = record.get("retrieved_at")
    if type(text) is not str:
        raise UniverseError("scanner retrieved_at must be an ISO string")
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise UniverseError("scanner retrieved_at is not ISO 8601") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise UniverseError("scanner retrieved_at must be an aware datetime")
    return value


def _read_scanner(data_root, digest) -> bytes:
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        raise UniverseError("scanner raw_sha256 is not a lowercase SHA-256 hex digest")
    path = Path(data_root) / "context" / "raw" / SCANNER_SOURCE_ID / f"{digest}.json"
    if path.is_symlink() or not path.is_file():
        raise UniverseError("retained scanner file is missing or not a regular file")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise UniverseError("retained scanner file does not match its recorded digest")
    return payload


def _check_reproduced(recorded: dict, rebuilt: dict) -> None:
    for name in REPRODUCED_FIELDS:
        if name not in recorded or recorded[name] != rebuilt[name]:
            raise UniverseError(f"retained evidence does not reproduce the recorded normal universe {name}")
    if "members" in recorded:
        members = recorded["members"]
        if type(members) is not list or any(type(member) is not dict for member in members):
            raise UniverseError("current universe members must be a list of records")
        if [member.get("ticker") for member in members] != [member["ticker"] for member in rebuilt["members"]]:
            raise UniverseError("retained evidence does not reproduce the recorded normal universe members")


def build_retained_new_listing_admissions(data_root, baseline_universe: dict, current_universe: dict, *,
                                          as_of: datetime, policy=None,
                                          max_retrieval_skew_seconds: int = DEFAULT_MAX_RETRIEVAL_SKEW_SECONDS
                                          ) -> dict:
    """Return US-NEW-LISTING-v1 rebuilt from retained evidence of two US-UNIVERSE-v1 documents.

    ``data_root`` is the absolute US data root; ``baseline_universe`` and
    ``current_universe`` are retained universe documents (or their member-less
    summaries); ``as_of`` is the aware decision time. ``policy`` is passed
    unchanged to the builder (``None`` = default us-new-listing-policy-v1).
    """
    # The loader validates as_of, data_root and the skew bound before any read.
    baseline = load_retained_directory_master(data_root, baseline_universe, as_of=as_of,
                                              max_retrieval_skew_seconds=max_retrieval_skew_seconds)
    current = load_retained_directory_master(data_root, current_universe, as_of=as_of,
                                             max_retrieval_skew_seconds=max_retrieval_skew_seconds)

    record = _scanner_record(current_universe)
    scanner_at = _retrieved_at(record)
    if scanner_at > as_of:
        raise UniverseError("current scanner evidence was retrieved after as_of")
    if abs((scanner_at - current.observed_at).total_seconds()) > max_retrieval_skew_seconds:
        raise UniverseError("scanner and directory retrievals are further apart than the declared skew")
    rows = parse_scanner(_read_scanner(data_root, record.get("raw_sha256")))
    if len(rows) != current_universe.get("scanner_rows"):
        raise UniverseError(f"retained scanner has {len(rows)} rows, universe recorded "
                            f"{current_universe.get('scanner_rows')!r}")

    size = current_universe.get("size")
    if type(size) is not int or size < 1:
        raise UniverseError("current universe size must be a positive int")
    normal = build_universe(current.master, rows, size=size)
    _check_reproduced(current_universe, normal)

    current_observed_at = max(current.observed_at, scanner_at)
    admissions = build_new_listing_admissions(baseline.master, current.master, rows, normal,
                                              baseline_observed_at=baseline.observed_at,
                                              current_observed_at=current_observed_at, policy=policy)
    return {
        "version": VERSION,
        "admissions": admissions,
        "baseline_directory": baseline.evidence,
        "current_directory": current.evidence,
        "current_scanner": {"source_id": SCANNER_SOURCE_ID, "url": SCANNER_URL,
                            "raw_sha256": record["raw_sha256"], "retrieved_at": scanner_at.isoformat(),
                            "scanner_rows": len(rows)},
        "normal_universe_reproduced": list(REPRODUCED_FIELDS) + (["members"] if "members" in current_universe
                                                                 else []),
        "baseline_observed_at": baseline.observed_at.isoformat(),
        "current_observed_at": current_observed_at.isoformat(),
        "as_of": as_of.isoformat(),
        "max_retrieval_skew_seconds": max_retrieval_skew_seconds,
        "observation_clock": OBSERVATION_CLOCK,
        "ipo_interpretation": "NOT_ESTABLISHED",
        "trading_eligibility": "NOT_ESTABLISHED",
        "trading_effect": "NONE",
        "mode": "SHADOW",
        "live_money": False,
    }
