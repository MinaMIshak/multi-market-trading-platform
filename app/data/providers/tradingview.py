"""TradingView (tvdatafeed protocol) adapter for EGX daily bars and symbol discovery.

Provider ``tradingview_tvdatafeed_egx``: an unofficial client of TradingView's
public chart websocket, used anonymously. Data vendor as resolved by
TradingView: ICE. Rights: no contractual licence; accepted by the operator for
internal Paper/Shadow research only (see app/data/source_admission.py).

- Discovery pages TradingView's symbol search (``exchange=EGX``,
  ``search_type=stocks``), which returns ticker, description and ISIN.
- Daily series are fetched by ``tools/tradingview_fetch.py`` under the
  dedicated tooling interpreter, which returns the raw websocket stream.
  Frames are decoded here as JSON (no regex scraping). ``symbol_resolved``
  must say EGX, Africa/Cairo, EGP and stock. Bar epochs are converted
  explicitly to the Africa/Cairo session date. Duplicate dates, non-finite
  or missing values fail closed. Bars after ``end_date`` (an unfinished
  session) are dropped, never stored.
- Prices are TradingView's split-adjusted series (``adjustment=splits``),
  recorded as ``price_adjustment`` in provenance. They are not unadjusted
  exchange prices and are never mixed with another provider's series.
- The native stream is kept under ``evidence_dir`` (it contains random session
  ids, so it is not part of the deterministic ingestion metadata).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Callable
from urllib import parse, request
from zoneinfo import ZoneInfo

from app.data.provider import ProviderResponse

PROVIDER = "tradingview_tvdatafeed_egx"
EXCHANGE = "EGX"
CAIRO = ZoneInfo("Africa/Cairo")
SEARCH_URL = "https://symbol-search.tradingview.com/symbol_search/v3/"
SEARCH_HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://www.tradingview.com",
                  "Referer": "https://www.tradingview.com/"}
FRAME = re.compile(r"~m~(\d+)~m~")


class TradingViewError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def discover_equities(*, opener=None, sleep: Callable[[float], None] = time.sleep,
                      delay_seconds: float = 1.0, max_pages: int = 40) -> tuple[list[bytes], list[dict]]:
    """All EGX stocks listed by TradingView symbol search (raw pages, rows)."""
    opener = opener or request.build_opener()
    pages, rows, start = [], [], 0
    for _ in range(max_pages):
        query = parse.urlencode({"text": "", "exchange": EXCHANGE, "search_type": "stocks", "start": start})
        with opener.open(request.Request(f"{SEARCH_URL}?{query}", headers=SEARCH_HEADERS), timeout=30) as response:
            payload = response.read()
        try:
            document = json.loads(payload)
        except ValueError:
            raise TradingViewError("SEARCH_NON_JSON") from None
        batch = document.get("symbols") if isinstance(document, dict) else None
        if not isinstance(batch, list):
            raise TradingViewError("SEARCH_SHAPE")
        pages.append(payload)
        rows.extend(row for row in batch if isinstance(row, dict) and row.get("exchange") == EXCHANGE)
        remaining = document.get("symbols_remaining", 0)
        if not batch or not remaining:
            return pages, rows
        start += len(batch)
        sleep(delay_seconds)
    raise TradingViewError("SEARCH_TOO_MANY_PAGES")


def parse_stream(raw: str) -> dict:
    """Decode ``~m~<len>~m~`` frames; return resolved symbol info, bars and errors."""
    frames, position = [], 0
    while True:
        match = FRAME.search(raw, position)
        if not match:
            break
        length = int(match.group(1))
        body = raw[match.end():match.end() + length]
        position = match.end() + length
        frames.append(body)
    resolved, bars, errors = None, {}, []
    for body in frames:
        if not body.startswith("{"):
            continue  # heartbeat frames
        try:
            message = json.loads(body)
        except ValueError:
            errors.append("UNDECODABLE_FRAME")
            continue
        kind, params = message.get("m"), message.get("p") or []
        if kind == "symbol_resolved" and len(params) >= 3 and isinstance(params[2], dict):
            resolved = params[2]
        elif kind in ("timescale_update", "du") and len(params) >= 2 and isinstance(params[1], dict):
            for item in (params[1].get("s1") or {}).get("s", []):
                values = item.get("v") if isinstance(item, dict) else None
                if isinstance(values, list) and len(values) >= 6:
                    bars[item.get("i")] = values
        elif kind in ("symbol_error", "series_error", "critical_error", "protocol_error"):
            errors.append(f"{kind}:{params[1] if len(params) > 1 else ''}")
    return {"resolved": resolved, "bars": [bars[key] for key in sorted(bars)], "errors": errors}


def _number(value) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return number if number.is_finite() else None


def daily_rows(parsed: dict, *, start_date: date, end_date: date) -> list[dict]:
    resolved = parsed["resolved"]
    if not isinstance(resolved, dict):
        raise TradingViewError("SYMBOL_NOT_RESOLVED")
    if (resolved.get("exchange") != EXCHANGE or resolved.get("timezone") != "Africa/Cairo"
            or resolved.get("currency_code") != "EGP" or resolved.get("type") != "stock"):
        raise TradingViewError("RESOLVED_NOT_EGX_CAIRO_EGP_STOCK")
    rows, seen = [], set()
    for values in parsed["bars"]:
        epoch = _number(values[0])
        if epoch is None:
            raise TradingViewError("BAR_TIME_INVALID")
        day = datetime.fromtimestamp(float(epoch), timezone.utc).astimezone(CAIRO).date()
        if day in seen:
            raise TradingViewError("DUPLICATE_SESSION_DATE")
        seen.add(day)
        if not start_date <= day <= end_date:
            continue
        numbers = [_number(v) for v in values[1:6]]
        if any(n is None for n in numbers):
            raise TradingViewError("BAR_VALUE_NOT_FINITE")
        rows.append({"date": day.isoformat(), "open": str(numbers[0]), "high": str(numbers[1]),
                     "low": str(numbers[2]), "close": str(numbers[3]), "volume": str(numbers[4])})
    return sorted(rows, key=lambda row: row["date"])


class TradingViewProvider:
    def __init__(self, *, python_path: str, fetch_script: str, evidence_dir: Path | None = None,
                 n_bars: int = 750, timeout_seconds: int = 60, max_attempts: int = 3,
                 backoff_seconds: float = 5.0, delay_seconds: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, runner=subprocess.run):
        self.python_path, self.fetch_script = python_path, fetch_script
        self.evidence_dir = evidence_dir
        self.n_bars = n_bars
        self.timeout_seconds, self.max_attempts = timeout_seconds, max_attempts
        self.backoff_seconds, self.delay_seconds = backoff_seconds, delay_seconds
        self._sleep, self._runner = sleep, runner
        self._streams: dict[str, dict] = {}
        self.resolved: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return PROVIDER

    def _fetch_stream(self, symbol: str) -> dict:
        if symbol in self._streams:
            return self._streams[symbol]
        last = "UNKNOWN"
        for attempt in range(1, self.max_attempts + 1):
            try:
                completed = self._runner([self.python_path, self.fetch_script, f"{EXCHANGE}:{symbol}",
                                          str(self.n_bars)], capture_output=True, text=True,
                                         timeout=self.timeout_seconds, check=False)
                document = json.loads(completed.stdout or "{}")
            except subprocess.TimeoutExpired:
                last = "TIMEOUT"
            except ValueError:
                last = "HELPER_OUTPUT_INVALID"
            else:
                if completed.returncode == 0 and isinstance(document.get("raw"), str):
                    self._sleep(self.delay_seconds)
                    self._streams[symbol] = document
                    return document
                last = f"HELPER_{document.get('error', 'FAILED')}"
            if attempt < self.max_attempts:
                self._sleep(self.backoff_seconds * 2 ** (attempt - 1))
        raise TradingViewError(f"FETCH_FAILED:{last}")

    def _keep_evidence(self, symbol: str, document: dict) -> str | None:
        if self.evidence_dir is None:
            return None
        payload = json.dumps(document, sort_keys=True).encode()
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.evidence_dir / datetime.now(CAIRO).date().isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{symbol}-{digest[:16]}.json"
        if not target.exists():
            target.write_bytes(payload)
            target.chmod(0o444)
        return str(target)

    def fetch_daily_bars(self, *, symbol: str, start_date: date, end_date: date) -> ProviderResponse:
        document = self._fetch_stream(symbol)
        parsed = parse_stream(document["raw"])
        rows = daily_rows(parsed, start_date=start_date, end_date=end_date)
        resolved = parsed["resolved"]
        self.resolved[symbol] = {"isin": resolved.get("isin"), "name": resolved.get("name"),
                                 "provider_id": resolved.get("provider_id"),
                                 "evidence": self._keep_evidence(symbol, document),
                                 "stream_errors": parsed["errors"]}
        body = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        return ProviderResponse(
            payload=body, filename=f"{symbol}-{start_date}-{end_date}-1D.json",
            source_uri=f"tradingview:wss/{EXCHANGE}:{symbol}?interval=1D&adjustment=splits&n_bars={self.n_bars}",
            record_count=len(rows),
            metadata={"provider": PROVIDER, "exchange": EXCHANGE, "interval": "1D",
                      "price_adjustment": "splits", "exchange_timezone": "Africa/Cairo",
                      "data_vendor": resolved.get("provider_id"), "access": "UNOFFICIAL_CLIENT_ANONYMOUS"})

    def fetch_intraday_bars(self, **kwargs):
        raise TradingViewError("INTRADAY_NOT_IMPLEMENTED")

    def fetch_corporate_actions(self, **kwargs):
        raise TradingViewError("CORPORATE_ACTIONS_NOT_IMPLEMENTED")
