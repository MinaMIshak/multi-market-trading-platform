import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.context.fetch import provenance, store_raw
from app.us.new_listing_baseline import DIRECTORY_URLS
from app.us.new_listing_retained import OBSERVATION_CLOCK, VERSION, build_retained_new_listing_admissions
from app.us.new_listings import VERSION as NEW_LISTING_VERSION
from app.us.new_listings import NewListingPolicy
from app.us.universe import SCANNER_URL, UniverseError, build_universe, parse_master, parse_scanner

BASELINE_AT = datetime(2026, 10, 21, 4, 15, tzinfo=timezone.utc)
CURRENT_AT = datetime(2026, 11, 20, 4, 15, tzinfo=timezone.utc)
AS_OF = CURRENT_AT + timedelta(hours=1)


def directory(extra=()):
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    nasdaq += "".join(f"N{i}|Nasdaq Co {i}|Q|N|N|100|N|N\n" for i in range(3200))
    nasdaq += "".join(f"{symbol}|{symbol} Corp|Q|N|N|100|N|N\n" for symbol in extra)
    nasdaq += "File Creation Time: 1021202604:00|||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    other += "JPM|JPMorgan Chase|N|JPM|N|100|N|JPM\n"
    other += "File Creation Time: 1021202604:00||||||\n"
    return nasdaq.encode(), other.encode()


def scanner(extra=()):
    # Filler dollar volume 1,000,000..2,099,000, so 99 normal names out-rank NEWCO (2,000,000).
    filler = [(f"N{i}", "NASDAQ", (1000 + i) * 1000, 1) for i in range(1100)]
    rows = [(t, "NASDAQ", vol, close) for t, vol, close in extra] + filler
    return json.dumps({"data": [
        {"s": f"{ex}:{t}", "d": [t, f"{t} Inc", ex, "stock", "common", vol, close, 1e9, "Tech", None]}
        for t, ex, vol, close in rows]}).encode()


def retain(root, at, *, extra=(), scanner_extra=(), size=5, scanner_at=None):
    """Store raw evidence exactly as collect_universe does and return its universe document."""
    payloads = directory(extra)
    records = []
    for offset, (url, payload) in enumerate(zip(DIRECTORY_URLS, payloads)):
        digest, _ = store_raw(root, "nasdaqtrader", payload, "txt")
        records.append(provenance("nasdaqtrader", url, digest, retrieved_at=at + timedelta(seconds=offset)))
    payload = scanner(scanner_extra)
    digest, _ = store_raw(root, "tradingview_scanner", payload, "json")
    records.append(provenance("tradingview_scanner", SCANNER_URL, digest,
                              retrieved_at=scanner_at or at + timedelta(seconds=2)))
    universe = build_universe(parse_master(*(p.decode() for p in payloads)), parse_scanner(payload), size=size)
    universe["provenance"] = records
    return universe


def summary(universe):
    """The member-less universe summary us_run writes into us-ranking.json, after a JSON round trip."""
    return json.loads(json.dumps({k: v for k, v in universe.items() if k != "members"}, default=str))


def pair(root, **options):
    baseline = retain(root, BASELINE_AT)
    current = retain(root, CURRENT_AT, extra=("NEWCO", "TINY"),
                     scanner_extra=(("NEWCO", 100000, 20), ("TINY", 10, 1)), **options)
    return baseline, current


def test_new_listing_population_is_rebuilt_from_retained_evidence_only(tmp_path):
    baseline, current = pair(tmp_path)
    result = build_retained_new_listing_admissions(tmp_path, summary(baseline), summary(current), as_of=AS_OF)
    assert result["version"] == VERSION and result["observation_clock"] == OBSERVATION_CLOCK
    admissions = result["admissions"]
    assert admissions["version"] == NEW_LISTING_VERSION
    # NEWCO is liquid enough but outside the top-5 normal universe; TINY is below the floor.
    assert [m["ticker"] for m in admissions["members"]] == ["NEWCO"]
    assert admissions["excluded"]["BELOW_NEW_LISTING_LIQUIDITY_FLOOR"] == 1
    assert admissions["new_to_directory"] == 2
    # Clocks come from retained provenance: later directory retrieval / latest current retrieval.
    assert result["baseline_observed_at"] == (BASELINE_AT + timedelta(seconds=1)).isoformat()
    assert result["current_observed_at"] == (CURRENT_AT + timedelta(seconds=2)).isoformat()
    assert admissions["baseline_observed_at"] == result["baseline_observed_at"]
    assert admissions["current_observed_at"] == result["current_observed_at"]
    assert result["current_scanner"]["raw_sha256"] == current["provenance"][2]["raw_sha256"]
    assert result["current_scanner"]["scanner_rows"] == current["scanner_rows"]
    assert "members" not in result["normal_universe_reproduced"]
    assert result["trading_eligibility"] == "NOT_ESTABLISHED" and result["trading_effect"] == "NONE"
    assert result["mode"] == "SHADOW" and result["live_money"] is False
    json.dumps(result)


