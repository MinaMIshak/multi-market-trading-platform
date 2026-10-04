from datetime import date, datetime, timedelta, timezone
import io
import json
import sqlite3
from urllib import error, parse
import uuid

import pytest

from app.data import twelve_data_refresh as refresh
from app.data.providers.twelve_data import (
    CreditLimiter, TwelveDataAuthenticationError, TwelveDataError, TwelveDataProvider,
)
from app.data.source_admission import EVIDENCE_BLOCKED, daily_source_admission
from app.data.twelve_data_mapping import build_mapping, targets, valid_isin

CAIRO_TZ = refresh.CAIRO
ACAP, ABUK, AALR = "EGS697S1C015", "EGS38191C010", "EGS01081C016"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=CAIRO_TZ).astimezone(timezone.utc)
SESSION = date(2026, 9, 29)


def trading_days(end=SESSION, count=20):
    days, day = [], end
    while len(days) < count:
        if day.weekday() not in (4, 5):  # EGX: Friday/Saturday closed
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def series(symbol, *, close=10.0, days=None, meta=None, drop_volume=False):
    values = []
    for index, day in enumerate(days or trading_days()):
        value = {"datetime": day.isoformat(), "open": f"{close:.2f}", "high": f"{close + 1:.2f}",
                 "low": f"{close - 1:.2f}", "close": f"{close + 0.5:.2f}", "volume": str(1000 + index)}
        if drop_volume:
            value.pop("volume")
        values.append(value)
    return {"meta": {"symbol": symbol, "interval": "1day", "currency": "EGP",
                     "exchange_timezone": "Africa/Cairo", "exchange": "EGX", "mic_code": "XCAI",
                     "type": "Common Stock", **(meta or {})},
            "values": list(reversed(values)), "status": "ok"}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTwelveData:
    def __init__(self, reference=None, series_by_symbol=None, fail=None):
        self.reference = reference if reference is not None else [
            {"symbol": ACAP, "name": "A Capital", "mic_code": "XCAI", "type": "Common Stock"},
            {"symbol": ABUK, "name": "Abou Kir", "mic_code": "XCAI", "type": "Common Stock"},
            {"symbol": AALR + ".EGP", "name": "Land Reclamation", "mic_code": "XCAI", "type": "Common Stock"},
        ]
        self.series = series_by_symbol or {}
        self.fail = list(fail or [])
        self.calls = []

    def open(self, req, timeout):
        url = parse.urlparse(req.full_url)
        query = dict(parse.parse_qsl(url.query))
        self.calls.append((url.path, query))
        if self.fail:
            outcome = self.fail.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return Response(json.dumps(outcome).encode())
        if url.path.endswith("/stocks"):
            return Response(json.dumps({"data": self.reference, "status": "ok"}).encode())
        symbols = query["symbol"].split(",")
        documents = {s: self.series.get(s) or series(s) for s in symbols}
        body = documents[symbols[0]] if len(symbols) == 1 else documents
        return Response(json.dumps(body).encode())


def provider(fake, key="secret-key", **kwargs):
    clock = {"t": 0.0}
    def sleep(seconds):
        clock["t"] += seconds
    return TwelveDataProvider(api_key=key, opener=fake, sleep=sleep,
                              monotonic=lambda: clock["t"], **kwargs)


# ----- provider -----

def test_daily_bars_are_neutral_windowed_and_key_redacted():
    fake = FakeTwelveData()
    td = provider(fake)
    response = td.fetch_daily_bars(symbol=ACAP, start_date=date(2026, 9, 20), end_date=SESSION)
    rows = json.loads(response.payload)
    assert rows[-1] == {"date": "2026-09-29", "open": "10.00", "high": "11.00", "low": "9.00",
                        "close": "10.50", "volume": str(1000 + 19)}
    assert all(date(2026, 9, 20) <= date.fromisoformat(r["date"]) <= SESSION for r in rows)
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert response.record_count == len(rows) and "adjusted_close" not in rows[0]
    assert "secret-key" not in response.source_uri and "apikey" not in response.source_uri
    path, query = fake.calls[-1]
    assert query["mic_code"] == "XCAI" and query["interval"] == "1day"
    assert query["timezone"] == "Africa/Cairo" and query["apikey"] == "secret-key"


