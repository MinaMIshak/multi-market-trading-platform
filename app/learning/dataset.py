"""Session-aligned learning dataset: point-in-time features and forward outcome labels (LEARN-DATA-v2).

Columnar and memory-bounded: one typed array per feature and per label field,
indexed by row. A missing value is NaN (features, returns) or -1 (indices),
never zero, and ``FEATURE_UNAVAILABLE`` / ``MISSING_SESSION`` stay explicit.

Rules (unchanged from v1):
- Observed sessions are the market-wide dates on which at least
  ``max(10, 30% of symbols)`` series have a bar.
- Features at session t use bars dated ≤ t only. Cross-sectional and sector
  features use the same session's values.
- A label for horizon h at t needs the symbol's bars on each of the next h
  observed sessions. Otherwise the label is invalid (MISSING_SESSION, or
  NOT_MATURED when the future has not happened), and it is never
  forward-filled. ``label_end`` is the session on which the label matured.
- Fewer than MIN_HISTORY sessions of history: no row (FEATURE_UNAVAILABLE).

Extras (not model features of FEAT-v1): turnover, close, breakout flag, and the
session gap (open / previous close − 1) and intraday move (close / open − 1)
used by the US gap research and gates.

Survivorship note: the universe is symbols with a current admitted series.
"""
from __future__ import annotations

from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from math import isnan, log, nan

VERSION = "LEARN-DATA-v2"
FEATURE_SCHEMA = "FEAT-v1"
HORIZONS = (1, 2, 3, 5)
THRESHOLDS = (0.03, 0.05, 0.10, 0.15, 0.20)
THRESHOLD_KEYS = tuple(f"{int(t * 100)}" for t in THRESHOLDS)
MIN_HISTORY = 60
FEATURES = ("r1", "r5", "r20", "r60", "ema20_gap", "ema_stack", "atr_pct", "vol20", "rvol", "turnover_accel",
            "breakout_dist", "high52_dist", "rs20", "log_turnover20", "sector_r5", "sector_breadth5",
            "sector_turnover_accel")
EXTRA = ("turnover20", "turnover", "volume", "close", "breakout20", "gap1", "intraday1")
CROSS_SECTIONAL = ("rs20", "sector_r5", "sector_breadth5", "sector_turnover_accel")
STATUS = {0: "VALID", 1: "MISSING_SESSION", 2: "NOT_MATURED"}


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


def _num(value):
    return nan if value is None else float(value)


def symbol_features(series):
    """{date: feature dict} for one symbol from its own bars ≤ each date (sector/RS are added in build)."""
    return dict(_symbol_feature_rows(series))


