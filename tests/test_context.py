import io
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.context import official, regime
from app.context.fetch import FetchError, Fetcher, store_raw
from app.context.run import run, section_status
from app.context.series import correlation, expected_latest, reconcile, returns, summarize
from app.context.tradingview_series import (BY_KEY, MarketSpec, SeriesError, completed_bars, current_trade_date,
                                            trade_date)
from app.data.providers.tradingview import parse_stream
from app.ui.context import load_context, render_context, summary
from app.us import nyse_calendar

NOW = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)  # Saturday


# --- fetch policy -------------------------------------------------------------------------------

class Response(io.BytesIO):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(payload)
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def open(self, request, timeout=None):
        self.calls.append(request.full_url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, None)


def test_fetch_retries_transient_and_honours_retry_after():
    sleeps = []
    opener = Opener([http_error(429, {"Retry-After": "7"}), http_error(503), Response(b"ok")])
    fetcher = Fetcher(opener=opener, sleep=sleeps.append, backoff=1.0, max_attempts=3)
    assert fetcher.get("https://example.org/a")[0] == b"ok"
    assert sleeps == [7.0, 2.0] and len(opener.calls) == 3


def test_fetch_policy_errors_are_not_retried_and_https_and_size_are_enforced():
    opener = Opener([http_error(403)])
    with pytest.raises(FetchError) as exc:
        Fetcher(opener=opener, sleep=lambda s: None).get("https://sec.example/x")
    assert exc.value.code == "HTTP_403" and len(opener.calls) == 1
    with pytest.raises(FetchError) as exc:
        Fetcher(opener=Opener([])).get("http://insecure.example/")
    assert exc.value.code == "HTTPS_REQUIRED"
    with pytest.raises(FetchError) as exc:
        Fetcher(opener=Opener([Response(b"x" * 11)]), max_bytes=10).get("https://big.example/")
    assert exc.value.code == "RESPONSE_TOO_LARGE"
    with pytest.raises(FetchError) as exc:
        Fetcher(opener=Opener([OSError("down")] * 2), sleep=lambda s: None, max_attempts=2).get("https://n.example/")
    assert exc.value.code == "NETWORK_OSError"


def test_fetch_spaces_requests_per_host():
    sleeps, clock = [], iter([0.0, 1.0, 1.0])
    fetcher = Fetcher(opener=Opener([Response(b"1"), Response(b"2")]), spacing={"api.gdeltproject.org": 6.0},
                      sleep=sleeps.append, clock=lambda: next(clock))
    fetcher.get("https://api.gdeltproject.org/a")
    fetcher.get("https://api.gdeltproject.org/b")
    assert sleeps == [5.0]


def test_raw_store_is_idempotent_and_detects_corruption(tmp_path):
    digest, path = store_raw(tmp_path, "src", b"payload", "json")
    assert store_raw(tmp_path, "src", b"payload", "json") == (digest, path)
    open(path, "wb").write(b"tampered")
    with pytest.raises(FetchError):
        store_raw(tmp_path, "src", b"payload", "json")


# --- calendars: US vs EGX separation ---------------------------------------------------------------

def test_nyse_holidays_follow_published_rules():
    assert set(nyse_calendar.holidays(2026)) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
        date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    assert date(2021, 12, 31) not in nyse_calendar.holidays(2021)  # Saturday New Year not observed Friday
    assert date(2022, 6, 20) in nyse_calendar.holidays(2022) and not any(
        d.month == 6 and d.day in (18, 19, 21) for d in nyse_calendar.holidays(2021))
    assert nyse_calendar.closure_reason(date(2025, 1, 9)).startswith("National Day of Mourning")
    assert nyse_calendar.previous_session(date(2026, 11, 27)) == date(2026, 11, 25)
    assert nyse_calendar.next_session(date(2026, 7, 2)) == date(2026, 7, 6)


def test_session_verification_reports_conflicts_instead_of_trusting_rules():
    result = nyse_calendar.verified_sessions([date(2026, 7, 2), date(2026, 7, 3)], date(2026, 7, 2),
                                             date(2026, 7, 6))
    assert result[date(2026, 7, 2)] == "VERIFIED_SESSION"
    assert result[date(2026, 7, 3)] == "CONFLICT_BAR_ON_RULE_HOLIDAY"
    assert result[date(2026, 7, 4)] == "VERIFIED_CLOSED"
    assert result[date(2026, 7, 6)] == "CONFLICT_NO_BAR_ON_RULE_SESSION"


