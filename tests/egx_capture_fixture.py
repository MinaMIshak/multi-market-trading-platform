"""Realistic official EGX market-watch captures for tests (format observed 2026-09-30 .. 2026-10-04).

A post-close row carries ``writeTime`` = the session date at 15:35 and
``lastTradeDate`` = the date of its previous close.
"""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo("Africa/Cairo")


def previous_egx_day(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() in (4, 5):
        day -= timedelta(days=1)
    return day


def official_row(isin, session: date, *, write_day=None, previous_close_day=None, trades=120, **prices):
    write_day = write_day or session
    previous_close_day = previous_close_day or previous_egx_day(session)
    row = {"isin": isin, "reuters": prices.pop("reuters", f"{isin}.CA"), "openPrice": 10.0, "high": 11.0,
           "low": 9.0, "closePrice": 10.5, "lastPrice": 10.5, "prevClose": 10.0, "volume": 1000, "trades": trades,
           "writeTime": write_day.strftime("%Y%m%d") + "1535", "lastTradeDate": f"{previous_close_day}T00:00:00"}
    row.update(prices)
    return row


def write_capture(root, session: date, rows, *, status="Closed", status_day=None, captured_at=None, name="cap"):
    status_day = status_day or session
    captured_at = captured_at or datetime.combine(session, datetime.min.time(), CAIRO).replace(hour=16, minute=45)
    folder = root / session.isoformat() / name
    folder.mkdir(parents=True)
    (folder / "MANIFEST.json").write_text(json.dumps({
        "market_status": status, "rows": len(rows), "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "session_gate": {"verdict": "COMPLETED_SESSION", "session_date": status_day.isoformat()}}))
    (folder / "market-status.json").write_text(json.dumps({"data": {
        "status": status, "statusDate": f"{status_day.isoformat()}T16:45:03"}}))
    (folder / "market-watch-page-0001.json").write_text(json.dumps({"data": {"data": rows}}))
    return folder
