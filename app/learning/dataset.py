"""Session-aligned learning dataset: point-in-time features and forward outcome labels (LEARN-DATA-v1).

Inputs are validated, session-dated primary bars only (one series per symbol).
Rules:
- Observed sessions are the market-wide dates on which at least
  ``max(10, 30% of symbols)`` series have a bar. A date with sparse bars is not
  a session for labelling.
- Features at session t use bars dated ≤ t only. Cross-sectional and sector
  features use the same session's values from all symbols.
- A label for horizon h at t needs the symbol's bars on each of the next h
  observed sessions. Otherwise the label is invalid (``MISSING_SESSION``) and
  never forward-filled. ``label_end`` records the session on which the label
  matured, so training can exclude labels not yet known.
- Missing history gives ``FEATURE_UNAVAILABLE``, never a default value.

Survivorship note: the universe is the set of symbols with a current admitted
series, so delisted names are absent. Historical metrics are labelled with
this caveat.
"""
from __future__ import annotations

from bisect import bisect_right
from math import log
from dataclasses import dataclass, field
from statistics import median

VERSION = "LEARN-DATA-v1"
FEATURE_SCHEMA = "FEAT-v1"
HORIZONS = (1, 2, 3, 5)
THRESHOLDS = (0.03, 0.05, 0.10, 0.15, 0.20)
MIN_HISTORY = 60
FEATURES = ("r1", "r5", "r20", "r60", "ema20_gap", "ema_stack", "atr_pct", "vol20", "rvol", "turnover_accel",
            "breakout_dist", "high52_dist", "rs20", "log_turnover20", "sector_r5", "sector_breadth5",
            "sector_turnover_accel")


@dataclass
class Series:
    ticker: str
    bars: list  # (date, open, high, low, close, volume) floats, ascending, unique dates
    isin: str | None = None
    sector: str | None = None
    meta: dict = field(default_factory=dict)


def observed_sessions(series_list, *, min_share=0.30, min_count=10):
    counts = {}
    for series in series_list:
        for bar in series.bars:
            counts[bar[0]] = counts.get(bar[0], 0) + 1
    floor = max(min_count, int(min_share * len(series_list)))
    return sorted(day for day, n in counts.items() if n >= floor)


def _ema(values, window):
    k = 2.0 / (window + 1)
    current = sum(values[:window]) / window
    out = [None] * (window - 1) + [current]
    for value in values[window:]:
        current = value * k + current * (1 - k)
        out.append(current)
    return out