def test_expected_latest_session_differs_by_market():
    saturday_next = date(2026, 10, 4)
    assert expected_latest(saturday_next, "NYSE") == date(2026, 10, 2)   # Friday is a US session
    assert expected_latest(saturday_next, "EGX") == date(2026, 10, 1)    # Friday/Saturday EGX weekend
    assert expected_latest(date(2026, 11, 27), "NYSE") == date(2026, 11, 25)
    assert expected_latest(date(2026, 12, 26), "FX") == date(2026, 12, 24)
    assert expected_latest(date(2026, 10, 5), "EGX") == date(2026, 10, 4)   # Sunday is an EGX session
    assert expected_latest(date(2026, 10, 5), "NYSE") == date(2026, 10, 2)


# --- series summaries, reconciliation, co-movement ------------------------------------------------------

def obs(values, end=date(2026, 10, 2)):
    days, day = [], end
    while len(days) < len(values):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(zip(reversed(days), [Decimal(str(v)) for v in values]))


def test_summary_freshness_is_session_aware_and_point_in_time():
    series = obs([100 + i for i in range(30)])
    current = summarize(series, as_of=date(2026, 10, 3), calendar="NYSE", unit="points",
                        current_trade_date=date(2026, 10, 5))
    assert current["freshness"] == "CURRENT" and current["expected_latest"] == "2026-10-02"
    assert current["change_20_pct"] is not None and current["change_1"] == "1"
    stale = summarize(series[:-1], as_of=date(2026, 10, 3), calendar="NYSE", unit="points",
                      current_trade_date=date(2026, 10, 5))
    assert stale["freshness"] == "STALE"
    early = summarize(series, as_of=date(2026, 9, 30), calendar="NYSE", unit="points")
    assert early["latest_date"] == "2026-09-30" and early["excluded_future"] == 2
    assert summarize([], as_of=date(2026, 10, 3), calendar="FX", unit="x")["status"] == "UNAVAILABLE"
    published = summarize(series, as_of=date(2026, 10, 20), calendar="PUBLICATION", unit="%", max_age_days=5)
    assert published["freshness"] == "STALE" and published["change_1_pct"] is None


def test_reconcile_and_correlation_use_common_dates_only():
    day = date(2026, 10, 2)
    assert reconcile((day, Decimal("100")), (day, Decimal("100.4")), tolerance_pct=Decimal("0.5"))["status"] == "AGREE"
    assert reconcile((day, Decimal("100")), (day, Decimal("101")), tolerance_pct=Decimal("0.5"))["status"] == "DISCREPANT"
    assert reconcile((day, Decimal("1")), (day - timedelta(days=1), Decimal("1")),
                     tolerance_pct=Decimal("1"))["status"] == "UNVERIFIED"
    assert reconcile(None, (day, Decimal("1")), tolerance_pct=Decimal("1"))["status"] == "UNVERIFIED"
    a = returns(obs([1, 2, 3, 4, 5, 7, 6, 8]))
    b = {d: v * 2 for d, v in list(a.items())[::2]}  # half the dates missing: never filled
    result = correlation(a, b, window=60, min_overlap=3)
    assert result["n"] == len(b) and result["value"] == 1.0
    assert correlation(a, b, window=60, min_overlap=10)["value"] is None


# --- TradingView market series -------------------------------------------------------------------------

def stream(resolved, bars):
    def frame(message):
        body = json.dumps(message)
        return f"~m~{len(body)}~m~{body}"
    raw = frame({"m": "symbol_resolved", "p": ["cs", "s1", resolved]})
    raw += frame({"m": "timescale_update", "p": ["cs", {"s1": {"s": [{"i": i, "v": v} for i, v in enumerate(bars)]}}]})
    return raw + frame({"m": "series_completed", "p": ["cs"]})


def epoch(text):
    return datetime.fromisoformat(text).timestamp()


FX = {"type": "forex", "timezone": "Etc/UTC", "currency_code": "EGP"}


def test_roll_markets_date_bars_by_session_end_and_drop_the_open_session():
    spec = BY_KEY["USDEGP"]
    bars = [[epoch("2026-09-30T22:00:00+00:00"), 52.1, 52.3, 52.0, 52.2, 0],
            [epoch("2026-10-01T22:00:00+00:00"), 52.2, 52.3, 52.2, 52.26, 0],
            [epoch("2026-10-04T22:00:00+00:00"), 52.3, 52.4, 52.2, 52.3, 0]]
    rows = completed_bars(parse_stream(stream(FX, bars)), spec, now=datetime(2026, 10, 5, 10, tzinfo=timezone.utc))
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-02"]   # the Monday session is still open
    assert trade_date(datetime(2026, 10, 1, 22, tzinfo=timezone.utc), spec) == date(2026, 10, 2)


