from datetime import date, datetime, timedelta, timezone
import io
import json
import sqlite3
import subprocess
import uuid

import pytest

from app.data import tradingview_refresh as refresh
from app.data.provider_mapping import build_isin_mapping, mapped_pairs
from app.data.providers.tradingview import (
    CAIRO, PROVIDER, TradingViewError, TradingViewProvider, daily_rows, discover_equities, parse_stream,
)
from app.data.source_admission import daily_source_admission

ACAP, ABUK, AALR = "EGS697S1C015", "EGS38191C010", "EGS01081C016"
SESSION = date(2026, 9, 29)
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=CAIRO).astimezone(timezone.utc)


def frame(message):
    body = json.dumps(message)
    return f"~m~{len(body)}~m~{body}"


def trading_days(end, count):
    days, day = [], end
    while len(days) < count:
        if day.weekday() not in (4, 5):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def stream(symbol="ACAP", isin=ACAP, days=None, close=10.0, resolved=None, extra_bars=()):
    days = days if days is not None else trading_days(date(2026, 9, 30), 25)  # includes the open session
    bars = [{"i": i, "v": [datetime(d.year, d.month, d.day, 10, 0, tzinfo=CAIRO).timestamp(),
                           close, close + 1, close - 1, close + 0.5, 1000 + i]}
            for i, d in enumerate(days)]
    bars += [{"i": len(bars) + k, "v": v} for k, v in enumerate(extra_bars)]
    info = {"name": symbol, "exchange": "EGX", "timezone": "Africa/Cairo", "currency_code": "EGP",
            "type": "stock", "isin": isin, "provider_id": "ice", **(resolved or {})}
    return "".join([
        frame({"m": "critical_error", "p": ["qs_x", "invalid_parameters", "quote_add_symbols"]}),
        frame({"m": "symbol_resolved", "p": ["cs_x", "symbol_1", info]}),
        "~m~4~m~~h~1",
        frame({"m": "timescale_update", "p": ["cs_x", {"s1": {"s": bars}}]}),
        frame({"m": "series_completed", "p": ["cs_x", "s1"]}),
    ])


def test_stream_is_decoded_and_dated_in_cairo_excluding_open_session():
    parsed = parse_stream(stream())
    assert parsed["errors"] == ["critical_error:invalid_parameters"]
    rows = daily_rows(parsed, start_date=date(2026, 9, 1), end_date=SESSION)
    assert rows[-1]["date"] == "2026-09-29" and all(r["date"] <= "2026-09-29" for r in rows)
    assert set(rows[-1]) == {"date", "open", "high", "low", "close", "volume"}


@pytest.mark.parametrize("resolved,code", [
    ({"exchange": "NASDAQ"}, "RESOLVED_NOT_EGX_CAIRO_EGP_STOCK"),
    ({"timezone": "Europe/London"}, "RESOLVED_NOT_EGX_CAIRO_EGP_STOCK"),
    ({"type": "fund"}, "RESOLVED_NOT_EGX_CAIRO_EGP_STOCK"),
])
def test_resolution_must_be_egx_cairo_egp_stock(resolved, code):
    with pytest.raises(TradingViewError, match=code):
        daily_rows(parse_stream(stream(resolved=resolved)), start_date=date(2026, 9, 1), end_date=SESSION)


def test_duplicate_dates_and_non_finite_values_fail_closed():
    day = datetime(2026, 9, 28, 10, 0, tzinfo=CAIRO).timestamp()
    with pytest.raises(TradingViewError, match="DUPLICATE_SESSION_DATE"):
        daily_rows(parse_stream(stream(extra_bars=[[day, 1, 2, 0.5, 1, 5]])), start_date=date(2026, 9, 1),
                   end_date=SESSION)
    with pytest.raises(TradingViewError, match="BAR_VALUE_NOT_FINITE"):
        daily_rows(parse_stream(stream(days=[], extra_bars=[[day, "NaN", 2, 0.5, 1, 5]])),
                   start_date=date(2026, 9, 1), end_date=SESSION)
    with pytest.raises(TradingViewError, match="SYMBOL_NOT_RESOLVED"):
        daily_rows({"resolved": None, "bars": [], "errors": []}, start_date=date(2026, 9, 1), end_date=SESSION)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SearchOpener:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def open(self, req, timeout):
        self.calls.append(req.full_url)
        return Response(json.dumps(self.pages.pop(0)).encode())


