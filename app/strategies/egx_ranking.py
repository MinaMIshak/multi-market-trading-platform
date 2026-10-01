"""EGX-RANK-v1: deterministic ranking and classification of daily evidence.

Classes (Paper/Shadow research only; a candidate is not a fill):
STRONG_CANDIDATE, CANDIDATE, WATCHLIST, NO_TRADE.

Rules are evaluated per symbol on bars dated on or before the scored session
only (no lookahead).

Hard gates (any failure gives NO_TRADE with the reason; a high score never
overrides a gate):
- SOURCE_NOT_ADMITTED: the daily source registry does not admit the provider;
- DATA_NOT_CURRENT: freshness is not CURRENT (from verified sessions);
- LAST_BAR_NOT_SESSION: the newest bar is not the scored session;
- INSUFFICIENT_HISTORY: fewer than ``MIN_BARS`` valid bars;
- ILLIQUID: 20-session average traded value below ``MIN_TRADED_VALUE`` EGP;
- ZERO_VOLUME_SESSION: no volume on the scored session.

Score (0-100) = trend (30) + momentum (25) + breakout (15) + volume (15) +
liquidity (15), each from explicit thresholds below.
- STRONG_CANDIDATE: score >= 75, trend UP, breakout, volume ratio >= 1.5;
- CANDIDATE: score >= 60 and trend UP;
- WATCHLIST: score >= 45, or trend UP;
- NO_TRADE: otherwise (LOW_SCORE, or DOWNTREND).

Plan (computed for every gated symbol, actionable only for candidates):
- entry zone: close +/- 0.5 % (the SWING Q03 entry band), next eligible
  session only;
- stop: close - max(1.5 x ATR14, 3 % of close);
- targets: T1 = close + 1.5 R, T2 = close + 3 R (R = close - stop).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

VERSION = "EGX-RANK-v1"
MIN_BARS = 60
MIN_TRADED_VALUE = Decimal("1000000")
ENTRY_BAND = Decimal("0.005")
STOP_FLOOR = Decimal("0.03")
ATR_MULTIPLE = Decimal("1.5")
T1_R, T2_R = Decimal("1.5"), Decimal("3")
CLASSES = ("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST", "NO_TRADE")


@dataclass(frozen=True)
class Bar:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def _ema(values, window):
    k = 2.0 / (window + 1)
    current = sum(values[:window]) / window
    for value in values[window:]:
        current = value * k + current * (1 - k)
    return current


def _price(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def features(bars: list[Bar]) -> dict:
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    volumes = [float(b.volume) for b in bars]
    close = closes[-1]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    trend = "UP" if close > ema20 > ema50 else "DOWN" if close < ema20 < ema50 else "NEUTRAL"
    momentum = close / closes[-21] - 1 if closes[-21] else 0.0
    prior_high = max(highs[-21:-1])
    breakout = close >= prior_high
    average_volume = mean(volumes[-21:-1])
    volume_ratio = volumes[-1] / average_volume if average_volume else 0.0
    traded_value = mean(c * v for c, v in zip(closes[-20:], volumes[-20:]))
    ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
              for i in range(len(bars) - 14, len(bars))]
    atr14 = mean(ranges)
    return {"close": close, "ema20": ema20, "ema50": ema50, "trend": trend, "momentum_20": momentum,
            "high_20_prior": prior_high, "breakout_20": breakout, "volume_ratio_20": volume_ratio,
            "avg_traded_value_20": traded_value, "atr14": atr14, "atr_pct": atr14 / close if close else 0.0}


def score(f: dict) -> dict:
    trend = {"UP": 30, "NEUTRAL": 10, "DOWN": 0}[f["trend"]]
    momentum = max(0.0, min(25.0, f["momentum_20"] / 0.15 * 25))
    breakout = 15 if f["breakout_20"] else (7 if f["close"] >= 0.97 * f["high_20_prior"] else 0)
    volume = 15 if f["volume_ratio_20"] >= 1.5 else 8 if f["volume_ratio_20"] >= 1.0 else 0
    value = f["avg_traded_value_20"]
    liquidity = 15 if value >= 20_000_000 else 10 if value >= 5_000_000 else 5 if value >= 1_000_000 else 0
    parts = {"trend": trend, "momentum": round(momentum, 2), "breakout": breakout,
             "volume": volume, "liquidity": liquidity}
    return {"total": round(sum(parts.values()), 2), "components": parts}


def plan(f: dict) -> dict:
    close = Decimal(str(f["close"]))
    stop_distance = max(ATR_MULTIPLE * Decimal(str(f["atr14"])), STOP_FLOOR * close)
    stop = close - stop_distance
    risk = close - stop
    t1, t2 = close + T1_R * risk, close + T2_R * risk
    return {"entry_zone": [_price(close * (1 - ENTRY_BAND)), _price(close * (1 + ENTRY_BAND))],
            "entry_reference": _price(close), "stop": _price(stop), "target_1": _price(t1),
            "target_2": _price(t2), "risk_per_share": _price(risk),
            "risk_reward_t1": str(T1_R), "risk_reward_t2": str(T2_R),
            "risk_pct": str((risk / close * 100).quantize(Decimal("0.01"))),
            "entry_timing": "NEXT_ELIGIBLE_SESSION_ONLY"}


def classify(*, ticker: str, company: str | None, isin: str | None, source: str, licensing: str,
             admitted: bool, freshness: str, session: str, bars: list[Bar],
             quarantined_rows: int = 0, warnings: tuple[str, ...] = ()) -> dict:
    record = {"ticker": ticker, "company": company, "isin": isin, "source": source,
              "licensing": licensing, "freshness": freshness, "last_market_session": session,
              "rank_version": VERSION, "classification": "NO_TRADE", "score": None,
              "rejection_reasons": [], "data_warnings": list(warnings)}
    if quarantined_rows:
        record["data_warnings"].append(f"QUARANTINED_ROWS:{quarantined_rows}")
    reasons = record["rejection_reasons"]
    if not admitted:
        reasons.append("SOURCE_NOT_ADMITTED")
    if freshness != "CURRENT":
        reasons.append("DATA_NOT_CURRENT")
    if not bars or bars[-1].date != session:
        reasons.append("LAST_BAR_NOT_SESSION")
    if len(bars) < MIN_BARS:
        reasons.append("INSUFFICIENT_HISTORY")
        return record
    f = features(bars)
    s = score(f)
    record.update(score=s["total"], score_components=s["components"],
                  trend=f["trend"], momentum_20_pct=round(f["momentum_20"] * 100, 2),
                  breakout_20=f["breakout_20"], volume_confirmation=f["volume_ratio_20"] >= 1.5,
                  volume_ratio_20=round(f["volume_ratio_20"], 2),
                  liquidity_avg_traded_value_20=round(f["avg_traded_value_20"]),
                  atr_pct=round(f["atr_pct"] * 100, 2), **plan(f))
    if Decimal(str(f["avg_traded_value_20"])) < MIN_TRADED_VALUE:
        reasons.append("ILLIQUID")
    if bars[-1].volume <= 0:
        reasons.append("ZERO_VOLUME_SESSION")
    if reasons:
        return record
    total = s["total"]
    if total >= 75 and f["trend"] == "UP" and f["breakout_20"] and f["volume_ratio_20"] >= 1.5:
        record["classification"] = "STRONG_CANDIDATE"
    elif total >= 60 and f["trend"] == "UP":
        record["classification"] = "CANDIDATE"
    elif total >= 45 or f["trend"] == "UP":
        record["classification"] = "WATCHLIST"
    else:
        reasons.append("DOWNTREND" if f["trend"] == "DOWN" else "LOW_SCORE")
    return record