def test_exchange_sessions_complete_only_after_the_close():
    spec = BY_KEY["SPX"]
    resolved = {"type": "index", "timezone": "America/New_York"}
    bars = [[epoch("2026-10-01T09:30:00-04:00"), 10, 11, 9, 10.5, 1],
            [epoch("2026-10-02T09:30:00-04:00"), 10.5, 12, 10, 11, 1]]
    during = completed_bars(parse_stream(stream(resolved, bars)), spec,
                            now=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))
    after = completed_bars(parse_stream(stream(resolved, bars)), spec,
                           now=datetime(2026, 10, 2, 21, 0, tzinfo=timezone.utc))
    assert [r["date"] for r in during] == ["2026-10-01"] and [r["date"] for r in after] == ["2026-10-01", "2026-10-02"]
    assert current_trade_date(datetime(2026, 10, 2, 21, tzinfo=timezone.utc), spec) == date(2026, 10, 3)


@pytest.mark.parametrize("resolved,bars,code", [
    ({"type": "stock", "timezone": "Etc/UTC", "currency_code": "EGP"}, [], "RESOLVED_MISMATCH"),
    ({**FX, "currency_code": "USD"}, [], "RESOLVED_CURRENCY_MISMATCH"),
    (FX, [[epoch("2026-09-30T22:00:00+00:00"), 1, 2, 1, 1.5, 0]] * 2, "DUPLICATE_TRADE_DATE"),
    (FX, [[epoch("2026-09-30T22:00:00+00:00"), 1, 2, 1, "NaN", 0]], "BAR_VALUE_NOT_FINITE"),
    (FX, [[epoch("2026-09-30T22:00:00+00:00"), 1, 0.5, 1, 1, 0]], "OHLC_RELATION_INVALID"),
])
def test_market_series_fail_closed(resolved, bars, code):
    with pytest.raises(SeriesError) as exc:
        completed_bars(parse_stream(stream(resolved, bars)), BY_KEY["USDEGP"], now=NOW)
    assert exc.value.code == code


# --- official / open / narrative parsers -----------------------------------------------------------------

def test_reference_rate_and_official_rate_parsers():
    er = official.parse_er_api(json.dumps({"result": "success", "base_code": "USD", "time_last_update_unix": 1790985752,
                                           "rates": {"EGP": 52.28}, "provider": "x", "terms_of_use": "t"}).encode())
    assert er["usd_egp"] == "52.28" and er["date"] == "2026-10-03"
    with pytest.raises(official.ParseError):
        official.parse_er_api(b'{"result":"error"}')
    imf = official.parse_imf(b'<m><Series INDICATOR="DISR_RT_PT_A_PT"><Obs TIME_PERIOD="2025-M07" OBS_VALUE="24.5"/>'
                             b'<Obs TIME_PERIOD="2025-M08" OBS_VALUE="22.5"/><Obs TIME_PERIOD="2025-M09" OBS_VALUE=""/>'
                             b'</Series><Series INDICATOR="OTHER"><Obs TIME_PERIOD="2025-M08" OBS_VALUE="1"/></Series></m>')
    assert imf == {"DISR_RT_PT_A_PT": {"title": "CBE discount rate", "unit": "%", "frequency": "monthly",
                                       "observations": [("2025-07", "24.5"), ("2025-08", "22.5")]}}
    bis = official.parse_bis(b"TIME_PERIOD,OBS_VALUE\n2026-09-27,3.875\n2026-09-28,3.875\n2026-09-29,\n")
    assert bis[-1] == ("2026-09-28", "3.875")


