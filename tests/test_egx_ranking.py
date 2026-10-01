from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
import uuid

import pytest

from app import egx_ranking_run
from app.paper.system_candidates import MIN_SAMPLE, performance, simulate
from app.strategies.egx_ranking import Bar, classify, features
from app.ui.ranking import render_performance, render_pre_surge, render_swing, render_today, summary

D = Decimal


def series(n=80, start=10.0, step=0.05, volume=1_000_000, last_volume=None, end=date(2026, 9, 29)):
    days, day = [], end
    while len(days) < n:
        if day.weekday() not in (4, 5):
            days.append(day)
        day -= timedelta(days=1)
    days.sort()
    bars = []
    for i, d in enumerate(days):
        close = start + step * i
        v = last_volume if (last_volume is not None and i == n - 1) else volume
        bars.append(Bar(d.isoformat(), D(str(round(close - 0.02, 4))), D(str(round(close + 0.1, 4))),
                        D(str(round(close - 0.1, 4))), D(str(round(close, 4))), D(v)))
    return bars


def classify_bars(bars, **overrides):
    values = dict(ticker="ACAP", company="A Capital", isin="EGS697S1C015",
                  source="tradingview_tvdatafeed_egx", licensing="NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED",
                  admitted=True, freshness="CURRENT", session=bars[-1].date, bars=bars)
    values.update(overrides)
    return classify(**values)


def test_strong_uptrend_with_breakout_and_volume_is_strong_candidate():
    record = classify_bars(series(step=0.2, last_volume=3_000_000))
    assert record["classification"] == "STRONG_CANDIDATE" and record["rejection_reasons"] == []
    assert record["trend"] == "UP" and record["breakout_20"] and record["volume_confirmation"]
    low, high = (D(v) for v in record["entry_zone"])
    close = D(record["entry_reference"])
    assert low < close < high
    risk = close - D(record["stop"])
    assert D(record["target_1"]) == pytest.approx(close + D("1.5") * risk, abs=D("0.02"))
    assert D(record["target_2"]) == pytest.approx(close + 3 * risk, abs=D("0.02"))
    assert D(record["risk_pct"]) >= D("3")


@pytest.mark.parametrize("overrides,reason", [
    ({"admitted": False}, "SOURCE_NOT_ADMITTED"),
    ({"freshness": "STALE"}, "DATA_NOT_CURRENT"),
    ({"session": "2026-09-30"}, "LAST_BAR_NOT_SESSION"),
])
def test_gates_override_any_score(overrides, reason):
    record = classify_bars(series(step=0.2, last_volume=3_000_000), **overrides)
    assert record["classification"] == "NO_TRADE" and reason in record["rejection_reasons"]
    assert record["score"] is not None


def test_illiquid_zero_volume_and_short_history_are_rejected():
    assert "ILLIQUID" in classify_bars(series(volume=10_000))["rejection_reasons"]
    assert "ZERO_VOLUME_SESSION" in classify_bars(series(step=0.2, last_volume=0))["rejection_reasons"]
    short = classify_bars(series(n=30))
    assert short["rejection_reasons"] == ["INSUFFICIENT_HISTORY"] and short["score"] is None


def test_downtrend_is_no_trade_and_mild_uptrend_is_watchlist_or_candidate():
    down = classify_bars(series(start=20.0, step=-0.1))
    assert down["classification"] == "NO_TRADE" and down["rejection_reasons"] == ["DOWNTREND"]
    mild = classify_bars(series(step=0.01, last_volume=900_000))
    assert mild["classification"] in ("WATCHLIST", "CANDIDATE")


def test_features_use_only_given_bars():
    bars = series(step=0.2)
    assert features(bars[:-1])["close"] == float(bars[-2].close)


def bar(d, o, h, l, c):
    return Bar(d, D(str(o)), D(str(h)), D(str(l)), D(str(c)), D(1000))


CANDIDATE = {"entry_zone": ["9.95", "10.05"], "stop": "9.70", "target_1": "10.45", "target_2": "10.90"}


