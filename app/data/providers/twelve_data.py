"""Twelve Data adapter for EGX (MIC ``XCAI``) daily bars and reference equities.

Registry status: EVIDENCE_BLOCKED until the operator records the subscription
and usage-rights review (app/data/source_admission.py). A working adapter or
a valid key is not admission.

- Daily bars come from ``/time_series`` (interval ``1day``, ``mic_code=XCAI``,
  ``timezone=Africa/Cairo``). They are mapped to the provider-neutral row
  contract (``date``, ``open``, ``high``, ``low``, ``close``, ``volume``) with
  no adjusted close and no invented values. Rows are filtered to the requested
  window and sorted. Duplicate dates, a non-XCAI instrument, a non-Cairo
  exchange timezone or missing fields fail closed for that symbol.
- ``prefetch`` batches up to ``batch_size`` symbols per request (Twelve Data
  bills credits per symbol). ``fetch_daily_bars`` then serves the same
  response without another call, so a run is one request per batch.
- A credit-based limiter keeps usage under ``credits_per_minute`` and
  ``daily_credit_budget``. Retries back off on HTTP 429/5xx and network errors.
  Authentication errors are never retried.
- The API key is sent only as a query parameter. Every recorded
  ``source_uri`` and error has it removed.
"""
from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import time
from typing import Any, Callable
from urllib import error, parse, request

from app.data.provider import ProviderResponse

PROVIDER = "twelve_data"
MIC = "XCAI"
EXCHANGE_TIMEZONE = "Africa/Cairo"
BASE_URL = "https://api.twelvedata.com"