def test_fed_press_keeps_monetary_policy_dated_entries_only():
    payload = json.dumps([
        {"d": "9/17/2026 2:00:00 PM", "t": "FOMC statement", "pt": "Monetary Policy", "l": "/a.htm"},
        {"d": "1/31/2006", "t": "Old statement", "pt": "Monetary Policy", "l": "/b.htm"},
        {"d": "garbage", "t": "Undatable", "pt": "Monetary Policy", "l": "/c.htm"},
        {"d": "9/18/2026 2:00:00 PM", "t": "Bank order", "pt": "Orders", "l": "/d.htm"},
        {"d": "9/17/2026 2:00:00 PM", "t": "FOMC statement", "pt": "Monetary Policy", "l": "/a.htm"}]).encode()
    releases = official.parse_fed_press(payload)
    assert [r["title"] for r in releases] == ["FOMC statement", "Old statement"]
    assert releases[0]["published_at"] == "2026-09-17T14:00:00-04:00"


def egx_item(code, heading, content, stamp="2026-10-01T12:28:00", section="Disclosure"):
    return {"code": code, "dateStamp": stamp, "heading": heading, "content": content, "section": section}


def test_egx_disclosures_are_deduplicated_with_identity_and_documents():
    content = ('Company Name : X<br />ISIN Code : EGS23141C012<br />Reuters Code : CRST.CA<br />'
               '<a href="/downloads/Bulletins/1.pdf">r</a>')
    docs = [{"success": True, "data": [egx_item(2, "B", content, "2026-10-01T15:31:19"), egx_item(1, "A", "x")]},
            {"success": True, "data": [egx_item(2, "B", content, "2026-10-01T15:31:19")]}]
    records = official.parse_egx_disclosures(docs)
    assert [r["code"] for r in records] == [2, 1]
    assert records[0]["isin"] == "EGS23141C012" and records[0]["reuters"] == "CRST.CA"
    assert records[0]["published_at"] == "2026-10-01T15:31:19+03:00"
    assert records[0]["documents"] == ["https://beta.egx.com.eg/downloads/Bulletins/1.pdf"]
    with pytest.raises(official.ParseError):
        official.parse_egx_disclosures([{"success": False}])


def test_egx_financial_statements_parse_layouts_signs_and_units():
    profit = ("ISIN Code: EGS37091C013<br />Currency: EGP<br />F/S Consolidated Period : From 01/01/2026 To 31/03/2026"
              "<br />Net Profit: 1,253,236 Value In Thousand<br />F/S Consolidated Period : From 01/01/2025 To "
              "31/03/2025<br />Net Comparative Profit: 1,626,782 Value In Thousand<br />Audit Status : Reviewed")
    loss = ("ISIN Code: EGS69021C011<br />F/S (Standalone) Period: From 01/01/2026 to 31/03/2026<br />Net loss: 5,553,675"
            "<br />F/S (Standalone) Period: From 01/01/2025 to 31/03/2025<br />Net Comparative loss: 7,028,010")
    plain = ("F/S Period : From 01/07/2025 To 31/03/2026<br />Net Profit   : 2,810,443<br />F/S Period: From "
             "01/07/2024 To 31/03/2025<br />Net Comparative Profit   : 564,289")
    amended = "Company: X<br />The amended financial statements for the year ending on 30/06/2026"
    records = {r["code"]: r for r in official.parse_egx_financials([{"success": True, "data": [
        egx_item(1, "P", profit), egx_item(2, "L", loss), egx_item(3, "N", plain), egx_item(4, "A", amended)]}])}
    assert records[1]["net_result"] == "1253236000" and records[1]["basis"] == "CONSOLIDATED"
    assert records[1]["net_result_change_pct"] == "-23.0" and records[1]["period_end"] == "2026-03-31"
    assert records[2]["net_result"] == "-5553675" and records[2]["comparative_net_result"] == "-7028010"
    assert records[2]["net_result_change_pct"] == "21.0" and records[2]["basis"] == "STANDALONE"
    assert records[3]["basis"] == "UNSPECIFIED" and records[3]["comparative_period_end"] == "2025-03-31"
    assert records[4]["parse_status"] == "UNPARSED_LAYOUT" and "net_result" not in records[4]


