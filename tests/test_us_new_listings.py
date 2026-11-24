import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.us.new_listings import (EXCLUSION_REASONS, POPULATION, NewListingPolicy, VERSION,
                                 build_new_listing_admissions)
from app.us.universe import UniverseError, build_universe, parse_master, parse_scanner

CURRENT_AT = datetime(2026, 11, 20, 4, 15, tzinfo=timezone.utc)
BASELINE_AT = CURRENT_AT - timedelta(days=30)


def master(extra=()):
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    nasdaq += "".join(f"N{i}|Nasdaq Co {i}|Q|N|N|100|N|N\n" for i in range(3200))
    nasdaq += "AAPL|Apple Inc. Common Stock|Q|N|N|100|N|N\n"
    nasdaq += "".join(f"{symbol}|{symbol} Corp|Q|{test}|N|100|{etf}|N\n" for symbol, etf, test in extra)
    nasdaq += "File Creation Time: 1120202604:00|||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    other += "JPM|JPMorgan Chase|N|JPM|N|100|N|JPM\n"
    other += "File Creation Time: 1120202604:00||||||\n"
    return parse_master(nasdaq, other)


NEW = (("BIGNEW", "N", "N"), ("NEWCO", "N", "N"), ("NEWETF", "Y", "N"), ("NOSCAN", "N", "N"),
       ("TINY", "N", "N"), ("TESTX", "N", "Y"))


def scanner(rows):
    filler = [(f"N{i}", "NASDAQ", 1000 + i, 1) for i in range(1100)]
    payload = json.dumps({"data": [
        {"s": f"{ex}:{t}", "d": [t, f"{t} Inc", ex, "stock", "common", vol, close, 1e9, "Tech", None]}
        for t, ex, vol, close in list(rows) + filler]}).encode()
    return parse_scanner(payload)


SCANNER = [("AAPL", "NASDAQ", 10000000, 300), ("BIGNEW", "NYSE", 100000000, 50), ("NEWCO", "NASDAQ", 100000, 20),
           ("TINY", "NASDAQ", 100, 5), ("NEWETF", "NASDAQ", 1000000, 10), ("GHOST", "NYSE", 900000000, 9),
           ("TESTX", "NASDAQ", 900000000, 9)]


def run(baseline=None, current=None, rows=None, normal=None, **kwargs):
    current = master(NEW) if current is None else current
    rows = scanner(SCANNER) if rows is None else rows
    normal = build_universe(current, rows, size=2) if normal is None else normal
    kwargs.setdefault("baseline_observed_at", BASELINE_AT)
    kwargs.setdefault("current_observed_at", CURRENT_AT)
    return build_new_listing_admissions(master() if baseline is None else baseline, current, rows, normal, **kwargs)


def test_new_listings_form_a_separate_counted_population():
    result = run()
    assert result["version"] == VERSION and result["population"] == POPULATION == "NEW_LISTING"
    # Test issues never enter the master; scanner-only GHOST is never new-to-directory.
    assert result["new_to_directory"] == 5
    assert result["in_normal_universe"] == ["BIGNEW"]
    assert result["excluded"] == {"ETF_IN_MASTER": 1, "NOT_COMMON_PRIMARY_IN_SCANNER": 1,
                                  "BELOW_NEW_LISTING_LIQUIDITY_FLOOR": 1, "BEYOND_MAX_MEMBERS": 0}
    assert list(result["excluded"]) == list(EXCLUSION_REASONS)
    [member] = result["members"]
    assert member["ticker"] == "NEWCO" and member["population"] == "NEW_LISTING"
    assert member["avg_dollar_volume_30d"] == "2000000" and member["close"] == "20"
    assert member["listing_exchange"] == "Nasdaq" and member["listing_name"] == "NEWCO Corp"
    assert result["window_seconds"] == 30 * 86400
    assert result["baseline_observed_at"] == BASELINE_AT.isoformat()
    assert result["observation_clock"] == "CALLER_SUPPLIED_UNVERIFIED"
    assert result["admission_source"] == "NASDAQ_TRADER_DIRECTORY_ONLY"
    assert result["ipo_interpretation"] == "NOT_ESTABLISHED"
    assert result["trading_eligibility"] == "NOT_ESTABLISHED" and result["trading_effect"] == "NONE"
    assert result["mode"] == "SHADOW" and result["live_money"] is False
    assert result["policy"]["basis"] == "DECLARED_UNVALIDATED" and result["policy"]["min_avg_dollar_volume"] == "1000000"


