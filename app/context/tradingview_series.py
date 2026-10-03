"""TradingView daily series for markets beyond EGX equities (context and US).

The anonymous helper (tools/tradingview_fetch.py) returns the raw websocket
stream, decoded by app.data.providers.tradingview.parse_stream. This module
adds explicit, per-instrument rules:

- the resolved symbol must match the spec (type, timezone, currency when
  given), or the series fails closed;
- trade date: exchange sessions use the local bar date; 24-hour markets that
  open the previous evening (FX, spot gold) use the roll rule: a bar opening at
  or after ``roll`` local time belongs to the next calendar day;
- only completed sessions are kept: for sessions, the bar date is before the
  local date or the local time is past ``close`` (+ buffer); for roll markets,
  the trade date is before the current trade date;
- duplicate trade dates and non-finite values fail closed.

Rights: NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED (docs/SOURCE_DECISION_MATRIX.md).
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.data.providers.tradingview import parse_stream

PROVIDER = "tradingview_tvdatafeed"
RIGHTS = "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED"
CLOSE_BUFFER = timedelta(minutes=20)


class SeriesError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MarketSpec:
    key: str
    symbol: str
    title: str
    group: str
    unit: str
    kind: str            # resolved "type"
    tz: str
    calendar: str        # NYSE / EGX / FX
    currency: str | None = None
    close: dtime | None = None   # exchange session close (local)
    roll: dtime | None = None    # 24h-market roll (local)
    nature: str = "MARKET_QUOTE"


CONTEXT_MARKETS = (
    MarketSpec("XAUUSD", "OANDA:XAUUSD", "Gold spot (OANDA)", "Gold", "USD/oz", "commodity", "America/New_York",
               "FX", "USD", roll=dtime(17, 0)),
    MarketSpec("GOLD_TVC", "TVC:GOLD", "Gold spot composite (TVC)", "Gold", "USD/oz", "commodity",
               "America/New_York", "FX", "USD", roll=dtime(17, 0)),
    MarketSpec("UKOIL", "TVC:UKOIL", "Brent crude front-month futures (continuous)", "Brent", "USD/bbl",
               "commodity", "Europe/London", "FX", "USD", roll=dtime(23, 0), nature="FUTURES_REFERENCE"),
    MarketSpec("USDEGP", "FX_IDC:USDEGP", "USD/EGP market rate (ICE)", "FX", "EGP per USD", "forex", "Etc/UTC",
               "FX", "EGP", roll=dtime(22, 0)),
    MarketSpec("EGX30", "EGX:EGX30", "EGX 30 index", "Equity indices", "points", "index", "Africa/Cairo", "EGX",
               close=dtime(14, 30)),
    MarketSpec("SPX", "SP:SPX", "S&P 500 index", "Equity indices", "points", "index", "America/New_York", "NYSE",
               close=dtime(16, 0)),
)
BY_KEY = {spec.key: spec for spec in CONTEXT_MARKETS}


def _decimal(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return number if number.is_finite() else None


def trade_date(local: datetime, spec: MarketSpec) -> date:
    if spec.roll is not None and local.time() >= spec.roll:
        return local.date() + timedelta(days=1)
    return local.date()


def current_trade_date(now: datetime, spec: MarketSpec) -> date:
    """The trade date whose session is (or would be) in progress at ``now``."""
    local = now.astimezone(ZoneInfo(spec.tz))
    if spec.roll is not None:
        return trade_date(local, spec)
    closed = local.time() >= (datetime.combine(local.date(), spec.close) + CLOSE_BUFFER).time()
    return local.date() + timedelta(days=1) if closed else local.date()


def completed_bars(parsed: dict, spec: MarketSpec, *, now: datetime) -> list[dict]:
    resolved = parsed.get("resolved")
    if not isinstance(resolved, dict):
        raise SeriesError("SYMBOL_NOT_RESOLVED")
    if resolved.get("type") != spec.kind or resolved.get("timezone") != spec.tz:
        raise SeriesError("RESOLVED_MISMATCH")
    if spec.currency and resolved.get("currency_code") != spec.currency:
        raise SeriesError("RESOLVED_CURRENCY_MISMATCH")
    zone, current = ZoneInfo(spec.tz), current_trade_date(now, spec)
    rows, seen = [], set()
    for values in parsed["bars"]:
        epoch = _decimal(values[0])
        if epoch is None:
            raise SeriesError("BAR_TIME_INVALID")
        day = trade_date(datetime.fromtimestamp(float(epoch), timezone.utc).astimezone(zone), spec)
        if day in seen:
            raise SeriesError("DUPLICATE_TRADE_DATE")
        seen.add(day)
        if day >= current:
            continue  # session not completed: never stored
        numbers = [_decimal(v) for v in values[1:6]]
        if any(n is None for n in numbers[:4]):
            raise SeriesError("BAR_VALUE_NOT_FINITE")
        o, h, l, c = numbers[:4]
        if not (l <= min(o, c) and h >= max(o, c) and l > 0):
            raise SeriesError("OHLC_RELATION_INVALID")
        rows.append({"date": day.isoformat(), "open": str(o), "high": str(h), "low": str(l), "close": str(c),
                     "volume": None if numbers[4] is None else str(numbers[4])})
    return sorted(rows, key=lambda row: row["date"])


def fetch_stream(symbol, *, python_path, fetch_script, n_bars=420, timeout=60, max_attempts=3, backoff=5.0,
                 delay=1.0, runner=subprocess.run, sleep=time.sleep) -> dict:
    last = "UNKNOWN"
    for attempt in range(1, max_attempts + 1):
        try:
            completed = runner([python_path, fetch_script, symbol, str(n_bars)], capture_output=True, text=True,
                               timeout=timeout, check=False)
            document = json.loads(completed.stdout or "{}")
        except subprocess.TimeoutExpired:
            last = "TIMEOUT"
        except ValueError:
            last = "HELPER_OUTPUT_INVALID"
        else:
            if completed.returncode == 0 and isinstance(document.get("raw"), str):
                sleep(delay)
                return document
            last = f"HELPER_{document.get('error', 'FAILED')}"
        if attempt < max_attempts:
            sleep(backoff * 2 ** (attempt - 1))
    raise SeriesError(f"FETCH_FAILED:{last}")


def closes(rows):
    return [(date.fromisoformat(row["date"]), Decimal(row["close"])) for row in rows]