def test_portwatch_ofac_and_gdelt_parsers():
    start = date(2025, 9, 1)
    features = [{"attributes": {"date": (start + timedelta(days=i)).isoformat(), "portid": "chokepoint1",
                                "n_total": 40 if i < 390 else 20, "n_tanker": 10}} for i in range(397)]
    stats = official.parse_portwatch(json.dumps({"features": features}).encode(), "chokepoint1")
    assert stats["mean_7d"] == 20 and stats["change_7d_vs_90d_pct"] < -40 and stats["days_year_ago"] == 7
    with pytest.raises(official.ParseError):
        official.parse_portwatch(b'{"features": []}', "chokepoint1")
    sdn = official.parse_ofac(b'36,"A",-0- ,"CUBA",-0-\n37,"B",-0- ,"SDGT] [IRGC",-0-\nheader,"x","y","z"\n')
    assert sdn["entries"] == 2 and dict(sdn["top_programs"]) == {"CUBA": 1, "SDGT": 1, "IRGC": 1}
    articles = official.parse_gdelt(json.dumps({"articles": [
        {"url": "https://a/x?utm=1", "title": "T", "seendate": "20261003T080000Z", "domain": "a"},
        {"url": "https://a/x?utm=2", "title": "T dup"}, {"url": "https://b/y", "title": ""}]}).encode())
    assert len(articles) == 1 and articles[0]["seen_at"] == "2026-10-03T08:00:00+00:00"


def test_sec_contact_gate(tmp_path):
    assert official.sec_contact(env={}, path=str(tmp_path / "missing")) is None
    assert official.sec_contact(env={"EGX_SEC_CONTACT": "not-an-email"}, path="/nonexistent") is None
    (tmp_path / "c").write_text("someone@example.org\n")
    assert official.sec_contact(env={}, path=str(tmp_path / "c")) == "someone@example.org"
    assert official.sec_status(None)["status"] == "BLOCKED"
    assert official.sec_status("a@b.org")["status"] == "CONFIGURED"


# --- regimes ------------------------------------------------------------------------------------------------

def series_from(values, end=date(2026, 10, 2)):
    return obs(values, end)


def test_trend_regime_and_fed_stance():
    rising = series_from([100 + i * 0.5 for i in range(260)])
    falling = series_from([300 - i * 0.5 for i in range(260)])
    assert regime.trend_regime(rising, name="x")["label"] == "RISK_ON"
    assert regime.trend_regime(falling, name="x")["label"] == "RISK_OFF"
    assert regime.trend_regime(rising[:100], name="x")["label"] == "UNKNOWN"
    upper = [(date(2026, 9, 16), Decimal("3.75")), (date(2026, 9, 17), Decimal("4.00")),
             (date(2026, 10, 1), Decimal("4.00"))]
    assert regime.fed_stance(upper)["label"] == "TIGHTENING"
    assert regime.fed_stance([(date(2026, 1, 1), Decimal("4.25")), (date(2026, 9, 1), Decimal("4"))])["label"] == "EASING"
    assert regime.fed_stance([(date(2024, 1, 1), Decimal("5")), (date(2024, 2, 1), Decimal("4.75")),
                              (date(2026, 9, 1), Decimal("4.75"))])["label"] == "HOLD"
    assert regime.fed_stance([])["label"] == "UNKNOWN"


def test_build_regimes_flags_and_unknowns():
    markets = {"SPX": series_from([100 + i for i in range(260)]), "EGX30": series_from([500 - i for i in range(260)]),
               "USDEGP": series_from([50 + i * 0.01 for i in range(260)])}
    portwatch = {"chokepoints": {"Suez Canal": {"change_7d_vs_90d_pct": -30.0, "latest_date": "2026-09-27"},
                                 "Strait of Hormuz": {"change_7d_vs_90d_pct": -50.0}}}
    built = regime.build_regimes(markets, {"VIXCLS": [(date(2026, 10, 1), Decimal("27"))]}, portwatch)
    assert built["us_equity"]["label"] == "NEUTRAL" and "vix_override" in built["us_equity"]
    assert built["egx_equity"]["label"] == "RISK_OFF" and built["suez"]["label"] == "DISRUPTED"
    assert built["risk"]["EGX"]["label"] == "HIGH" and built["curve"]["label"] == "UNKNOWN"
    assert "Energy chokepoint disrupted (Strait of Hormuz)" in built["risk"]["US"]["flags"]
    assert regime.build_regimes({}, {}, None)["risk"]["US"]["label"] == "UNKNOWN"
    assert regime.usd_terms([(date(2026, 1, 1), Decimal("100")), (date(2026, 1, 2), Decimal("100"))],
                            [(date(2026, 1, 2), Decimal("50"))]) == [(date(2026, 1, 2), Decimal("2"))]


# --- end-to-end run with fake transports, report rendering -------------------------------------------------------

