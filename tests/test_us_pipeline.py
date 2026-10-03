import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import us_run
from app.context.fetch import Fetcher
from app.ui.product import product_state, render_product
from app.ui.us import coverage, load_us_ranking, render_us, summary
from app.us import nyse_calendar
from app.us.universe import UniverseError, build_universe, normalize, parse_master, parse_scanner

NOW = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)  # Saturday morning UTC: Friday's NYSE session is complete


def master_texts(n=3200):
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    nasdaq += "".join(f"N{i}|Nasdaq Co {i}|Q|N|N|100|N|N\n" for i in range(n))
    nasdaq += "AAPL|Apple Inc. Common Stock|Q|N|N|100|N|N\nQQQ|Invesco QQQ|G|N|N|100|Y|N\nZZT|Test|Q|Y|N|100|N|N\n"
    nasdaq += "File Creation Time: 1002202621:31|||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    other += "BF.B|Brown Forman Class B|N|BF.B|N|100|N|BF.B\nJPM|JPMorgan Chase|N|JPM|N|100|N|JPM\n"
    other += "File Creation Time: 1002202621:31||||||\n"
    return nasdaq, other


def scanner_payload(rows):
    return json.dumps({"totalCount": len(rows), "data": [
        {"s": f"{ex}:{t}", "d": [t, f"{t} Inc", ex, "stock", "common", vol, close, 1e9, "Tech", isin]}
        for t, ex, vol, close, isin in rows]}).encode()


def test_master_and_universe_selection_by_dollar_volume():
    master = parse_master(*master_texts())
    assert master["BF.B"]["exchange"] == "NYSE" and master["QQQ"]["etf"] and "ZZT" not in master
    rows = parse_scanner(scanner_payload(
        [("AAPL", "NASDAQ", 1e7, 300, "US0378331005"), ("JPM", "NYSE", 2e7, 100, "US46625H1005"),
         ("QQQ", "NASDAQ", 9e9, 500, None), ("MISSING", "NYSE", 9e9, 9, None), ("BAD", "NYSE", 0, 5, None)]
        + [(f"N{i}", "NASDAQ", 1000 + i, 1, None) for i in range(1100)]))
    universe = build_universe(master, rows, size=3)
    assert [m["ticker"] for m in universe["members"]] == ["AAPL", "JPM", "N1099"]
    assert universe["excluded"] == {"NOT_IN_MASTER": 1, "ETF_IN_MASTER": 1}
    assert universe["floor_avg_dollar_volume_30d"] == "2099" and "current liquidity" in universe["rule"]
    assert normalize("brk/b") == "BRK.B"
    with pytest.raises(UniverseError):
        parse_master("Symbol|Security Name\nA|B\n", master_texts()[1])
    with pytest.raises(UniverseError):
        parse_scanner(scanner_payload([("AAPL", "NASDAQ", 1, 1, None)]))


def test_identity_problems_quarantine_mismatches():
    member = {"exchange": "NASDAQ", "isin": "US0378331005"}
    good = {"type": "stock", "timezone": "America/New_York", "currency_code": "USD", "listed_exchange": "NASDAQ",
            "isin": "US0378331005"}
    assert us_run.identity_problems(member, good) == []
    assert us_run.identity_problems(member, {**good, "isin": "US0000000000", "listed_exchange": "NYSE"}) == [
        "EXCHANGE_MISMATCH", "ISIN_MISMATCH"]
    assert "TYPE_NOT_STOCK" in us_run.identity_problems(member, {**good, "type": "fund"})


def test_artifacts_are_immutable_and_conflicts_are_recorded(tmp_path):
    rows = [{"date": "2026-10-02", "close": "1"}]
    path, status = us_run.write_artifact(tmp_path, "BF.B", "2026-10-02", rows, {"provider": "p"})
    assert status == "WRITTEN" and path.endswith("BF_B.json")
    assert us_run.write_artifact(tmp_path, "BF.B", "2026-10-02", rows, {"provider": "p"})[1] == "IDEMPOTENT"
    assert us_run.write_artifact(tmp_path, "BF.B", "2026-10-02", [{"date": "2026-10-02", "close": "2"}],
                                 {"provider": "p"})[1] == "CONFLICT_KEPT_EXISTING"
    assert json.loads(open(path).read())["rows"] == rows


