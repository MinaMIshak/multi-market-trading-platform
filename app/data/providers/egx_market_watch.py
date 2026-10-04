"""Official EGX market-watch adapter (``egx_official_market_watch``).

Registry status: EVIDENCE_BLOCKED (entitlement NOT_ESTABLISHED, see
docs/EGX_OFFICIAL_MARKET_WATCH_QUALIFICATION.md). This module fetches and
interprets; it grants no admission and writes nothing.

Row session semantics (field study 2026-10-05 over every stored capture;
docs/EGX_SESSION_INTEGRITY.md):
- ``lastTradeDate`` is the date of the row's ``prevClose`` (the previous close),
  not the session of its prices. In the post-close captures of 2026-09-30 and
  2026-10-01, the official volume equalled the admitted primary bar of the
  ``writeTime`` date for 205/205 matched symbols, and 0/205 for the
  ``lastTradeDate`` date. ``prevClose`` matched the primary close on
  ``lastTradeDate`` for about 94% of rows.
- ``lastTradeDate`` therefore never dates a bar, and neither does ``writeTime``,
  the market status or the capture time alone.

``row_observation`` assigns each row an observation session with its evidence:
SESSION_ALIGNED only when every condition below holds; otherwise an explicit
non-aligned status:
- the market status is Closed;
- the capture is after the Cairo completion cutoff on the status date;
- the source write date equals the status date;
- the row has trades in that session;
- the previous-close date is strictly earlier.

``completed_session_bars`` turns only SESSION_ALIGNED rows into verification
bars. Nothing here repairs a row or grants admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as clock, timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
import json
import time
from typing import Any, Callable
from urllib import error, parse, request
from zoneinfo import ZoneInfo

from app.data.provider import ProviderResponse

PROVIDER = "egx_official_market_watch"
CAIRO = ZoneInfo("Africa/Cairo")
SESSION_COMPLETE_AT = clock(16, 0)
CLOSED_STATUSES = frozenset({"closed"})
REQUIRED_ROW_FIELDS = ("isin", "reuters", "openPrice", "high", "low", "closePrice",
                       "volume", "lastTradeDate")
BFF_HEADERS = {"Accept": "application/json", "x-egx-bff-request": "1",
               "x-egx-bff-client": "web", "User-Agent": "Mozilla/5.0"}


class MarketWatchError(RuntimeError):
    """Fail-closed stop with a stable, secret-free code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MarketWatchSnapshot:
    captured_at: datetime
    status: str
    status_date: str
    status_response: ProviderResponse
    pages: tuple[ProviderResponse, ...]
    rows: tuple[dict, ...]
    total_count: int