def test_prefetch_batches_and_serves_from_cache():
    fake = FakeTwelveData()
    td = provider(fake, batch_size=2, credits_per_minute=2)
    assert td.prefetch([ACAP, ABUK, AALR], date(2026, 9, 1), SESSION) == {}
    series_calls = [q for p, q in fake.calls if p.endswith("/time_series")]
    assert [q["symbol"] for q in series_calls] == [f"{ACAP},{ABUK}", AALR]
    td.fetch_daily_bars(symbol=ABUK, start_date=date(2026, 9, 1), end_date=SESSION)
    assert len([p for p, _ in fake.calls if p.endswith("/time_series")]) == 2


@pytest.mark.parametrize("bad,code", [
    (series(ACAP, meta={"mic_code": "XNAS"}), "SERIES_NOT_XCAI_CAIRO"),
    (series(ACAP, meta={"exchange_timezone": "America/New_York"}), "SERIES_NOT_XCAI_CAIRO"),
    (series(ACAP, drop_volume=True), "SERIES_FIELD_VOLUME"),
    (series(ACAP, days=[SESSION, SESSION]), "SERIES_DUPLICATE_DATE"),
    ({"code": 400, "message": "symbol not found", "status": "error"}, "SERIES_ERROR"),
])
def test_invalid_series_fail_closed_per_symbol(bad, code):
    fake = FakeTwelveData(series_by_symbol={ACAP: bad})
    td = provider(fake, batch_size=2, credits_per_minute=2)
    failures = td.prefetch([ACAP, ABUK], date(2026, 9, 1), SESSION)
    assert failures[ACAP].startswith(code) and ABUK not in failures


def test_rate_limit_retry_and_auth_errors():
    fake = FakeTwelveData(fail=[{"code": 429, "message": "x", "status": "error"}])
    td = provider(fake)
    td.fetch_daily_bars(symbol=ACAP, start_date=date(2026, 9, 1), end_date=SESSION)
    assert len(fake.calls) == 2
    with pytest.raises(TwelveDataAuthenticationError):
        provider(FakeTwelveData(fail=[{"code": 401, "message": "bad key", "status": "error"}])).fetch_daily_bars(
            symbol=ACAP, start_date=date(2026, 9, 1), end_date=SESSION)
    with pytest.raises(TwelveDataAuthenticationError, match="API_KEY_NOT_CONFIGURED"):
        provider(FakeTwelveData(), key=None).fetch_daily_bars(
            symbol=ACAP, start_date=date(2026, 9, 1), end_date=SESSION)
    outage = FakeTwelveData(fail=[error.URLError("down")] * 3)
    with pytest.raises(TwelveDataError, match="UNAVAILABLE_AFTER_RETRIES:URLError"):
        provider(outage).fetch_daily_bars(symbol=ACAP, start_date=date(2026, 9, 1), end_date=SESSION)


def test_credit_limiter_waits_and_enforces_budget():
    clock, waits = {"t": 0.0}, []
    def sleep(seconds):
        waits.append(seconds)
        clock["t"] += seconds
    limiter = CreditLimiter(credits_per_minute=8, daily_credit_budget=20, sleep=sleep,
                            monotonic=lambda: clock["t"])
    limiter.acquire(8)
    limiter.acquire(8)
    assert waits and clock["t"] >= 60
    with pytest.raises(TwelveDataError, match="DAILY_CREDIT_BUDGET_EXHAUSTED"):
        limiter.acquire(8)
    with pytest.raises(TwelveDataError, match="BATCH_EXCEEDS_MINUTE_LIMIT"):
        limiter.acquire(9)