def fake_market_fetch(symbol, **kwargs):
    spec = next(s for s in BY_KEY.values() if s.symbol == symbol)
    if spec.key == "EGX30":
        raise SeriesError("FETCH_FAILED:TIMEOUT")
    resolved = {"type": spec.kind, "timezone": spec.tz, "currency_code": spec.currency}
    bars = []
    for i, (day, value) in enumerate(series_from([100 + i * 0.1 for i in range(260)], end=date(2026, 10, 1))):
        hour = "09:30:00" if spec.roll is None else "12:00:00"
        bars.append([epoch(f"{day.isoformat()}T{hour}+00:00"), value, value + 1, value - 1, value, 1])
    return {"symbol": symbol, "raw": stream(resolved, [[b[0]] + [float(v) for v in b[1:]] for b in bars])}


class RoutingOpener:
    def __init__(self, routes):
        self.routes = routes

    def open(self, request, timeout=None):
        for prefix, payload in self.routes.items():
            if request.full_url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return Response(payload)
        raise http_error(404)


def test_run_builds_a_traceable_context_report(tmp_path):
    fred = lambda url: ("observation_date," + url.split("id=")[1].split("&")[0] + "\n2026-10-01,4.0\n").encode()
    opener = RoutingOpener({
        official.ER_API_URL: json.dumps({"result": "success", "base_code": "USD", "time_last_update_unix": 1790985752,
                                         "rates": {"EGP": 120.0}}).encode(),
        "https://stats.bis.org": http_error(500),
        "https://api.imf.org": b"<m/>",
        official.FED_PRESS_URL: b"[]",
        official.PORTWATCH_URL: b'{"features": []}',
        official.OFAC_URL: b'1,"A",-0- ,"CUBA"\n',
        official.GDELT_URL: http_error(429)})
    egx = lambda data_root: ({"status": "UNAVAILABLE", "reason": "NETWORK_x"}, {"status": "UNAVAILABLE", "reason": "x"})
    report = run(data_root=str(tmp_path), report_path=str(tmp_path / "ctx.json"), macro_report_path=str(tmp_path / "m.json"),
                 tv_python="/py", tv_script="/s", now=NOW, fetcher=Fetcher(opener=opener, sleep=lambda s: None),
                 market_fetch=fake_market_fetch, egx_collect=egx, fred_get=fred)
    status = section_status(report)
    assert status["market:EGX30"] == "UNAVAILABLE" and status["market:XAUUSD"] == "AVAILABLE"
    assert status["rates:bis"] == "UNAVAILABLE" and status["narrative"] == "UNAVAILABLE" and status["us_sec"] in (
        "BLOCKED", "CONFIGURED")
    usd = next(r for r in report["reconciliation"] if r["check"].startswith("USD/EGP"))
    assert usd["status"] == "DISCREPANT"   # 120 vs ~126: an honest, reported discrepancy
    assert report["live_money"] is False and report["use"] == "CONTEXT_ONLY_NOT_A_SIGNAL"
    assert json.loads((tmp_path / "ctx.json").read_text())["schema"] == "context-report-v1"
    assert (tmp_path / "context" / "raw" / "tradingview").is_dir()
    html = render_context(report)
    for title in ("Market regime", "Rates and monetary policy", "FX", "Gold", "Brent / oil", "Equity indices",
                  "News and catalysts", "Geopolitical context", "Fundamentals", "Cross-market evidence"):
        assert f"<h2>{title}</h2>" in html
    assert "UNAVAILABLE: FETCH_FAILED:TIMEOUT" in html and "BLOCKED" in html


def test_context_loader_validates_and_summarizes(tmp_path, monkeypatch):
    assert "UNAVAILABLE" in render_context(None) and summary(None) == {"status": "UNAVAILABLE"}
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema": "context-report-v1", "live_money": False, "generated_at": "t",
                                "markets": {"A": {"status": "AVAILABLE"}}, "regimes": {"risk": {"EGX": {"label": "LOW"}}}}))
    monkeypatch.setenv("EGX_CONTEXT_REPORT_PATH", str(path))
    assert summary(load_context())["egx_risk"] == "LOW"
    path.write_text(json.dumps({"schema": "context-report-v1", "live_money": True}))
    assert load_context() is None


def test_market_spec_is_explicit_about_nature():
    assert BY_KEY["UKOIL"].nature == "FUTURES_REFERENCE" and BY_KEY["XAUUSD"].nature == "MARKET_QUOTE"
    assert isinstance(BY_KEY["SPX"], MarketSpec) and BY_KEY["SPX"].calendar == "NYSE"
    assert BY_KEY["EGX30"].calendar == "EGX"