def test_session_verification_requires_the_expected_session_to_be_observed():
    friday = [{"date": d} for d in ("2026-09-30", "2026-10-01", "2026-10-02")]
    verified = us_run.verify_sessions(friday, now=NOW)
    assert verified["status"] == "VERIFIED" and verified["expected_session"] == "2026-10-02"
    missing = us_run.verify_sessions(friday[:2], now=NOW)
    assert missing["status"] == "UNVERIFIED" and missing["verified"] is False
    assert us_run.verify_sessions([], now=NOW)["status"] == "UNKNOWN"
    # US and EGX calendars are separate: Friday is a NYSE session, Sunday is not.
    assert nyse_calendar.is_session(date(2026, 10, 2)) and not nyse_calendar.is_session(date(2026, 10, 4))


def test_yahoo_parser_uses_exchange_local_dates():
    payload = json.dumps({"chart": {"result": [{"meta": {"gmtoffset": -14400},
                                                "timestamp": [1790947800, 1791034200],
                                                "indicators": {"quote": [{"close": [100.5, None]}]}}]}}).encode()
    assert us_run.parse_yahoo(payload) == {"2026-10-02": Decimal("100.5")}
    with pytest.raises(ValueError):
        us_run.parse_yahoo(b'{"chart": {"result": []}}')


def tv_stream(resolved, closes, end=date(2026, 10, 2)):
    days, day = [], end
    while len(days) < len(closes):
        if nyse_calendar.is_session(day):
            days.append(day)
        day -= timedelta(days=1)
    bars = []
    for day, close in zip(reversed(days), closes):
        stamp = datetime.fromisoformat(f"{day.isoformat()}T09:30:00-04:00").timestamp()
        bars.append({"i": len(bars), "v": [stamp, close, close * 1.01, close * 0.99, close, 5_000_000]})

    def frame(message):
        body = json.dumps(message)
        return f"~m~{len(body)}~m~{body}"
    return (frame({"m": "symbol_resolved", "p": ["cs", "s1", resolved]})
            + frame({"m": "timescale_update", "p": ["cs", {"s1": {"s": bars}}]}))


def fake_fetch(symbol, **kwargs):
    if symbol == "SP:SPX":
        return {"raw": tv_stream({"type": "index", "timezone": "America/New_York"}, [5000 + i for i in range(120)])}
    ticker = symbol.split(":")[1]
    resolved = {"type": "stock", "timezone": "America/New_York", "currency_code": "USD",
                "listed_exchange": symbol.split(":")[0], "isin": f"ISIN{ticker}"}
    if ticker == "FAIL":
        raise us_run.SeriesError("FETCH_FAILED:TIMEOUT")
    if ticker == "WRONG":
        resolved["listed_exchange"] = "NYSE"
    return {"raw": tv_stream(resolved, [100 + i * (1 if ticker != "DOWN" else -0.2) for i in range(260)])}


class Routes:
    def __init__(self, routes):
        self.routes = routes

    def open(self, request, timeout=None):
        from tests.test_context import Response, http_error
        for prefix, payload in self.routes.items():
            if request.full_url.startswith(prefix):
                return Response(payload() if callable(payload) else payload)
        raise http_error(404)