def test_reference_requires_xcai_rows():
    fake = FakeTwelveData(reference=[{"symbol": "X", "mic_code": "XNAS"}])
    with pytest.raises(TwelveDataError, match="REFERENCE_NOT_XCAI"):
        provider(fake).fetch_reference_equities()


def test_registry_keeps_twelve_data_blocked():
    assert daily_source_admission("twelve_data", "EGX").status == EVIDENCE_BLOCKED


# ----- mapping -----

def equities(*pairs):
    return [{"instrument_id": str(uuid.uuid4()), "canonical_ticker": t, "isin": i, "name_en": t}
            for t, i in pairs]


def test_isin_check_digit():
    assert valid_isin(ACAP) and valid_isin(ABUK) and valid_isin("US0378331005")
    assert not valid_isin("EGS697S1C016") and not valid_isin("EGS697S1C01")


def test_mapping_categories_are_exact():
    rows = [{"symbol": ACAP}, {"symbol": AALR + ".EGP"}, {"symbol": "US0378331005"},
            {"symbol": "EGS697S1C016"}, {"symbol": ABUK}, {"symbol": ABUK + ".EGP"}]
    report = build_mapping(rows, equities(("ACAP", ACAP), ("AALR", AALR), ("ABUK", ABUK),
                                          ("NONE", "EGS60121C018")))
    s = report["summary"]
    assert (s["matched_exact"], s["matched_suffix"], s["provider_only"], s["unmatched_known"],
            s["invalid_provider_symbols"], s["ambiguous"]) == (2, 1, 1, 1, 1, 0)
    assert ("ABUK", ABUK) in targets(report) and ("AALR", AALR + ".EGP") in targets(report)


def test_ambiguous_isins_are_never_mapped():
    report = build_mapping([{"symbol": ACAP}], equities(("ACAP", ACAP), ("ACAP2", ACAP)))
    assert report["summary"]["ambiguous"] == 1 and targets(report) == []


# ----- credentials -----

def test_api_key_file_must_be_private(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("abc\n")
    key.chmod(0o644)
    monkeypatch.setenv("EGX_TWELVE_DATA_API_KEY_FILE", str(key))
    with pytest.raises(refresh.RefreshStop, match="API_KEY_FILE_PERMISSIONS"):
        refresh.read_api_key()
    key.chmod(0o600)
    assert refresh.read_api_key() == "abc"
    monkeypatch.delenv("EGX_TWELVE_DATA_API_KEY_FILE")
    monkeypatch.setenv("TWELVE_DATA_API_KEY", " xyz ")
    assert refresh.read_api_key() == "xyz"


# ----- end-to-end refresh on a temporary platform DB -----

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


def run(tmp_path, fake, key="secret-key", **kwargs):
    db = tmp_path / "platform.db"
    return refresh.run(db_path=db, data_root=tmp_path / "data", state_dir=tmp_path / "state",
                       mode="daily", now=NOW, lookback_days=28, minimum_valid_bars=5,
                       provider=provider(fake, key=key), **kwargs)


def aliases(db):
    with sqlite3.connect(db) as con:
        return con.execute("SELECT COUNT(*) FROM instrument_aliases WHERE provider='twelve_data'").fetchone()[0]


def test_without_credentials_mapping_is_produced_and_nothing_written(tmp_path):
    db = platform(tmp_path)
    result = run(tmp_path, FakeTwelveData(), key=None)
    assert result["outcome"] == "NO_CREDENTIALS"
    assert result["mapping"]["matched"] == 3 and result["mapping"]["matched_suffix"] == 1
    latest = json.loads((tmp_path / "state" / "mapping" / "latest.json").read_text())
    assert latest["summary"]["known_equities"] == 3
    assert (tmp_path / "state" / "reference").is_dir() and aliases(db) == 0


def test_daily_refresh_stores_artifacts_idempotently(tmp_path):
    db = platform(tmp_path)
    first = run(tmp_path, FakeTwelveData())
    assert first["session"] == "2026-09-29" and first["aliases_inserted"] == 3
    assert first["outcomes"] == {"STORED": 3}
    assert first["cross_check"] == {"UNVERIFIED": 3}
    assert first["counts"]["daily_canonical_artifacts"] == [0, 3]
    assert first["integrity_after"] == "ok"
    second = run(tmp_path, FakeTwelveData())
    assert second["aliases_inserted"] == 0 and second["outcomes"] == {"STORED": 3}
    assert second["counts"]["daily_canonical_artifacts"] == [3, 3]
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT provider, newest_market_date FROM daily_canonical_artifacts "
                          "WHERE canonical_symbol='ACAP'").fetchone()
    assert row == ("twelve_data", "2026-09-29")
    assert daily_source_admission("twelve_data", "EGX").status == EVIDENCE_BLOCKED


