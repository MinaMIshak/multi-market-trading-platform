"""US benchmark series (SPY, QQQ) for benchmark-relative evaluation; same validated path as US equities.

Stored immutably under ``<us-data>/us/benchmarks/<session>/<SYMBOL>.json``. Benchmarks are
evaluation references only, never ranking inputs, and never shared with EGX.
"""
from __future__ import annotations

import glob
import json
from datetime import time as dtime
from pathlib import Path

from app.context.fetch import store_raw
from app.context.tradingview_series import MarketSpec, SeriesError, completed_bars, fetch_stream
from app.data.providers.tradingview import parse_stream

BENCHMARKS = (MarketSpec("SPY", "AMEX:SPY", "SPDR S&P 500 ETF", "Benchmark", "USD", "fund", "America/New_York",
                         "NYSE", "USD", close=dtime(16, 0)),
              MarketSpec("QQQ", "NASDAQ:QQQ", "Invesco QQQ Trust", "Benchmark", "USD", "fund", "America/New_York",
                         "NYSE", "USD", close=dtime(16, 0)))


def fetch_and_store(data_root, *, now, tv_python, tv_script, fetch=fetch_stream):
    out = {}
    for spec in BENCHMARKS:
        try:
            document = fetch(spec.symbol, python_path=tv_python, fetch_script=tv_script, n_bars=300, delay=0.3)
            digest, _ = store_raw(data_root, "tradingview_us", json.dumps(document, sort_keys=True).encode(), "json")
            rows = completed_bars(parse_stream(document["raw"]), spec, now=now)
        except (SeriesError, KeyError, ValueError) as exc:
            out[spec.key] = {"status": "UNAVAILABLE", "reason": getattr(exc, "code", str(exc)[:80])}
            continue
        if not rows:
            out[spec.key] = {"status": "UNAVAILABLE", "reason": "NO_COMPLETED_BARS"}
            continue
        folder = Path(data_root) / "us" / "benchmarks" / rows[-1]["date"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{spec.key}.json"
        payload = json.dumps({"symbol": spec.key, "rows": rows, "raw_sha256": digest}, sort_keys=True).encode()
        try:
            with path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError:
            pass
        out[spec.key] = {"status": "AVAILABLE", "latest": rows[-1]["date"], "bars": len(rows)}
    return out


def load(data_root):
    """{symbol: {date: close}} from the newest stored benchmark artifact per symbol."""
    out = {}
    for spec in BENCHMARKS:
        paths = sorted(glob.glob(str(Path(data_root) / "us" / "benchmarks" / "*" / f"{spec.key}.json")))
        if paths:
            rows = json.loads(Path(paths[-1]).read_text())["rows"]
            out[spec.key] = {r["date"]: float(r["close"]) for r in rows}
    return out


def forward_returns(sessions, closes, horizon=3):
    """{session_index: close[k+h] / close[k] − 1} on the market's observed sessions; missing dates are skipped."""
    out = {}
    for k, day in enumerate(sessions[:-horizon] if horizon else sessions):
        later = sessions[k + horizon]
        if day in closes and later in closes and closes[day] > 0:
            out[k] = closes[later] / closes[day] - 1
    return out