class TwelveDataError(RuntimeError):
    """Fail-closed stop with a stable, secret-free code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TwelveDataAuthenticationError(TwelveDataError):
    pass


class CreditLimiter:
    """Sliding one-minute window plus a per-run budget, both in API credits."""

    def __init__(self, *, credits_per_minute: int, daily_credit_budget: int,
                 sleep: Callable[[float], None], monotonic: Callable[[], float]):
        if credits_per_minute < 1 or daily_credit_budget < 1:
            raise ValueError("credit limits must be positive")
        self.credits_per_minute = credits_per_minute
        self.remaining = daily_credit_budget
        self._sleep, self._monotonic = sleep, monotonic
        self._spent: deque = deque()

    def acquire(self, credits: int) -> None:
        if credits > self.credits_per_minute:
            raise TwelveDataError("BATCH_EXCEEDS_MINUTE_LIMIT")
        if credits > self.remaining:
            raise TwelveDataError("DAILY_CREDIT_BUDGET_EXHAUSTED")
        while True:
            now = self._monotonic()
            while self._spent and now - self._spent[0][0] >= 60:
                self._spent.popleft()
            used = sum(amount for _, amount in self._spent)
            if used + credits <= self.credits_per_minute:
                break
            self._sleep(60 - (now - self._spent[0][0]) + 0.5)
        self._spent.append((self._monotonic(), credits))
        self.remaining -= credits


def _number(value) -> str | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return str(number) if number.is_finite() else None


class TwelveDataProvider:
    def __init__(self, *, api_key: str | None, base_url: str = BASE_URL, opener: Any | None = None,
                 timeout_seconds: int = 30, batch_size: int = 8, credits_per_minute: int = 8,
                 daily_credit_budget: int = 800, max_attempts: int = 3, backoff_seconds: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        self.api_key = api_key.strip() if api_key else None
        self.base_url = base_url.rstrip("/")
        self._opener = opener or request.build_opener()
        self.timeout_seconds = timeout_seconds
        if not 1 <= batch_size <= credits_per_minute:
            raise ValueError("batch_size must be between 1 and credits_per_minute")
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.limiter = CreditLimiter(credits_per_minute=credits_per_minute,
                                     daily_credit_budget=daily_credit_budget,
                                     sleep=sleep, monotonic=monotonic)
        self._cache: dict[tuple[str, date, date], ProviderResponse] = {}

    @property
    def name(self) -> str:
        return PROVIDER

    def _redacted(self, endpoint: str, params: dict) -> str:
        public = {key: value for key, value in params.items() if key != "apikey"}
        return f"{self.base_url}/{endpoint}?{parse.urlencode(public)}"

    def _get(self, endpoint: str, params: dict, *, credits: int, authenticated: bool) -> tuple[bytes, Any]:
        query = dict(params)
        if authenticated:
            if not self.api_key:
                raise TwelveDataAuthenticationError("API_KEY_NOT_CONFIGURED")
            query["apikey"] = self.api_key
        url = f"{self.base_url}/{endpoint}?{parse.urlencode(query)}"
        last = "UNKNOWN"
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.acquire(credits)
            try:
                with self._opener.open(request.Request(url, headers={"Accept": "application/json"}),
                                       timeout=self.timeout_seconds) as response:
                    payload = response.read()
            except error.HTTPError as exc:
                last = f"HTTP_{exc.code}"
                if exc.code in (401, 403):
                    raise TwelveDataAuthenticationError(last) from None
                if exc.code not in (429, 500, 502, 503, 504):
                    raise TwelveDataError(last) from None
            except (error.URLError, TimeoutError, ConnectionError) as exc:
                last = type(exc).__name__
            else:
                try:
                    document = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise TwelveDataError("NON_JSON_RESPONSE") from None
                if isinstance(document, dict) and document.get("status") == "error":
                    code = document.get("code")
                    if code in (401, 403):
                        raise TwelveDataAuthenticationError(f"API_{code}")
                    if code != 429:
                        raise TwelveDataError(f"API_{code}")
                    last = "API_429"
                else:
                    return payload, document
            if attempt < self.max_attempts:
                self._sleep(max(self.backoff_seconds * 2 ** (attempt - 1), 60 if "429" in last else 0))
        raise TwelveDataError(f"UNAVAILABLE_AFTER_RETRIES:{last}")

    def fetch_reference_equities(self) -> tuple[ProviderResponse, list[dict]]:
        """All XCAI equities listed by the provider (reference data, no prices)."""
        params = {"mic_code": MIC}
        payload, document = self._get("stocks", params, credits=1, authenticated=bool(self.api_key))
        rows = document.get("data") if isinstance(document, dict) else None
        if not isinstance(rows, list) or not rows:
            raise TwelveDataError("REFERENCE_SHAPE")
        if any(not isinstance(row, dict) or row.get("mic_code") != MIC or not row.get("symbol")
               for row in rows):
            raise TwelveDataError("REFERENCE_NOT_XCAI")
        return ProviderResponse(payload=payload, filename="twelve-data-xcai-stocks.json",
                                source_uri=self._redacted("stocks", params),
                                record_count=len(rows), metadata={"mic_code": MIC}), rows

    def _series_rows(self, symbol: str, series: Any, start: date, end: date) -> list[dict]:
        if not isinstance(series, dict) or series.get("status") == "error":
            raise TwelveDataError(f"SERIES_ERROR:{symbol}")
        meta = series.get("meta") or {}
        if meta.get("mic_code") not in (None, MIC) or meta.get("exchange_timezone") not in (None, EXCHANGE_TIMEZONE):
            raise TwelveDataError(f"SERIES_NOT_XCAI_CAIRO:{symbol}")
        if meta.get("interval") not in (None, "1day"):
            raise TwelveDataError(f"SERIES_INTERVAL:{symbol}")
        values = series.get("values")
        if not isinstance(values, list):
            raise TwelveDataError(f"SERIES_SHAPE:{symbol}")
        rows, seen = [], set()
        for value in values:
            try:
                day = date.fromisoformat(str(value["datetime"])[:10])
            except (KeyError, TypeError, ValueError):
                raise TwelveDataError(f"SERIES_DATE:{symbol}") from None
            if len(str(value["datetime"])) != 10:
                raise TwelveDataError(f"SERIES_NOT_DAILY:{symbol}")
            if day in seen:
                raise TwelveDataError(f"SERIES_DUPLICATE_DATE:{symbol}")
            seen.add(day)
            if not start <= day <= end:
                continue
            row = {"date": day.isoformat()}
            for field in ("open", "high", "low", "close", "volume"):
                number = _number(value.get(field))
                if number is None:
                    raise TwelveDataError(f"SERIES_FIELD_{field.upper()}:{symbol}")
                row[field] = number
            rows.append(row)
        return sorted(rows, key=lambda item: item["date"])

    def prefetch(self, symbols, start_date: date, end_date: date) -> dict[str, str]:
        """Fetch symbols in batches; returns per-symbol error codes for failures."""
        pending = [s for s in dict.fromkeys(symbols) if (s, start_date, end_date) not in self._cache]
        failures = {}
        for offset in range(0, len(pending), self.batch_size):
            batch = pending[offset:offset + self.batch_size]
            params = {"symbol": ",".join(batch), "mic_code": MIC, "interval": "1day",
                      "start_date": start_date.isoformat(),
                      "end_date": (end_date + timedelta(days=1)).isoformat(),
                      "timezone": EXCHANGE_TIMEZONE, "order": "ASC", "outputsize": 5000}
            payload, document = self._get("time_series", params, credits=len(batch), authenticated=True)
            series_by_symbol = {batch[0]: document} if len(batch) == 1 else document
            if not isinstance(series_by_symbol, dict):
                raise TwelveDataError("BATCH_SHAPE")
            for symbol in batch:
                try:
                    rows = self._series_rows(symbol, series_by_symbol.get(symbol), start_date, end_date)
                except TwelveDataError as exc:
                    failures[symbol] = exc.code
                    continue
                body = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
                single = {k: v for k, v in params.items()}
                single["symbol"] = symbol
                self._cache[(symbol, start_date, end_date)] = ProviderResponse(
                    payload=body, filename=f"{symbol}-{start_date}-{end_date}-1day.json",
                    source_uri=self._redacted("time_series", single), record_count=len(rows),
                    # Deterministic for identical content: the immutable ingestion
                    # record compares metadata, and records its own receipt time.
                    metadata={"provider": PROVIDER, "mic_code": MIC, "interval": "1day",
                              "exchange_timezone": EXCHANGE_TIMEZONE})
        return failures

    def fetch_daily_bars(self, *, symbol: str, start_date: date, end_date: date) -> ProviderResponse:
        key = (symbol, start_date, end_date)
        if key not in self._cache:
            failures = self.prefetch([symbol], start_date, end_date)
            if symbol in failures:
                raise TwelveDataError(failures[symbol])
        return self._cache[key]

    def fetch_intraday_bars(self, **kwargs):
        raise TwelveDataError("INTRADAY_NOT_IMPLEMENTED")

    def fetch_corporate_actions(self, **kwargs):
        raise TwelveDataError("CORPORATE_ACTIONS_NOT_IMPLEMENTED")