def test_full_documents_with_members_are_reproduced_and_normal_membership_wins(tmp_path):
    baseline, current = pair(tmp_path, size=1102)
    result = build_retained_new_listing_admissions(str(tmp_path), baseline, current, as_of=AS_OF)
    assert "members" in result["normal_universe_reproduced"]
    # With a universe large enough to contain NEWCO, it transitions to the normal population.
    assert result["admissions"]["members"] == []
    assert "NEWCO" in result["admissions"]["in_normal_universe"]


def test_recorded_universe_that_retained_bytes_do_not_reproduce_is_refused(tmp_path):
    baseline, current = pair(tmp_path)
    for name, value in (("eligible", current["eligible"] + 1), ("floor_avg_dollar_volume_30d", "1"),
                        ("excluded", {"NOT_IN_MASTER": 99, "ETF_IN_MASTER": 0})):
        altered = {**summary(current), name: value}
        with pytest.raises(UniverseError, match=f"normal universe {name}"):
            build_retained_new_listing_admissions(tmp_path, baseline, altered, as_of=AS_OF)
    reordered = {**current, "members": list(reversed(current["members"]))}
    with pytest.raises(UniverseError, match="normal universe members"):
        build_retained_new_listing_admissions(tmp_path, baseline, reordered, as_of=AS_OF)
    with pytest.raises(UniverseError, match="size must be a positive int"):
        build_retained_new_listing_admissions(tmp_path, baseline, {**current, "size": "5"}, as_of=AS_OF)


def test_tampered_missing_or_malformed_scanner_evidence_is_refused(tmp_path):
    baseline, current = pair(tmp_path)
    digest = current["provenance"][2]["raw_sha256"]
    path = tmp_path / "context" / "raw" / "tradingview_scanner" / f"{digest}.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(UniverseError, match="scanner file does not match"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF)
    path.unlink()
    with pytest.raises(UniverseError, match="scanner file is missing"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF)
    path.write_bytes(original)
    for bad in ("../../etc/passwd", "A" * 64, None):
        altered = json.loads(json.dumps(current, default=str))
        altered["provenance"][2]["raw_sha256"] = bad
        with pytest.raises(UniverseError, match="lowercase SHA-256"):
            build_retained_new_listing_admissions(tmp_path, baseline, altered, as_of=AS_OF)


@pytest.mark.parametrize("mutate, message", [
    (lambda u: u["provenance"].pop(2), "exactly one tradingview_scanner"),
    (lambda u: u["provenance"].append(dict(u["provenance"][2])), "exactly one tradingview_scanner"),
    (lambda u: u["provenance"][2].update(url="https://example.com/scan"), "unexpected tradingview_scanner"),
    (lambda u: u["provenance"][2].update(retrieved_at="2026-11-20T04:15:02"), "aware datetime"),
    (lambda u: u["provenance"][2].update(retrieved_at="today"), "not ISO 8601"),
    (lambda u: u.update(scanner_rows=7), "universe recorded 7"),
])
def test_malformed_scanner_provenance_is_refused(tmp_path, mutate, message):
    baseline, current = pair(tmp_path)
    current = json.loads(json.dumps(current, default=str))
    mutate(current)
    with pytest.raises(UniverseError, match=message):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF)


def test_scanner_after_as_of_or_outside_skew_is_refused(tmp_path):
    # Current directory observation is CURRENT_AT + 1s, so the scanner is 901s away.
    baseline, current = pair(tmp_path, scanner_at=CURRENT_AT + timedelta(seconds=902))
    with pytest.raises(UniverseError, match="further apart than the declared skew"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF)
    build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF, max_retrieval_skew_seconds=901)
    with pytest.raises(UniverseError, match="scanner evidence was retrieved after as_of"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=CURRENT_AT + timedelta(seconds=1),
                                              max_retrieval_skew_seconds=901)


def test_directory_look_ahead_and_out_of_window_baseline_are_refused(tmp_path):
    baseline, current = pair(tmp_path)
    with pytest.raises(UniverseError, match="retrieved after as_of"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=CURRENT_AT)
    too_recent = retain(tmp_path, CURRENT_AT - timedelta(days=3))
    with pytest.raises(UniverseError, match="outside the declared new-listing window"):
        build_retained_new_listing_admissions(tmp_path, too_recent, current, as_of=AS_OF)


def test_policy_is_passed_unchanged_to_the_builder(tmp_path):
    baseline, current = pair(tmp_path)
    policy = NewListingPolicy(min_avg_dollar_volume=Decimal("5"))
    result = build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF, policy=policy)
    assert [m["ticker"] for m in result["admissions"]["members"]] == ["NEWCO", "TINY"]
    with pytest.raises(UniverseError, match="policy must be a NewListingPolicy"):
        build_retained_new_listing_admissions(tmp_path, baseline, current, as_of=AS_OF, policy={})