def market_watch_capture(root, rows):
    from tests.egx_capture_fixture import official_row, write_capture
    write_capture(root, SESSION, [official_row(row.pop("isin"), SESSION, lastPrice=None, volume=None, **row) for row in rows],
                  name="20260929T134500Z")


def test_cross_check_quarantines_material_discrepancy(tmp_path):
    db = platform(tmp_path)
    evidence = tmp_path / "evidence"
    market_watch_capture(evidence, [
        {"isin": ACAP, "openPrice": 10.0, "high": 11.0, "low": 9.0, "closePrice": 10.5},
        {"isin": ABUK, "openPrice": 10.0, "high": 11.0, "low": 9.0, "closePrice": 12.0},
    ])
    result = run(tmp_path, FakeTwelveData(), market_watch_evidence=evidence)
    assert result["cross_check"] == {"MATCH": 1, "DISCREPANCY": 1, "UNVERIFIED": 1}
    assert result["outcomes"] == {"STORED": 2, "QUARANTINED_CROSS_CHECK": 1}
    quarantined = tmp_path / "state" / "quarantine" / "2026-09-29" / f"{ABUK}-2026-09-29.json"
    detail = json.loads(quarantined.read_text())
    assert detail["differences"]["close"]["official"] == "12.0"
    with sqlite3.connect(db) as con:
        stored = {r[0] for r in con.execute("SELECT canonical_symbol FROM daily_canonical_artifacts")}
    assert stored == {"ACAP", "AALR"}


def test_per_symbol_fetch_failure_does_not_block_others(tmp_path):
    platform(tmp_path)
    fake = FakeTwelveData(series_by_symbol={ABUK: {"code": 400, "message": "x", "status": "error"}})
    result = run(tmp_path, fake)
    assert result["outcomes"] == {"STORED": 2, "FETCH_FAILED": 1}


def test_stale_provider_data_is_rejected_by_admission_policy(tmp_path):
    platform(tmp_path)
    stale = {ACAP: series(ACAP, days=trading_days(end=date(2026, 9, 28)))}
    result = run(tmp_path, FakeTwelveData(series_by_symbol=stale))
    assert result["outcomes"] == {"STORED": 2, "REJECTED": 1}


def test_no_verified_session_fails_closed(tmp_path):
    db = platform(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM market_sessions")
    with pytest.raises(refresh.RefreshStop, match="NO_VERIFIED_SESSION"):
        run(tmp_path, FakeTwelveData())


def test_cli_without_credentials_exits_2_and_records_status(tmp_path, monkeypatch, capsys):
    platform(tmp_path)
    monkeypatch.delenv("EGX_TWELVE_DATA_API_KEY_FILE", raising=False)
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.setattr(refresh, "TwelveDataProvider",
                        lambda **kw: provider(FakeTwelveData(), key=None))
    code = refresh.main(["--mode", "daily", "--db-path", str(tmp_path / "platform.db"),
                         "--data-root", str(tmp_path / "data"), "--state-dir", str(tmp_path / "state")])
    assert code == refresh.EXIT_NO_CREDENTIALS
    last = json.loads((tmp_path / "state" / "last-run.json").read_text())
    assert last["status"] == "NO_CREDENTIALS" and last["live_money"] is False
    assert "secret" not in capsys.readouterr().out