def test_scanner_recognition_alone_never_admits():
    tickers = {m["ticker"] for m in run()["members"]}
    assert "GHOST" not in tickers and "TESTX" not in tickers


def test_normal_universe_membership_wins_and_status_ends_when_baseline_contains_symbol():
    current = master(NEW)
    rows = scanner(SCANNER)
    wide = build_universe(current, rows, size=3)
    assert "NEWCO" in {m["ticker"] for m in wide["members"]}
    result = run(current=current, rows=rows, normal=wide)
    assert result["members"] == [] and result["in_normal_universe"] == ["BIGNEW", "NEWCO"]
    seasoned = run(baseline=master((("NEWCO", "N", "N"),)))
    assert seasoned["members"] == [] and seasoned["new_to_directory"] == 4


def test_observation_window_and_clock_fail_closed():
    for days in (27, 61, -30):
        with pytest.raises(UniverseError, match="window"):
            run(baseline_observed_at=CURRENT_AT - timedelta(days=days))
    with pytest.raises(UniverseError, match="aware"):
        run(baseline_observed_at=BASELINE_AT.replace(tzinfo=None))
    with pytest.raises(UniverseError, match="aware"):
        run(current_observed_at="2026-11-20T04:15:00+00:00")
    assert run(baseline_observed_at=CURRENT_AT - timedelta(days=28))["members"]
    assert run(baseline_observed_at=CURRENT_AT - timedelta(days=60))["members"]


def test_member_cap_orders_by_dollar_volume_and_counts_overflow():
    extra = NEW + (("SECOND", "N", "N"),)
    rows = scanner(SCANNER + [("SECOND", "NYSE", 300000, 20)])
    current = master(extra)
    policy = NewListingPolicy(max_members=1)
    result = run(current=current, rows=rows, normal=build_universe(current, rows, size=2), policy=policy)
    assert [m["ticker"] for m in result["members"]] == ["SECOND"]
    assert result["excluded"]["BEYOND_MAX_MEMBERS"] == 1
    uncapped = run(current=current, rows=rows, normal=build_universe(current, rows, size=2))
    assert [m["ticker"] for m in uncapped["members"]] == ["SECOND", "NEWCO"]


def test_result_is_independent_of_scanner_row_order():
    rows = scanner(SCANNER)
    current = master(NEW)
    normal = build_universe(current, rows, size=2)
    assert run(current=current, rows=rows, normal=normal) == run(current=current, rows=list(reversed(rows)),
                                                                 normal=normal)


def test_ambiguous_or_malformed_inputs_fail_closed():
    rows = scanner(SCANNER)
    with pytest.raises(UniverseError, match="duplicate"):
        run(rows=rows + [dict(rows[0])])
    with pytest.raises(UniverseError, match="liquidity"):
        run(rows=[{**rows[0], "avg_dollar_volume_30d": 5.0}] + rows[1:])
    with pytest.raises(UniverseError, match="normal universe"):
        run(normal={"version": "US-UNIVERSE-v0", "members": []})
    with pytest.raises(UniverseError, match="unique"):
        run(normal={"version": "US-UNIVERSE-v1", "members": [{"ticker": "A"}, {"ticker": "A"}]})
    current = master(NEW)
    broken = dict(current)
    broken["NEWCO"] = {**current["NEWCO"], "etf": "N"}
    with pytest.raises(UniverseError, match="parse_master"):
        run(current=broken, normal={"version": "US-UNIVERSE-v1", "members": []})
    with pytest.raises(UniverseError, match="normalized"):
        run(baseline={**master(), "aapl": master()["AAPL"]})
    with pytest.raises(UniverseError, match="implausible"):
        run(policy=NewListingPolicy(max_new_to_directory=2))


@pytest.mark.parametrize("bad", [
    {"max_members": 0}, {"max_members": True}, {"min_window_days": 61}, {"max_window_days": 400},
    {"min_avg_dollar_volume": Decimal("-1")}, {"min_avg_dollar_volume": 1000000.0},
    {"min_avg_dollar_volume": Decimal("NaN")}, {"version": "us-new-listing-policy-v0"}, {"basis": "VALIDATED"},
])
def test_invalid_policy_is_refused(bad):
    with pytest.raises(UniverseError, match="policy"):
        run(policy=NewListingPolicy(**bad))
