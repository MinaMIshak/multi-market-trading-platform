import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.context.fetch import provenance, store_raw
from app.us.new_listing_baseline import (DIRECTORY_URLS, OBSERVATION_CLOCK, VERSION, RetainedDirectoryMaster,
                                         load_retained_directory_master)
from app.us.new_listings import build_new_listing_admissions
from app.us.universe import (NASDAQ_LISTED_URL, OTHER_LISTED_URL, UniverseError, build_universe, parse_master,
                             parse_scanner)

RETRIEVED = datetime(2026, 10, 21, 4, 15, 3, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 20, 4, 15, tzinfo=timezone.utc)


def texts(extra=()):
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    nasdaq += "".join(f"N{i}|Nasdaq Co {i}|Q|N|N|100|N|N\n" for i in range(3200))
    nasdaq += "".join(f"{symbol}|{symbol} Corp|Q|N|N|100|N|N\n" for symbol in extra)
    nasdaq += "File Creation Time: 1021202604:00|||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    other += "JPM|JPMorgan Chase|N|JPM|N|100|N|JPM\n"
    other += "File Creation Time: 1021202604:00||||||\n"
    return nasdaq.encode(), other.encode()


def retain(root, payloads=None, *, retrieved=(RETRIEVED, RETRIEVED + timedelta(seconds=1)), master_symbols=None):
    payloads = texts() if payloads is None else payloads
    records = []
    for url, payload, moment in zip(DIRECTORY_URLS, payloads, retrieved):
        digest, _ = store_raw(root, "nasdaqtrader", payload, "txt")
        records.append(provenance("nasdaqtrader", url, digest, retrieved_at=moment))
    records.append(provenance("tradingview_scanner", "https://scanner.tradingview.com/america/scan", "0" * 64,
                              retrieved_at=retrieved[0]))
    count = len(parse_master(*(p.decode() for p in payloads))) if master_symbols is None else master_symbols
    return {"version": "US-UNIVERSE-v1", "members": [], "master_symbols": count, "provenance": records}


def test_reconstructs_the_parsed_master_from_retained_bytes(tmp_path):
    universe = retain(tmp_path)
    result = load_retained_directory_master(tmp_path, universe, as_of=AS_OF)
    assert type(result) is RetainedDirectoryMaster
    nasdaq, other = texts()
    assert result.master == parse_master(nasdaq.decode(), other.decode())
    # The master is complete only once both files were retrieved.
    assert result.observed_at == RETRIEVED + timedelta(seconds=1)
    evidence = result.evidence
    assert evidence["version"] == VERSION and evidence["observation_clock"] == OBSERVATION_CLOCK
    assert [f["url"] for f in evidence["files"]] == [NASDAQ_LISTED_URL, OTHER_LISTED_URL]
    assert evidence["files"][0]["raw_sha256"] == hashlib.sha256(nasdaq).hexdigest()
    assert evidence["master_symbols"] == len(result.master) == universe["master_symbols"]
    assert evidence["membership_interpretation"] == "NOT_ESTABLISHED"
    assert evidence["trading_effect"] == "NONE" and evidence["mode"] == "SHADOW" and evidence["live_money"] is False
    json.dumps(evidence)


def test_provenance_order_does_not_matter_and_string_root_is_accepted(tmp_path):
    universe = retain(tmp_path)
    universe["provenance"].reverse()
    result = load_retained_directory_master(str(tmp_path), universe, as_of=AS_OF)
    assert [f["url"] for f in result.evidence["files"]] == list(DIRECTORY_URLS)


def test_retained_baseline_feeds_the_unchanged_new_listing_builder(tmp_path):
    baseline = load_retained_directory_master(tmp_path, retain(tmp_path), as_of=AS_OF)
    nasdaq, other = texts(extra=("NEWCO",))
    current = parse_master(nasdaq.decode(), other.decode())
    filler = [(f"N{i}", "NASDAQ", 1000 + i, 1) for i in range(1100)]
    rows = parse_scanner(json.dumps({"data": [
        {"s": f"{ex}:{t}", "d": [t, f"{t} Inc", ex, "stock", "common", vol, close, 1e9, "Tech", None]}
        for t, ex, vol, close in [("NEWCO", "NASDAQ", 100000, 20)] + filler]}).encode())
    # Keep NEWCO out of the normal universe so it reaches the separate new-listing population.
    normal = build_universe(current, [row for row in rows if row["ticker"] != "NEWCO"], size=2)
    result = build_new_listing_admissions(baseline.master, current, rows, normal,
                                          baseline_observed_at=baseline.observed_at, current_observed_at=AS_OF)
    assert [m["ticker"] for m in result["members"]] == ["NEWCO"]