def _symbol_feature_rows(series):
    bars = series.bars
    if len(bars) <= MIN_HISTORY:
        return
    opens = [b[1] for b in bars]
    closes = [b[4] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    volumes = [b[5] for b in bars]
    turnover = [c * v for c, v in zip(closes, volumes)]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
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
        prior_high = max(highs[i - 20:i])
        yield bars[i][0], {
            "r1": c / closes[i - 1] - 1, "r5": c / closes[i - 5] - 1, "r20": c / closes[i - 20] - 1,
            "r60": c / closes[i - 60] - 1, "ema20_gap": c / ema20[i] - 1,
            "ema_stack": 1.0 if c > ema20[i] > ema50[i] else -1.0 if c < ema20[i] < ema50[i] else 0.0,
            "atr_pct": (sum(trs) / 14) / c, "vol20": (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5,
            "rvol": volumes[i] / prior_vol if prior_vol > 0 else None,
            "turnover_accel": turnover5 / turnover20 if turnover20 > 0 else None,
            "breakout_dist": c / prior_high - 1, "high52_dist": c / max(highs[max(0, i - 249):i + 1]) - 1,
            "log_turnover20": log(turnover20) if turnover20 > 0 else None,
            "turnover20": turnover20, "turnover": turnover[i], "volume": volumes[i], "close": c,
            "breakout20": c >= prior_high,
            "gap1": opens[i] / closes[i - 1] - 1 if opens[i] > 0 else None,
            "intraday1": c / opens[i] - 1 if opens[i] > 0 else None}


class Dataset:
    """Columnar rows (symbol, observed session) with features, extras and per-horizon labels."""

    def __init__(self, sessions, tickers, isins, sectors):
        self.version, self.feature_schema = VERSION, FEATURE_SCHEMA
        self.sessions = sessions
        self.session_index = {d: k for k, d in enumerate(sessions)}
        self.tickers, self.isins, self.sectors = tickers, isins, sectors
        self.row_ticker, self.row_session = array("i"), array("i")
        self.f = {name: array("d") for name in FEATURES + EXTRA}
        self.ret = {h: array("d") for h in HORIZONS}
        self.mfe = {h: array("d") for h in HORIZONS}
        self.mae = {h: array("d") for h in HORIZONS}
        self.end = {h: array("i") for h in HORIZONS}      # session index of label maturity, -1 if invalid
        self.status = {h: array("b") for h in HORIZONS}

    @property
    def n(self):
        return len(self.row_ticker)

    def session(self, i):
        return self.sessions[self.row_session[i]]

    def ticker(self, i):
        return self.tickers[self.row_ticker[i]]

    def sector(self, i):
        return self.sectors[self.row_ticker[i]]

    def value(self, name, i):
        value = self.f[name][i]
        return None if isnan(value) else value

    def valid(self, h, i, cutoff_index=None):
        """A usable label: VALID and, under a cutoff, matured strictly before that session index."""
        return self.status[h][i] == 0 and (cutoff_index is None or self.end[h][i] < cutoff_index)

    def features(self, i):
        out = {name: self.value(name, i) for name in FEATURES + EXTRA}
        out["breakout20"] = bool(out["breakout20"]) if out["breakout20"] is not None else None
        return out

    def labels(self, i):
        out = {}
        for h in HORIZONS:
            code = self.status[h][i]
            if code:
                out[h] = {"status": STATUS[code]}
                continue
            ret = self.ret[h][i]
            out[h] = {"status": "VALID", "ret": ret, "mfe": self.mfe[h][i], "mae": self.mae[h][i],
                      "label_end": self.sessions[self.end[h][i]],
                      "hits": {k: ret >= t for t, k in zip(THRESHOLDS, THRESHOLD_KEYS)}}
        return out

    def row(self, i):
        t = self.row_ticker[i]
        return {"ticker": self.tickers[t], "isin": self.isins[t], "sector": self.sectors[t], "session": self.session(i),
                "features": self.features(i), "labels": self.labels(i)}

    def by_session(self):
        out = {}
        for i in range(self.n):
            out.setdefault(self.row_session[i], []).append(i)
        return out

    def rows_for(self, session):
        k = self.session_index.get(session)
        return [i for i in range(self.n) if self.row_session[i] == k] if k is not None else []

    def keep_sessions(self, keep):
        """A new Dataset restricted to the given sessions (memory release after fitting)."""
        idx = {self.session_index[d] for d in keep if d in self.session_index}
        out = Dataset(self.sessions, self.tickers, self.isins, self.sectors)
        for i in range(self.n):
            if self.row_session[i] in idx:
                out._copy_row(self, i)
        return out

    def _copy_row(self, other, i):
        self.row_ticker.append(other.row_ticker[i])
        self.row_session.append(other.row_session[i])
        for name in self.f:
            self.f[name].append(other.f[name][i])
        for h in HORIZONS:
            for target, source in ((self.ret, other.ret), (self.mfe, other.mfe), (self.mae, other.mae),
                                   (self.end, other.end), (self.status, other.status)):
                target[h].append(source[h][i])


def build(series_list, *, sector_min=3):
    sessions = observed_sessions(series_list)
    session_index = {d: k for k, d in enumerate(sessions)}
    ds = Dataset(sessions, [s.ticker for s in series_list], [s.isin for s in series_list],
                 [s.sector for s in series_list])
    for t, series in enumerate(series_list):
        bar_index = {bar[0]: k for k, bar in enumerate(series.bars)}
        for day, feats in _symbol_feature_rows(series):
            k = session_index.get(day)
            if k is None:
                continue
            ds.row_ticker.append(t)
            ds.row_session.append(k)
            for name in FEATURES + EXTRA:
                ds.f[name].append(nan if name in CROSS_SECTIONAL else _num(feats[name]))
            close = feats["close"]
            for h in HORIZONS:
                future = sessions[k + 1:k + 1 + h]
                if len(future) < h:
                    _no_label(ds, h, 2)
                    continue
                if any(d not in bar_index for d in future):
                    _no_label(ds, h, 1)
                    continue
                window = [series.bars[bar_index[d]] for d in future]
                ds.ret[h].append(window[-1][4] / close - 1)
                ds.mfe[h].append(max(b[2] for b in window) / close - 1)
                ds.mae[h].append(min(b[3] for b in window) / close - 1)
                ds.end[h].append(k + h)
                ds.status[h].append(0)
    _cross_sectional(ds, sector_min=sector_min)
    return ds


def _no_label(ds, h, code):
    ds.ret[h].append(nan)
    ds.mfe[h].append(nan)
    ds.mae[h].append(nan)
    ds.end[h].append(-1)
    ds.status[h].append(code)


def _cross_sectional(ds, *, sector_min):
    f = ds.f
    for members in ds.by_session().values():
        r20s = sorted(f["r20"][i] for i in members)
        n = len(r20s)
        mid = (r20s[n // 2] if n % 2 else (r20s[n // 2 - 1] + r20s[n // 2]) / 2) if n else nan
        sectors = {}
        for i in members:
            f["rs20"][i] = f["r20"][i] - mid
            sector = ds.sectors[ds.row_ticker[i]]
            if sector:
                sectors.setdefault(sector, []).append(i)
        for group in sectors.values():
            if len(group) < sector_min:
                continue
            r5 = sum(f["r5"][g] for g in group) / len(group)
            breadth = sum(1 for g in group if f["r5"][g] > 0) / len(group)
            accel = [f["turnover_accel"][g] for g in group if not isnan(f["turnover_accel"][g])]
            accel_mean = sum(accel) / len(accel) if accel else nan
            for g in group:
                f["sector_r5"][g], f["sector_breadth5"][g], f["sector_turnover_accel"][g] = r5, breadth, accel_mean


def forward_labels(series, sessions):
    """Per-series labels against observed sessions (tests and audits; same rules as ``build``)."""
    index = {bar[0]: k for k, bar in enumerate(series.bars)}
    out = {}
    for bar in series.bars:
        day, close = bar[0], bar[4]
        position = bisect_right(sessions, day)
        if close <= 0 or position == 0 or sessions[position - 1] != day:
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
                         "hits": {k: ret >= t for t, k in zip(THRESHOLDS, THRESHOLD_KEYS)}}
        out[day] = labels
    return out


def label_counts(ds):
    counts = {"observations": ds.n}
    for h in HORIZONS:
        status = ds.status[h]
        counts[f"h{h}_valid"] = sum(1 for s in status if s == 0)
        counts[f"h{h}_excluded_missing_session"] = sum(1 for s in status if s == 1)
    return counts