def listing(symbol, isin, name="x"):
    return {"symbol": symbol, "isin": isin, "description": name, "type": "stock", "exchange": "EGX"}


def test_discovery_pages_until_no_symbols_remain():
    opener = SearchOpener([{"symbols": [listing("ACAP", ACAP)], "symbols_remaining": 1},
                           {"symbols": [listing("ABUK", ABUK)], "symbols_remaining": 0}])
    pages, rows = discover_equities(opener=opener, sleep=lambda s: None)
    assert [r["symbol"] for r in rows] == ["ACAP", "ABUK"] and len(pages) == 2
    assert "start=1" in opener.calls[1] and "exchange=EGX" in opener.calls[0]


def equities(*pairs):
    return [{"instrument_id": str(uuid.uuid4()), "canonical_ticker": t, "isin": i, "name_en": t}
            for t, i in pairs]


def test_isin_mapping_categories():
    rows = [listing("ACAP", ACAP), listing("AIH", ABUK), listing("X", "US0378331005"),
            listing("BAD", "EGS697S1C016"), listing("D1", AALR), listing("D2", AALR)]
    report = build_isin_mapping(rows, equities(("ACAP", ACAP), ("ABUK", ABUK), ("AALR", AALR),
                                               ("NONE", "EGS60121C018")))
    s = report["summary"]
    assert (s["matched"], s["ambiguous"], s["provider_only"], s["invalid"], s["unmatched_known"],
            s["ticker_disagreements"]) == (2, 1, 1, 1, 1, 1)
    assert mapped_pairs(report) == [("ABUK", "AIH"), ("ACAP", "ACAP")]


def runner_for(streams, calls=None, fail_first=0):
    state = {"fail": fail_first}
    def run(command, **kwargs):
        if calls is not None:
            calls.append(command)
        if state["fail"]:
            state["fail"] -= 1
            return subprocess.CompletedProcess(command, 1, stdout=json.dumps({"error": "timeout"}))
        symbol = command[2].split(":")[1]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(
            {"symbol": command[2], "raw": streams[symbol]}))
    return run


def tv_provider(streams, tmp_path=None, **kwargs):
    return TradingViewProvider(python_path="/tv/python", fetch_script="/repo/tools/tradingview_fetch.py",
                               evidence_dir=tmp_path, sleep=lambda s: None,
                               runner=runner_for(streams, **kwargs))


def test_provider_retries_helper_and_records_provenance(tmp_path):
    calls = []
    provider = tv_provider({"ACAP": stream()}, tmp_path / "native", calls=calls, fail_first=1)
    response = provider.fetch_daily_bars(symbol="ACAP", start_date=date(2026, 9, 1), end_date=SESSION)
    assert len(calls) == 2 and calls[-1][2] == "EGX:ACAP"
    assert response.metadata["price_adjustment"] == "splits"
    assert response.metadata["data_vendor"] == "ice"
    assert provider.resolved["ACAP"]["isin"] == ACAP
    assert list((tmp_path / "native").rglob("ACAP-*.json"))
    with pytest.raises(TradingViewError, match="FETCH_FAILED"):
        tv_provider({"ACAP": stream()}, fail_first=3).fetch_daily_bars(
            symbol="ACAP", start_date=date(2026, 9, 1), end_date=SESSION)


