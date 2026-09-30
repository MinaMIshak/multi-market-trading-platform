from datetime import date, datetime, timezone
import io
import json
from urllib import error

import pytest

from app.data import egx_market_watch_capture as capture_module
from app.data.providers.egx_market_watch import (
    CAIRO, EGXMarketWatchProvider, MarketWatchError, MarketWatchSnapshot, PROVIDER,
    completed_session_bars,
)
from app.data.source_admission import EVIDENCE_BLOCKED, daily_source_admission


def row(reuters, isin, traded="2026-09-30", **overrides):
    values = {"reuters": reuters, "isin": isin, "openPrice": 10.0, "high": 11.0, "low": 9.5,
              "closePrice": 10.5, "lastPrice": 10.4, "volume": 1000,
              "lastTradeDate": f"{traded}T00:00:00"}
    values.update(overrides)
    return values


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    """Serves canned documents keyed by URL fragments; records every call."""

    def __init__(self, status="Closed", status_date="2026-09-30T16:30:00", pages=None,
                 overrides=None):
        self.calls = []
        self.status = {"success": True, "data": {"status": status, "statusDate": status_date}}
        pages = pages or [[row("AAA.CA", "EG1"), row("BBB.CA", "EG2")]]
        self.pages = pages
        self.total = sum(len(page) for page in pages)
        self.overrides = overrides or {}

    def open(self, req, timeout):
        url = req.full_url
        self.calls.append(url)
        for fragment, outcome in self.overrides.items():
            if fragment in url:
                result = outcome.pop(0) if isinstance(outcome, list) else outcome
                if isinstance(result, Exception):
                    raise result
                return Response(result)
        if url.endswith("/en"):
            return Response(b"<html></html>")
        if "market-status" in url:
            return Response(json.dumps(self.status).encode())
        page = int(url.split("Page=")[1].split("&")[0])
        document = {"success": True, "data": {
            "data": self.pages[page - 1], "totalCount": self.total,
            "totalPages": len(self.pages), "pageNumber": page}}
        return Response(json.dumps(document).encode())


AFTER_CLOSE = datetime(2026, 9, 30, 17, 0, tzinfo=CAIRO).astimezone(timezone.utc)


def provider(opener, now=AFTER_CLOSE, **kwargs):
    return EGXMarketWatchProvider(opener=opener, sleep=lambda s: None,
                                  clock_now=lambda: now, **kwargs)


def test_registry_keeps_market_watch_evidence_blocked():
    assert daily_source_admission(PROVIDER, "EGX").status == EVIDENCE_BLOCKED


def test_snapshot_pages_through_all_rows_with_bff_headers():
    opener = Opener(pages=[[row("AAA.CA", "EG1")], [row("BBB.CA", "EG2")], [row("CCC.CA", "EG3")]])
    snapshot = provider(opener, page_size=1).fetch_snapshot()
    assert [r["isin"] for r in snapshot.rows] == ["EG1", "EG2", "EG3"]
    assert snapshot.total_count == 3 and len(snapshot.pages) == 3
    assert snapshot.status == "Closed"
    assert opener.calls[0].endswith("/en")
    assert [c for c in opener.calls if "Page=" in c][0].endswith("Page=1&PageSize=1")


def test_waf_html_page_fails_closed():
    opener = Opener(overrides={"market-watch": b"<html><title>Request Rejected</title></html>"})
    with pytest.raises(MarketWatchError, match="NON_JSON_RESPONSE:market-watch"):
        provider(opener).fetch_snapshot()


def test_transient_errors_retry_with_backoff_then_succeed():
    waits = []
    opener = Opener(overrides={"market-status": [
        error.URLError("down"), error.HTTPError("u", 503, "busy", {}, None),
        json.dumps({"success": True, "data": {"status": "Closed",
                                             "statusDate": "2026-09-30T16:30:00"}}).encode()]})
    snapshot = EGXMarketWatchProvider(opener=opener, sleep=waits.append,
                                      clock_now=lambda: AFTER_CLOSE).fetch_snapshot()
    assert snapshot.status == "Closed" and waits == [2.0, 4.0]


def test_persistent_outage_and_client_errors_fail_closed():
    with pytest.raises(MarketWatchError, match="UNAVAILABLE_AFTER_RETRIES:URLError"):
        provider(Opener(overrides={"market-status": error.URLError("down")})).fetch_snapshot()
    with pytest.raises(MarketWatchError, match="HTTP_403"):
        provider(Opener(overrides={"market-status": error.HTTPError("u", 403, "no", {}, None)})).fetch_snapshot()


def test_row_count_and_duplicate_isin_are_rejected():
    opener = Opener(pages=[[row("AAA.CA", "EG1"), row("BBB.CA", "EG1")]])
    with pytest.raises(MarketWatchError, match="DUPLICATE_ISIN"):
        provider(opener).fetch_snapshot()
    short = Opener(pages=[[row("AAA.CA", "EG1")]])
    short.total = 5
    with pytest.raises(MarketWatchError, match="ROW_COUNT_MISMATCH"):
        provider(short).fetch_snapshot()


def snapshot_of(rows, *, status="Closed", status_date="2026-09-30T16:30:00", now=AFTER_CLOSE):
    return MarketWatchSnapshot(captured_at=now, status=status, status_date=status_date,
                               status_response=None, pages=(), rows=tuple(rows),
                               total_count=len(rows))