def test_tampered_retained_bytes_are_refused(tmp_path):
    universe = retain(tmp_path)
    digest = universe["provenance"][0]["raw_sha256"]
    (tmp_path / "context" / "raw" / "nasdaqtrader" / f"{digest}.txt").write_bytes(b"tampered")
    with pytest.raises(UniverseError, match="does not match its recorded digest"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)


def test_missing_and_symlinked_retained_files_are_refused(tmp_path):
    universe = retain(tmp_path)
    path = tmp_path / "context" / "raw" / "nasdaqtrader" / f"{universe['provenance'][1]['raw_sha256']}.txt"
    moved = tmp_path / "elsewhere.txt"
    path.rename(moved)
    with pytest.raises(UniverseError, match="missing or not a regular file"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)
    path.symlink_to(moved)
    with pytest.raises(UniverseError, match="missing or not a regular file"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)


@pytest.mark.parametrize("digest", ["../../../etc/passwd", "A" * 64, "0" * 63, None])
def test_malformed_digests_are_refused_before_any_read(tmp_path, digest):
    universe = retain(tmp_path)
    universe["provenance"][0]["raw_sha256"] = digest
    with pytest.raises(UniverseError, match="lowercase SHA-256"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)


def test_evidence_retrieved_after_as_of_is_refused(tmp_path):
    universe = retain(tmp_path)
    with pytest.raises(UniverseError, match="after as_of"):
        load_retained_directory_master(tmp_path, universe, as_of=RETRIEVED)
    # Exactly at the later retrieval is allowed.
    load_retained_directory_master(tmp_path, universe, as_of=RETRIEVED + timedelta(seconds=1))


def test_retrievals_further_apart_than_declared_skew_are_refused(tmp_path):
    universe = retain(tmp_path, retrieved=(RETRIEVED, RETRIEVED + timedelta(seconds=901)))
    with pytest.raises(UniverseError, match="declared skew"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)
    load_retained_directory_master(tmp_path, universe, as_of=AS_OF, max_retrieval_skew_seconds=901)


def test_master_size_mismatch_is_refused(tmp_path):
    universe = retain(tmp_path, master_symbols=12)
    with pytest.raises(UniverseError, match="universe recorded 12"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)


@pytest.mark.parametrize("mutate, message", [
    (lambda u: u["provenance"].pop(0), "lacks nasdaqtrader provenance"),
    (lambda u: u["provenance"].append(dict(u["provenance"][0])), "duplicate nasdaqtrader provenance"),
    (lambda u: u["provenance"][0].update(url="https://example.com/x.txt"), "unexpected nasdaqtrader provenance url"),
    (lambda u: u.update(version="US-UNIVERSE-v0"), "US-UNIVERSE-v1 document"),
    (lambda u: u.update(provenance="nope"), "list of records"),
    (lambda u: u.update(master_symbols="3201"), "master_symbols must be a positive int"),
    (lambda u: u["provenance"][0].update(retrieved_at="2026-10-21T04:15:03"), "aware datetime"),
    (lambda u: u["provenance"][0].update(retrieved_at="yesterday"), "not ISO 8601"),
])
def test_malformed_universe_documents_are_refused(tmp_path, mutate, message):
    universe = retain(tmp_path)
    mutate(universe)
    with pytest.raises(UniverseError, match=message):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF)


def test_naive_as_of_relative_root_and_bad_skew_are_refused(tmp_path):
    universe = retain(tmp_path)
    with pytest.raises(UniverseError, match="as_of must be an aware datetime"):
        load_retained_directory_master(tmp_path, universe, as_of=AS_OF.replace(tzinfo=None))
    with pytest.raises(UniverseError, match="data_root must be absolute"):
        load_retained_directory_master("relative/root", universe, as_of=AS_OF)
    with pytest.raises(UniverseError, match="data_root must be a str or Path"):
        load_retained_directory_master(None, universe, as_of=AS_OF)
    for skew in (-1, 86401, True, 1.5):
        with pytest.raises(UniverseError, match="max_retrieval_skew_seconds"):
            load_retained_directory_master(tmp_path, universe, as_of=AS_OF, max_retrieval_skew_seconds=skew)