def test_registry_admits_tradingview_as_operator_accepted_unlicensed():
    from app.data.source_admission import licensing_label
    result = daily_source_admission(PROVIDER, "EGX")
    assert result.status == "ADMITTED" and "no contractual licence" in result.reason
    assert licensing_label(result.declaration) == "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED"
    assert daily_source_admission("tradingview_tvdatafeed", "EGX").status == "EVIDENCE_BLOCKED"


# ----- end-to-end on a temporary platform DB -----

def platform(tmp_path):
    from app.storage.database import Database
    db_path = tmp_path / "platform.db"
    database = Database(str(db_path))
    database.initialize()
    with database.connect() as con:
        for ticker, isin in (("ACAP", ACAP), ("ABUK", ABUK), ("AALR", AALR)):
            instrument = str(uuid.uuid4())
            con.execute(
                "INSERT INTO canonical_instruments (instrument_id, instrument_type, canonical_ticker, "
                "name_en, source_provider, source_symbol_code, source_sha256, normalization_notes_json, "
                "updated_at) VALUES (?, 'EQUITY', ?, ?, 'egid', ?, 'x', '[]', '2026-09-26T00:00:00+00:00')",
                (instrument, ticker, ticker, isin))
            con.execute(
                "INSERT INTO instrument_aliases (instrument_id, provider, alias_type, alias_value, "
                "normalized_value, created_at) VALUES (?, 'canonical', 'CANONICAL_TICKER', ?, ?, "
                "'2026-09-26T00:00:00+00:00')", (instrument, ticker, ticker))
        con.execute("INSERT INTO market_sessions VALUES (?, 'VERIFIED', '{}', '2026-09-30T00:00:00+00:00')",
                    (SESSION.isoformat(),))
    return db_path


def discovery(rows):
    return lambda: ([json.dumps({"symbols": rows}).encode()], rows)


LISTING = [listing("ACAP", ACAP), listing("AIH", ABUK), listing("AALR", AALR)]


def run(tmp_path, streams, rows=LISTING, **kwargs):
    return refresh.run(db_path=tmp_path / "platform.db", data_root=tmp_path / "data",
                       state_dir=tmp_path / "state", now=NOW, provider=tv_provider(streams),
                       discover=discovery(rows), lookback_days=40, minimum_valid_bars=5, **kwargs)


STREAMS = {"ACAP": stream("ACAP", ACAP), "AIH": stream("AIH", ABUK), "AALR": stream("AALR", AALR)}


def test_acquisition_stores_mapped_symbols_idempotently_without_open_session(tmp_path):
    db = platform(tmp_path)
    first = run(tmp_path, STREAMS)
    assert first["outcomes"] == {"STORED": 3} and first["aliases_inserted"] == 3
    assert first["mapping"]["ticker_disagreements"] == 1
    second = run(tmp_path, STREAMS)
    assert second["outcomes"] == {"STORED": 3} and second["aliases_inserted"] == 0
    assert second["counts"]["daily_canonical_artifacts"] == [3, 3]
    with sqlite3.connect(db) as con:
        newest = {r[0] for r in con.execute("SELECT newest_market_date FROM daily_canonical_artifacts")}
        providers = {r[0] for r in con.execute("SELECT provider FROM daily_canonical_artifacts")}
    assert newest == {"2026-09-29"} and providers == {PROVIDER}
    assert (tmp_path / "state" / "mapping" / "latest.json").is_file()


def test_resolved_isin_mismatch_is_rejected(tmp_path):
    platform(tmp_path)
    streams = dict(STREAMS, ACAP=stream("ACAP", ABUK))
    result = run(tmp_path, streams)
    assert result["outcomes"] == {"STORED": 2, "REJECTED": 1}
    outcomes = json.loads(next((tmp_path / "state" / "outcomes").glob("*.json")).read_text())["symbol_outcomes"]
    assert outcomes["ACAP"] == "REJECTED:RESOLVED_ISIN_MISMATCH"