def test_completed_session_yields_neutral_rows_dated_by_session():
    session, bars, rejected = completed_session_bars(snapshot_of([row("AAA.CA", "EG1")]))
    assert session == date(2026, 9, 30) and rejected == {}
    assert bars["AAA.CA"] == {"date": "2026-09-30", "open": "10.0", "high": "11.0",
                              "low": "9.5", "close": "10.5", "volume": "1000", "isin": "EG1"}
    assert "adjusted_close" not in bars["AAA.CA"]


def test_open_session_trap_is_never_dated_as_previous_session():
    # Observed live: status Open, lastTradeDate = previous session, live prices.
    live = snapshot_of([row("AAA.CA", "EG1", traded="2026-09-29")], status="Open",
                       status_date="2026-09-30T11:11:09",
                       now=datetime(2026, 9, 30, 11, 39, tzinfo=CAIRO))
    with pytest.raises(MarketWatchError, match="SESSION_NOT_CLOSED:Open"):
        completed_session_bars(live)


def test_capture_before_completion_cutoff_or_on_other_day_is_rejected():
    early = datetime(2026, 9, 30, 15, 0, tzinfo=CAIRO)
    with pytest.raises(MarketWatchError, match="CAPTURE_NOT_AFTER_SESSION_COMPLETION"):
        completed_session_bars(snapshot_of([row("AAA.CA", "EG1")], now=early))
    with pytest.raises(MarketWatchError, match="CAPTURE_NOT_AFTER_SESSION_COMPLETION"):
        completed_session_bars(snapshot_of([row("AAA.CA", "EG1")],
                                           status_date="2026-09-29T16:30:00"))


@pytest.mark.parametrize("overrides,reason", [
    ({"lastTradeDate": "2026-09-29T00:00:00"}, "NOT_TRADED_IN_SESSION"),
    ({"lastTradeDate": "2026-10-01T00:00:00"}, "LAST_TRADE_DATE_AFTER_SESSION"),
    ({"closePrice": None}, "MISSING_FIELD"),
    ({"volume": "n/a"}, "NON_NUMERIC_FIELD"),
    ({"low": 0}, "NON_POSITIVE_FIELD"),
    ({"closePrice": 12.0}, "OHLC_INCONSISTENT"),
    ({"high": 9.0}, "OHLC_INCONSISTENT"),
    ({"lastTradeDate": "yesterday"}, "LAST_TRADE_DATE_INVALID"),
])
def test_bad_rows_are_rejected_not_repaired(overrides, reason):
    _, bars, rejected = completed_session_bars(snapshot_of([row("AAA.CA", "EG1", **overrides)]))
    assert bars == {} and rejected == {"AAA.CA": reason}


def test_capture_writes_immutable_evidence_and_never_overwrites(tmp_path):
    out = capture_module.capture(tmp_path, provider=provider(Opener()))
    assert out.parent.name == "2026-09-30"
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["admission"] == "EVIDENCE_BLOCKED" and manifest["rows"] == 2
    assert manifest["session_gate"] == {"verdict": "COMPLETED_SESSION",
                                        "session_date": "2026-09-30", "bars": 2, "rejected": {}}
    assert {f["file"] for f in manifest["files"]} == {"market-status.json",
                                                      "market-watch-page-0001.json"}
    assert all(not p.stat().st_mode & 0o222 for p in [out, *out.iterdir()])
    with pytest.raises(FileExistsError):
        capture_module.capture(tmp_path, provider=provider(Opener()))


def test_capture_during_open_session_records_rejected_gate(tmp_path):
    opener = Opener(status="Open", status_date="2026-09-30T11:11:09",
                    pages=[[row("AAA.CA", "EG1", traded="2026-09-29")]])
    out = capture_module.capture(tmp_path, provider=provider(
        opener, now=datetime(2026, 9, 30, 11, 39, tzinfo=CAIRO).astimezone(timezone.utc)))
    gate = json.loads((out / "MANIFEST.json").read_text())["session_gate"]
    assert gate == {"verdict": "REJECTED", "reason": "SESSION_NOT_CLOSED:Open", "bars": 0}


def test_capture_failure_leaves_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(capture_module, "EGXMarketWatchProvider",
                        lambda: provider(Opener(overrides={"market-status": error.URLError("x")})))
    assert capture_module.main(["--out-root", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    assert list(tmp_path.rglob("*")) == []


def test_cross_session_consistency_requires_every_close_to_become_prev_close():
    from app.data.providers.egx_market_watch import cross_session_consistency
    earlier = [row("AAA.CA", "EG1", closePrice=10.5), row("BBB.CA", "EG2", closePrice=20.0)]
    later = [row("AAA.CA", "EG1", prevClose=10.5), row("BBB.CA", "EG2", prevClose=20.0),
             row("CCC.CA", "EG3", prevClose=5.0)]
    assert cross_session_consistency(earlier, later) == {
        "compared": 2, "matched": 2, "mismatched": [], "only_earlier": 0,
        "only_later": 1, "consistent": True}
    later[1]["prevClose"] = 19.9
    assert cross_session_consistency(earlier, later)["mismatched"] == ["EG2"]
    assert cross_session_consistency([], later)["consistent"] is False