def symbol_features(series):
    """{date: feature dict} for one symbol from its own bars ≤ each date (sector/RS added later)."""
    bars = series.bars
    if len(bars) <= MIN_HISTORY:
        return {}
    closes = [b[4] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    volumes = [b[5] for b in bars]
    turnover = [c * v for c, v in zip(closes, volumes)]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    out = {}
    for i in range(MIN_HISTORY, len(bars)):
        c = closes[i]
        if c <= 0 or closes[i - 1] <= 0:
            continue
        prior_vol = sum(volumes[i - 20:i]) / 20
        turnover20 = sum(turnover[i - 19:i + 1]) / 20
        turnover5 = sum(turnover[i - 4:i + 1]) / 5
        returns = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1) if closes[j - 1] > 0]
        mean_r = sum(returns) / len(returns)
        trs = [max(highs[j] - lows[j], abs(highs[j] - closes[j - 1]), abs(lows[j] - closes[j - 1]))
               for j in range(i - 13, i + 1)]
        window_high = max(highs[max(0, i - 249):i + 1])

        out[bars[i][0]] = {
            "r1": c / closes[i - 1] - 1, "r5": c / closes[i - 5] - 1, "r20": c / closes[i - 20] - 1,
            "r60": c / closes[i - 60] - 1, "ema20_gap": c / ema20[i] - 1,
            "ema_stack": 1.0 if c > ema20[i] > ema50[i] else -1.0 if c < ema20[i] < ema50[i] else 0.0,
            "atr_pct": (sum(trs) / 14) / c, "vol20": (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5,
            "rvol": volumes[i] / prior_vol if prior_vol > 0 else None,
            "turnover_accel": turnover5 / turnover20 if turnover20 > 0 else None,
            "breakout_dist": c / max(highs[i - 20:i]) - 1, "high52_dist": c / window_high - 1,
            "log_turnover20": log(turnover20) if turnover20 > 0 else None,
            "turnover20": turnover20, "turnover": turnover[i], "volume": volumes[i], "close": c,
            "breakout20": c >= max(highs[i - 20:i]), "history_bars": i + 1}
    return out


def forward_labels(series, sessions):
    """{date: label dict} using the next h observed sessions; invalid when a session bar is missing."""
    index = {bar[0]: k for k, bar in enumerate(series.bars)}
    out = {}
    for k, bar in enumerate(series.bars):
        day, close = bar[0], bar[4]
        position = bisect_right(sessions, day)
        if close <= 0 or (position == 0 or sessions[position - 1] != day):
            continue
        labels = {}
        for h in HORIZONS:
            future = sessions[position:position + h]
            if len(future) < h or any(d not in index for d in future):
                labels[h] = {"status": "MISSING_SESSION" if len(future) == h else "NOT_MATURED"}
                continue
            window = [series.bars[index[d]] for d in future]
            ret = window[-1][4] / close - 1
            labels[h] = {"status": "VALID", "ret": ret, "mfe": max(b[2] for b in window) / close - 1,
                         "mae": min(b[3] for b in window) / close - 1, "label_end": future[-1],
                         "hits": {f"{int(t * 100)}": ret >= t for t in THRESHOLDS}}
        out[day] = labels
    return out


def build(series_list, *, sector_min=3):
    """Rows: one per (symbol, observed session) with features (where available) and labels."""
    sessions = observed_sessions(series_list)
    per_symbol = {s.ticker: symbol_features(s) for s in series_list}
    labels = {s.ticker: forward_labels(s, sessions) for s in series_list}
    rows = []
    by_session = {}
    for series in series_list:
        for day, feats in per_symbol[series.ticker].items():
            if day not in labels[series.ticker]:
                continue
            row = {"ticker": series.ticker, "isin": series.isin, "sector": series.sector, "session": day,
                   "features": dict(feats), "labels": labels[series.ticker][day]}
            rows.append(row)
            by_session.setdefault(day, []).append(row)
    for day, members in by_session.items():
        r20s = [m["features"]["r20"] for m in members]
        mid = median(r20s) if r20s else None
        sectors = {}
        for m in members:
            sectors.setdefault(m["sector"], []).append(m)
        for m in members:
            f = m["features"]
            f["rs20"] = f["r20"] - mid if mid is not None else None
            group = sectors.get(m["sector"]) if m["sector"] else None
            if group and len(group) >= sector_min:
                f["sector_r5"] = sum(g["features"]["r5"] for g in group) / len(group)
                f["sector_breadth5"] = sum(1 for g in group if g["features"]["r5"] > 0) / len(group)
                accel = [g["features"]["turnover_accel"] for g in group if g["features"]["turnover_accel"]]
                f["sector_turnover_accel"] = sum(accel) / len(accel) if accel else None
            else:
                f["sector_r5"] = f["sector_breadth5"] = f["sector_turnover_accel"] = None
    rows.sort(key=lambda r: (r["session"], r["ticker"]))
    return {"version": VERSION, "feature_schema": FEATURE_SCHEMA, "sessions": sessions, "rows": rows}


def label_counts(dataset):
    counts = {"observations": len(dataset["rows"])}
    for h in HORIZONS:
        valid = sum(1 for r in dataset["rows"] if r["labels"].get(h, {}).get("status") == "VALID")
        missing = sum(1 for r in dataset["rows"] if r["labels"].get(h, {}).get("status") == "MISSING_SESSION")
        counts[f"h{h}_valid"], counts[f"h{h}_excluded_missing_session"] = valid, missing
    return counts