def test_lifecycle_paths():
    assert simulate(CANDIDATE, [])["status"] == "PENDING_ENTRY"
    assert simulate(CANDIDATE, [bar("d1", 9.8, 10.0, 9.7, 9.9)])["status"] == "NO_FILL_GAP_DOWN"
    assert simulate(CANDIDATE, [bar("d1", 10.3, 10.4, 10.2, 10.3)])["status"] == "NO_FILL_GAP_UP"
    gap_up_fill = simulate(CANDIDATE, [bar("d1", 10.3, 10.4, 10.0, 10.2)])
    assert gap_up_fill["entry"] == "10.05" and gap_up_fill["status"] == "OPEN"
    stop = simulate(CANDIDATE, [bar("d1", 10.0, 10.1, 9.6, 9.65)])
    assert stop["status"] == "CLOSED_STOP" and Decimal(stop["r_multiple_gross"]) == Decimal("-1")
    win = simulate(CANDIDATE, [bar("d1", 10.0, 10.2, 9.9, 10.1), bar("d2", 10.2, 10.5, 10.1, 10.4),
                              bar("d3", 10.5, 11.0, 10.4, 10.9)])
    assert win["status"] == "CLOSED_T2"
    assert Decimal(win["r_multiple_gross"]) == Decimal("2.25")
    assert Decimal(win["net_return_pct"]) < Decimal("6.75")
    breakeven = simulate(CANDIDATE, [bar("d1", 10.0, 10.5, 9.9, 10.4), bar("d2", 10.3, 10.35, 9.95, 10.0)])
    assert breakeven["status"] == "CLOSED_BREAKEVEN"
    same_bar = simulate(CANDIDATE, [bar("d1", 10.0, 10.6, 9.6, 10.0)])
    assert same_bar["status"] == "CLOSED_STOP"
    flat = [bar(f"d{i}", 10.0, 10.1, 9.9, 10.0) for i in range(1, 13)]
    timed = simulate(CANDIDATE, flat)
    assert timed["status"] == "CLOSED_TIME" and timed["sessions_held"] == 11
    gap_through_stop = simulate(CANDIDATE, [bar("d1", 10.0, 10.1, 9.9, 10.0), bar("d2", 9.5, 9.6, 9.4, 9.5)])
    assert gap_through_stop["events"][-1]["price"] == "9.5"


def test_performance_requires_closed_sample():
    assert performance([{"status": "OPEN"}])["status"] == "INSUFFICIENT_SAMPLE"
    closed = [{"status": "CLOSED_T2", "net_return_pct": "6.0", "r_multiple_gross": "2.25", "sessions_held": 3},
              {"status": "CLOSED_STOP", "net_return_pct": "-3.5", "r_multiple_gross": "-1", "sessions_held": 1}]
    result = performance(closed * MIN_SAMPLE)
    assert result["status"] == "MEASURED" and result["win_rate_pct"] == "50.0"
    assert result["expectancy_r"] == "0.625" and Decimal(result["max_drawdown_r"]) <= 0


# ----- runner end-to-end on a temporary platform DB -----

def platform(tmp_path, bars_by_ticker, provider="tradingview_tvdatafeed_egx", snapshot="2026-09-30"):
    from app.storage.database import Database
    db_path = tmp_path / "platform.db"
    Database(str(db_path)).initialize()
    data_root = tmp_path / "data"
    with sqlite3.connect(db_path) as con:
        for d in ("2026-09-28", "2026-09-29"):
            payload = json.dumps({"market_date": d, "status": "VERIFIED", "timezone": "Africa/Cairo",
                                  "opened_at": None, "closed_at": None, "first15_closed_at": None,
                                  "data_verified_at": "2026-09-30T00:00:00Z"})
            con.execute("INSERT INTO market_sessions VALUES (?, 'VERIFIED', ?, '2026-09-30T00:00:00+00:00')",
                        (d, payload))
        for index, (ticker, bars) in enumerate(bars_by_ticker.items()):
            instrument = str(uuid.uuid4())
            con.execute(
                "INSERT INTO canonical_instruments (instrument_id, instrument_type, canonical_ticker, name_en, "
                "source_provider, source_symbol_code, source_sha256, normalization_notes_json, updated_at) "
                "VALUES (?, 'EQUITY', ?, ?, 'egid', ?, 'x', '[]', '2026-09-26T00:00:00+00:00')",
                (instrument, ticker, ticker + " Co", f"EGS0000{index}C0{index}"))
            rows = [{"market_date": b.date, "open": str(b.open), "high": str(b.high), "low": str(b.low),
                     "close": str(b.close), "volume": str(b.volume), "semantic_class": "VALID_EXECUTABLE"}
                    for b in bars]
            payload = json.dumps(rows).encode()
            relative = f"{provider}/daily_bars/{ticker}.json"
            (data_root / "canonical" / provider / "daily_bars").mkdir(parents=True, exist_ok=True)
            (data_root / "canonical" / relative).write_bytes(payload)
            con.execute(
                "INSERT INTO daily_canonical_artifacts (artifact_id, instrument_id, canonical_symbol, provider, "
                "provider_symbol, source_snapshot_date, canonical_path, sha256, byte_size, record_count, "
                "oldest_market_date, newest_market_date, valid_bar_count, quarantined_bar_count, "
                "semantic_contract_version, serialization_format, status, created_at, validated_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'egx-daily-semantic-v1', 'canonical-json-v1', "
                "'VALIDATED', '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00', '{}')",
                (str(uuid.uuid4()), instrument, ticker, provider, ticker, snapshot, relative,
                 hashlib.sha256(payload).hexdigest(), len(payload), len(rows), bars[0].date, bars[-1].date, len(rows)))
    return db_path, data_root


NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)


def test_runner_ranks_records_candidates_once_and_simulates(tmp_path):
    db, data_root = platform(tmp_path, {"ACAP": series(step=0.2, last_volume=3_000_000),
                                        "DOWN": series(start=20.0, step=-0.1)})
    report_path, ledger = tmp_path / "out" / "ranking.json", tmp_path / "out" / "candidates.jsonl"
    report = egx_ranking_run.run(db_path=db, data_root=data_root, report_path=report_path,
                                 candidates_path=ledger, now=NOW)
    assert report["admission"] == "ADMITTED"
    assert report["licensing"] == "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED"
    assert report["counts"] == {"STRONG_CANDIDATE": 1, "NO_TRADE": 1}
    assert report["symbols"][0]["ticker"] == "ACAP" and report["new_candidates"] == 1
    entry = json.loads(ledger.read_text())
    assert entry["origin"] == "SYSTEM_GENERATED" and entry["human_review"] == "OPTIONAL_NOT_REVIEWED"
    assert entry["live_money"] is False
    assert report["lifecycles"][0]["status"] == "PENDING_ENTRY"
    again = egx_ranking_run.run(db_path=db, data_root=data_root, report_path=report_path,
                                candidates_path=ledger, now=NOW)
    assert again["new_candidates"] == 0 and len(ledger.read_text().splitlines()) == 1
    assert json.loads(report_path.read_text())["schema"] == "egx-ranking-report-v1"


def test_runner_rejects_tampered_artifacts_and_unadmitted_providers(tmp_path):
    db, data_root = platform(tmp_path, {"ACAP": series(step=0.2, last_volume=3_000_000)})
    next((data_root / "canonical").rglob("ACAP.json")).write_text("[]")
    report = egx_ranking_run.run(db_path=db, data_root=data_root, report_path=tmp_path / "r.json",
                                 candidates_path=tmp_path / "c.jsonl", now=NOW)
    assert report["symbols"][0]["rejection_reasons"][0].startswith("ARTIFACT_UNREADABLE")
    db2, root2 = platform(tmp_path / "b", {"ACAP": series(step=0.2, last_volume=3_000_000)},
                          provider="tradingview_tvdatafeed")
    blocked = egx_ranking_run.run(db_path=db2, data_root=root2, report_path=tmp_path / "b" / "r.json",
                                  candidates_path=tmp_path / "b" / "c.jsonl", now=NOW,
                                  provider="tradingview_tvdatafeed")
    assert blocked["admission"] == "EVIDENCE_BLOCKED"
    assert blocked["symbols"][0]["classification"] == "NO_TRADE"
    assert "SOURCE_NOT_ADMITTED" in blocked["symbols"][0]["rejection_reasons"]
    assert blocked["new_candidates"] == 0


def test_renderers_show_evidence_and_safety_labels(tmp_path):
    db, data_root = platform(tmp_path, {"ACAP": series(step=0.2, last_volume=3_000_000)})
    report = egx_ranking_run.run(db_path=db, data_root=data_root, report_path=tmp_path / "r.json",
                                 candidates_path=tmp_path / "c.jsonl", now=NOW)
    for html in (render_today(report), render_swing(report), render_pre_surge(report), render_performance(report)):
        assert "LIVE MONEY DISABLED" in html and "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED" in html
    assert "STRONG_CANDIDATE" in render_swing(report) and "Target 2" in render_swing(report)
    assert "NOT a prediction" in render_pre_surge(report)
    assert "INSUFFICIENT_SAMPLE" in render_performance(report)
    assert summary(None) == {"status": "UNAVAILABLE"}
    assert "UNAVAILABLE" in render_today(None)