class EGXMarketWatchProvider:
    def __init__(self, *, base_url: str = "https://beta.egx.com.eg", timeout_seconds: int = 30,
                 opener: Any | None = None, page_size: int = 50, max_attempts: int = 3,
                 backoff_seconds: float = 2.0, max_pages: int = 40,
                 sleep: Callable[[float], None] = time.sleep,
                 clock_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or request.build_opener(request.HTTPCookieProcessor(CookieJar()))
        self.page_size = page_size
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self._sleep = sleep
        self._now = clock_now
        self._warmed = False

    @property
    def name(self) -> str:
        return PROVIDER

    def _open(self, url: str, headers: dict) -> bytes:
        last = "UNKNOWN"
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._opener.open(request.Request(url, headers=headers),
                                       timeout=self.timeout_seconds) as response:
                    return response.read()
            except error.HTTPError as exc:
                last = f"HTTP_{exc.code}"
                if exc.code not in (429, 500, 502, 503, 504):
                    raise MarketWatchError(last) from exc
            except (error.URLError, TimeoutError, ConnectionError) as exc:
                last = type(exc).__name__
            if attempt < self.max_attempts:
                self._sleep(self.backoff_seconds * 2 ** (attempt - 1))
        raise MarketWatchError(f"UNAVAILABLE_AFTER_RETRIES:{last}")

    def _warm(self) -> None:
        if not self._warmed:
            self._open(self.base_url + "/en", {"User-Agent": "Mozilla/5.0"})
            self._warmed = True

    def _get(self, endpoint: str, params: dict | None = None) -> tuple[ProviderResponse, dict]:
        self._warm()
        query = ("?" + parse.urlencode(params)) if params else ""
        url = f"{self.base_url}/api/bff/egx/{endpoint}{query}"
        payload = self._open(url, BFF_HEADERS)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # The WAF answers rejected requests with an HTML page and HTTP 200.
            raise MarketWatchError(f"NON_JSON_RESPONSE:{endpoint}") from exc
        if not isinstance(document, dict) or document.get("success") is not True:
            raise MarketWatchError(f"UNSUCCESSFUL_RESPONSE:{endpoint}")
        page = params.get("Page", 0) if params else 0
        filename = f"{endpoint}-page-{page:04d}.json" if params else f"{endpoint}.json"
        return ProviderResponse(payload=payload, filename=filename, source_uri=url,
                                metadata={"endpoint": endpoint}), document

    def fetch_snapshot(self) -> MarketWatchSnapshot:
        captured_at = self._now()
        status_response, status_doc = self._get("market-status")
        status_data = status_doc.get("data") or {}
        status, status_date = status_data.get("status"), status_data.get("statusDate")
        if not isinstance(status, str) or not isinstance(status_date, str):
            raise MarketWatchError("MARKET_STATUS_SHAPE")
        pages, rows, total, page = [], [], None, 1
        while True:
            response, document = self._get(
                "market-watch", {"Page": page, "PageSize": self.page_size})
            data = document.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise MarketWatchError("MARKET_WATCH_SHAPE")
            count, pages_total = data.get("totalCount"), data.get("totalPages")
            if type(count) is not int or type(pages_total) is not int or count < 0:
                raise MarketWatchError("MARKET_WATCH_PAGINATION_SHAPE")
            if total is None:
                total = count
            elif count != total:
                raise MarketWatchError("TOTAL_COUNT_CHANGED_DURING_CAPTURE")
            pages.append(response)
            rows.extend(data["data"])
            if page >= pages_total or not data["data"]:
                break
            page += 1
            if page > self.max_pages:
                raise MarketWatchError("TOO_MANY_PAGES")
        if len(rows) != total:
            raise MarketWatchError(f"ROW_COUNT_MISMATCH:{len(rows)}!={total}")
        isins = [row.get("isin") for row in rows]
        if len(set(isins)) != len(isins):
            raise MarketWatchError("DUPLICATE_ISIN")
        return MarketWatchSnapshot(captured_at=captured_at, status=status,
                                   status_date=status_date, status_response=status_response,
                                   pages=tuple(pages), rows=tuple(rows), total_count=total)


def _price(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


SESSION_ALIGNED, SECONDARY_STALE = "SESSION_ALIGNED", "SECONDARY_STALE"
NOT_TRADED, AMBIGUOUS = "NOT_TRADED_IN_SESSION", "UNVERIFIED_AMBIGUOUS_SESSION"


def _write_date(value):
    text = str(value or "")
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def row_observation(row: dict, *, status_date: date, captured_at: datetime, market_closed: bool) -> dict:
    """Times and observation-session status for one official row (pure; no repair)."""
    local = captured_at.astimezone(CAIRO)
    write_day = _write_date(row.get("writeTime"))
    try:
        previous_close_day = datetime.fromisoformat(str(row.get("lastTradeDate"))).date()
    except ValueError:
        previous_close_day = None
    times = {"capture_timestamp": captured_at.isoformat(), "market_status_date": status_date.isoformat(),
             "source_write_timestamp": str(row.get("writeTime")),
             "provider_last_trade_date": previous_close_day.isoformat() if previous_close_day else None,
             "provider_last_trade_date_meaning": "date of prevClose (previous close)"}
    if not market_closed or local.date() != status_date or local.time() < SESSION_COMPLETE_AT:
        return {**times, "status": AMBIGUOUS, "observation_session_date": None,
                "reason": "capture not after a closed, completed session"}
    if write_day is None or previous_close_day is None:
        return {**times, "status": AMBIGUOUS, "observation_session_date": None, "reason": "missing writeTime/lastTradeDate"}
    if write_day < status_date:
        return {**times, "status": SECONDARY_STALE, "observation_session_date": None,
                "reason": f"source write date {write_day} precedes session {status_date}"}
    if write_day > status_date or previous_close_day >= status_date:
        return {**times, "status": AMBIGUOUS, "observation_session_date": None,
                "reason": "write date or previous-close date not consistent with the session"}
    trades, volume = row.get("trades"), _price(row.get("volume"))
    if (trades is not None and not trades) or volume == 0:
        return {**times, "status": NOT_TRADED, "observation_session_date": None,
                "reason": "no trades in the session (official price carried)"}
    return {**times, "status": SESSION_ALIGNED, "observation_session_date": status_date.isoformat(),
            "reason": "closed post-cutoff capture, write date = session, trades > 0, previous close earlier"}


def completed_session_bars(snapshot: MarketWatchSnapshot) -> tuple[date, dict, dict]:
    """(session_date, bars by Reuters code, rejection reasons by Reuters code or ISIN).

    Raises MarketWatchError when the capture cannot describe a completed session.
    """
    if snapshot.status.strip().lower() not in CLOSED_STATUSES:
        raise MarketWatchError(f"SESSION_NOT_CLOSED:{snapshot.status}")
    try:
        session_date = datetime.fromisoformat(snapshot.status_date).date()
    except ValueError as exc:
        raise MarketWatchError("MARKET_STATUS_DATE_INVALID") from exc
    local = snapshot.captured_at.astimezone(CAIRO)
    if local.date() != session_date or local.time() < SESSION_COMPLETE_AT:
        raise MarketWatchError("CAPTURE_NOT_AFTER_SESSION_COMPLETION")
    bars, rejected = {}, {}
    for row in snapshot.rows:
        key = row.get("reuters") or row.get("isin") or "UNKNOWN"
        if any(row.get(field) in (None, "") for field in REQUIRED_ROW_FIELDS):
            rejected[key] = "MISSING_FIELD"
            continue
        observation = row_observation(row, status_date=session_date, captured_at=snapshot.captured_at,
                                      market_closed=True)
        if observation["status"] != SESSION_ALIGNED:
            rejected[key] = observation["status"]
            continue
        values = {name: _price(row[field]) for name, field in (
            ("open", "openPrice"), ("high", "high"), ("low", "low"),
            ("close", "closePrice"), ("volume", "volume"))}
        if any(value is None for value in values.values()):
            rejected[key] = "NON_NUMERIC_FIELD"
            continue
        if min(values["open"], values["high"], values["low"], values["close"]) <= 0 or values["volume"] < 0:
            rejected[key] = "NON_POSITIVE_FIELD"
            continue
        if not (values["low"] <= min(values["open"], values["close"])
                and max(values["open"], values["close"]) <= values["high"]):
            rejected[key] = "OHLC_INCONSISTENT"
            continue
        bars[key] = {"date": session_date.isoformat(), "open": str(values["open"]),
                     "high": str(values["high"]), "low": str(values["low"]),
                     "close": str(values["close"]), "volume": str(values["volume"]),
                     "isin": row["isin"]}
    return session_date, bars, rejected


def cross_session_consistency(earlier_rows, later_rows) -> dict:
    """Compare an earlier capture's closePrice with a later capture's prevClose by ISIN.

    Evidence only: full agreement across ISINs supports dating post-close rows
    by the official closed-session date; it changes nothing by itself.
    """
    earlier = {row.get("isin"): _price(row.get("closePrice")) for row in earlier_rows}
    later = {row.get("isin"): _price(row.get("prevClose")) for row in later_rows}
    common = sorted(isin for isin in set(earlier) & set(later) if isin)
    matched = [isin for isin in common
               if earlier[isin] is not None and earlier[isin] == later[isin]]
    return {"compared": len(common), "matched": len(matched),
            "mismatched": sorted(set(common) - set(matched)),
            "only_earlier": len(set(earlier) - set(later)),
            "only_later": len(set(later) - set(earlier)),
            "consistent": bool(common) and len(matched) == len(common)}
