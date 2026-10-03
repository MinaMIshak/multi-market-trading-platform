import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.research.macro import (BY_ID, SERIES, MacroParseError, build_report, parse_fred_csv, summarize,
                                valid_report)
from app.research.macro_fetch import main, store_raw
from app.ui.macro import load_macro, render_macro, summary

NOW = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)


def csv(series_id, rows):
    return "observation_date," + series_id + "\n" + "\n".join(f"{d},{v}" for d, v in rows) + "\n"


def daily(series_id, start_value, days=25, end=date(2026, 10, 1)):
    rows = [(date.fromordinal(end.toordinal() - days + 1 + i).isoformat(), f"{start_value + i * 0.01:.2f}")
            for i in range(days)]
    return csv(series_id, rows)


def test_parse_skips_missing_values_and_validates_shape():
    observations, missing = parse_fred_csv(csv("DGS10", [("2026-09-30", "5.29"), ("2026-10-01", ""),
                                                         ("2026-10-02", ".")]), "DGS10")
    assert observations == [(date(2026, 9, 30), Decimal("5.29"))] and missing == 2
    for bad in ("observation_date,DGS2\n2026-09-30,1\n", csv("DGS10", [("2026-10-01", "1"), ("2026-09-30", "1")]),
                csv("DGS10", [("2026-10-01", "abc")]), csv("DGS10", [("2026-10-01", "NaN")]),
                "observation_date,DGS10\n2026-10-01,1,2\n"):
        with pytest.raises(MacroParseError):
            parse_fred_csv(bad, "DGS10")


def test_summary_is_point_in_time_and_marks_staleness():
    observations, _ = parse_fred_csv(daily("DGS10", 5.0), "DGS10")
    early = summarize(BY_ID["DGS10"], observations, date(2026, 9, 20))
    assert early["latest_date"] == "2026-09-20" and early["excluded_future"] == 11
    current = summarize(BY_ID["DGS10"], observations, date(2026, 10, 3))
    assert current["freshness"] == "CURRENT" and current["age_days"] == 2
    assert current["change_1"] == "0.01" and current["change_20"] == "0.20" and current["change_1_pct"] is None
    stale = summarize(BY_ID["DGS10"], observations, date(2026, 10, 12))
    assert stale["freshness"] == "STALE"
    assert summarize(BY_ID["DGS10"], observations, date(2026, 1, 1))["status"] == "UNAVAILABLE"
    brent, _ = parse_fred_csv(daily("DCOILBRENTEU", 100.0), "DCOILBRENTEU")
    assert summarize(BY_ID["DCOILBRENTEU"], brent, date(2026, 10, 3))["change_1_pct"] == "0.01"


def fetched_all(**overrides):
    out = {s.series_id: {"text": daily(s.series_id, 4.0), "sha256": "x", "retrieved_at": NOW.isoformat(),
                         "url": "https://example.invalid"} for s in SERIES}
    out.update(overrides)
    return out


def test_report_derives_curve_from_same_dated_values_and_records_failures():
    report = build_report(fetched_all(DCOILBRENTEU={"error": "URLError: offline"}),
                          as_of=date(2026, 10, 3), generated_at=NOW)
    assert valid_report(report) and report["live_money"] is False and report["use"] == "CONTEXT_ONLY_NOT_A_SIGNAL"
    rows = {row["series_id"]: row for row in report["series"]}
    assert rows["DCOILBRENTEU"]["status"] == "UNAVAILABLE" and "offline" in rows["DCOILBRENTEU"]["reason"]
    assert report["derived"][0]["value"] == "0" and report["derived"][0]["shape"] == "FLAT"
    assert {item["capability"] for item in report["blocked"]} >= {"Gold spot", "USD/EGP and CBE policy rate"}
    skewed = build_report(fetched_all(DGS2={"text": daily("DGS2", 4.0, end=date(2026, 9, 30)), "sha256": "x",
                                             "retrieved_at": NOW.isoformat(), "url": "u"}),
                          as_of=date(2026, 10, 3), generated_at=NOW)
    assert skewed["derived"][0]["value"] is None and skewed["derived"][0]["shape"] == "UNKNOWN"
    with pytest.raises(ValueError):
        build_report({}, as_of=date(2026, 10, 3), generated_at=datetime(2026, 10, 3))


def test_raw_store_is_content_addressed_and_detects_corruption(tmp_path):
    digest, path = store_raw(tmp_path, "DFF", b"observation_date,DFF\n")
    assert digest == hashlib.sha256(b"observation_date,DFF\n").hexdigest()
    assert store_raw(tmp_path, "DFF", b"observation_date,DFF\n") == (digest, path)
    with open(path, "wb") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError):
        store_raw(tmp_path, "DFF", b"observation_date,DFF\n")


def test_fetch_job_writes_report_and_fails_closed_per_series(tmp_path, capsys):
    def get(url):
        series_id = url.split("id=")[1].split("&")[0]
        if series_id == "DEXUSEU":
            raise OSError("timeout")
        return daily(series_id, 3.0).encode()
    report_path = tmp_path / "state" / "macro-context.json"
    assert main(["--data-root", str(tmp_path / "data"), "--report", str(report_path)], get=get, now=lambda: NOW) == 0
    report = json.loads(report_path.read_text())
    status = {row["series_id"]: row["status"] for row in report["series"]}
    assert status.pop("DEXUSEU") == "UNAVAILABLE" and set(status.values()) == {"AVAILABLE"}
    assert len(list((tmp_path / "data" / "macro" / "raw").rglob("*.csv"))) == len(SERIES) - 1
    assert '"macro_series_available": 5' in capsys.readouterr().out
    assert main(["--data-root", str(tmp_path / "d2"), "--report", str(tmp_path / "r2.json")],
                get=lambda url: (_ for _ in ()).throw(OSError("down")), now=lambda: NOW) == 1


def test_panel_renders_report_and_unavailable_state(tmp_path, monkeypatch):
    html = render_macro(None)
    assert "UNAVAILABLE" in html and "Gold spot" in html
    report = build_report(fetched_all(DGS2={"error": "boom"}), as_of=date(2026, 10, 3), generated_at=NOW)
    html = render_macro(report)
    assert "US Treasury 10-year yield" in html and "UNAVAILABLE: boom" in html and "+0.01pp" in html
    assert "Context only" in html and "UNKNOWN bp" in html
    path = tmp_path / "macro-context.json"
    path.write_text(json.dumps(report))
    monkeypatch.setenv("EGX_MACRO_REPORT_PATH", str(path))
    assert load_macro() == report
    assert summary(load_macro())["available"] == len(SERIES) - 1
    path.write_text(json.dumps({**report, "live_money": True}))
    assert load_macro() is None and summary(None) == {"status": "UNAVAILABLE"}