def test_market_watch_discrepancy_quarantines_symbol(tmp_path):
    platform(tmp_path)
    evidence = tmp_path / "evidence" / SESSION.isoformat() / "cap"
    evidence.mkdir(parents=True)
    official = [{"isin": ACAP, "openPrice": 10.0, "high": 11.0, "low": 9.0, "closePrice": 10.5},
                {"isin": ABUK, "openPrice": 10.0, "high": 11.0, "low": 9.0, "closePrice": 13.0}]
    (evidence / "MANIFEST.json").write_text(json.dumps({
        "market_status": "Closed", "rows": 2,
        "session_gate": {"verdict": "COMPLETED_SESSION", "session_date": SESSION.isoformat()}}))
    (evidence / "market-watch-page-0001.json").write_text(json.dumps({"data": {"data": official}}))
    result = run(tmp_path, STREAMS, market_watch_evidence=tmp_path / "evidence")
    assert result["cross_check"] == {"MATCH": 1, "DISCREPANCY": 1, "UNVERIFIED": 1}
    assert result["outcomes"] == {"STORED": 2, "QUARANTINED_CROSS_CHECK": 1}


def test_insufficient_history_is_rejected_per_symbol(tmp_path):
    platform(tmp_path)
    streams = dict(STREAMS, AALR=stream("AALR", AALR, days=trading_days(date(2026, 9, 30), 3)))
    assert run(tmp_path, streams)["outcomes"] == {"STORED": 2, "REJECTED": 1}


def test_cli_records_failure_without_verified_session(tmp_path, monkeypatch, capsys):
    db = platform(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM market_sessions")
    monkeypatch.setattr(refresh, "discover_equities", discovery(LISTING))
    code = refresh.main(["--db-path", str(db), "--data-root", str(tmp_path / "data"),
                         "--state-dir", str(tmp_path / "state"), "--tv-python", "/tv/python"])
    assert code == refresh.EXIT_FAILED
    assert json.loads((tmp_path / "state" / "last-run.json").read_text())["error"] == "NO_VERIFIED_SESSION"


def test_cross_check_policy_v2_separates_material_from_definitional_differences():
    from decimal import Decimal
    from app.data.daily_cross_check import DISCREPANCY, MATCH, MINOR, UNVERIFIED, compare
    tol = Decimal("0.005")
    bar = {"open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "1000"}
    official = {"openPrice": 10, "high": 11, "low": 9, "lastPrice": 10.5, "closePrice": 10.45,
                "volume": 1000}
    assert compare(bar, official, tolerance=tol)[0] == MATCH  # weighted close: report-only
    verdict, detail = compare(dict(bar, low="8.73"), official, tolerance=tol)  # low 3 % lower
    assert verdict == MINOR and detail["low"]["material"] is False
    assert compare(dict(bar, open="10.2"), official, tolerance=tol)[0] == DISCREPANCY  # open 2 %
    assert compare(dict(bar, close="10.8"), official, tolerance=tol)[0] == DISCREPANCY  # vs last 2.9 %
    assert compare(dict(bar, high="11.3"), official, tolerance=tol)[0] == DISCREPANCY  # high 2.7 %
    verdict, detail = compare(dict(bar, volume="1500"), official, tolerance=tol)
    assert verdict == MINOR and detail["volume"]["material"] is False
    fallback = dict(official)
    fallback.pop("lastPrice")
    assert compare(bar, fallback, tolerance=tol)[0] == MATCH  # close vs closePrice within 0.5 %
    assert compare(bar, None, tolerance=tol)[0] == UNVERIFIED


def test_same_day_snapshot_already_used_is_deferred_not_rejected(tmp_path):
    platform(tmp_path)
    run(tmp_path, STREAMS)
    later = dict(STREAMS, ACAP=stream("ACAP", ACAP, close=11.0))
    result = run(tmp_path, later)
    assert result["outcomes"] == {"STORED": 2, "DEFERRED_SNAPSHOT_DATE_USED": 1}