def run_with_fakes(tmp_path):
    nasdaq, other = master_texts()
    nasdaq += "UP|Up Inc|Q|N|N|100|N|N\nDOWN|Down Inc|Q|N|N|100|N|N\nFAIL|Fail Inc|Q|N|N|100|N|N\n"
    nasdaq += "WRONG|Wrong Inc|Q|N|N|100|N|N\n"
    scan = scanner_payload([("UP", "NASDAQ", 1e8, 100, "ISINUP"), ("DOWN", "NASDAQ", 9e7, 100, "ISINDOWN"),
                            ("FAIL", "NASDAQ", 8e7, 100, None), ("WRONG", "NASDAQ", 7e7, 100, None)]
                           + [(f"N{i}", "NASDAQ", 10, 1, None) for i in range(1100)])
    yahoo = json.dumps({"chart": {"result": [{"meta": {"gmtoffset": -14400}, "timestamp": [1790947800],
                                              "indicators": {"quote": [{"close": [359.0]}]}}]}}).encode()
    opener = Routes({us_run.NASDAQ_LISTED_URL: nasdaq.encode(), us_run.OTHER_LISTED_URL: other.encode(),
                     us_run.SCANNER_URL: scan, "https://query1.finance.yahoo.com": yahoo})
    report = us_run.run(data_root=str(tmp_path / "data"), state_dir=str(tmp_path / "state"), tv_python="/p",
                        tv_script="/s", size=4, now=NOW, fetcher=Fetcher(opener=opener, sleep=lambda s: None),
                        fetch=fake_fetch)
    by = {r["ticker"]: r for r in report["symbols"]}
    assert report["acquisition"] == {"ACQUIRED": 2, "FAILED": 1, "QUARANTINED": 1}
    assert by["UP"]["freshness"] == "CURRENT" and by["UP"]["classification"] in ("CANDIDATE", "STRONG_CANDIDATE")
    assert by["UP"]["rank_version"] == "US-RANK-v1" and "USD/day" in by["UP"]["selection_reason"]
    assert by["DOWN"]["classification"] == "NO_TRADE" and "DOWNTREND" in by["DOWN"]["rejection_reasons"]
    assert by["WRONG"]["rejection_reasons"] == ["QUARANTINED:EXCHANGE_MISMATCH"]
    assert by["FAIL"]["rejection_reasons"] == ["FAILED:FETCH_FAILED:TIMEOUT"]
    assert report["sessions"]["status"] == "VERIFIED" and report["next_expected_session"] == "2026-10-05"
    assert report["cross_check"]["sample"] == 2 and report["live_money"] is False
    ledger = (tmp_path / "state" / "system-candidates.jsonl").read_text().splitlines()
    assert len(ledger) == report["new_candidates"] >= 1 and json.loads(ledger[0])["market"] == "US"
    again = us_run.run(data_root=str(tmp_path / "data"), state_dir=str(tmp_path / "state"), tv_python="/p",
                       tv_script="/s", size=4, now=NOW, fetcher=Fetcher(opener=opener, sleep=lambda s: None),
                       fetch=fake_fetch)
    assert again["new_candidates"] == 0 and {r["artifact_status"] for r in again["symbols"]
                                             if "artifact_status" in r} == {"IDEMPOTENT"}
    return report, tmp_path / "state" / "us-ranking.json"


def test_run_end_to_end_with_fakes(tmp_path):
    report, path = run_with_fakes(tmp_path)
    assert path.is_file() and report["schema"] == "us-ranking-report-v1"


def test_us_report_renders_and_loads(tmp_path, monkeypatch):
    report, path = run_with_fakes(tmp_path)
    monkeypatch.setenv("EGX_US_RANKING_REPORT_PATH", str(path))
    loaded = load_us_ranking()
    assert loaded["market"] == "US" and summary(loaded)["sessions"] == "VERIFIED"
    assert coverage(loaded)[1]["count"] == 2
    html = render_us(loaded, "TODAY")
    assert "US candidates and watchlist" in html and "US-RANK-v1" in html and "NYSE" in html
    assert "UNAVAILABLE" in render_us(None, "TODAY")
    state = product_state({"configured": True, "available": True, "status": "PARTIAL", "observed_at": None,
                           "symbols": []}, market="ALL", section="SWING", us_ranking=loaded)
    page = render_product(state)
    assert "US system-generated Paper/Shadow candidates" in page and "LIVE MONEY DISABLED" in page
    path.write_text(json.dumps({**loaded, "live_money": True}))
    assert load_us_ranking() is None
