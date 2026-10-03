"""Deterministic, explainable context regimes and cross-market co-movement.

Every label states its rule and inputs. Labels are context for confidence,
ordering and risk notes. They never create, upgrade or veto a candidate
(``CONTEXT-v1``). Missing inputs give UNKNOWN, never a default.
"""
from __future__ import annotations

from app.context.series import correlation, returns

VERSION = "CONTEXT-v1"


def _sma(values, window):
    return sum(values[-window:]) / window if len(values) >= window else None


def _pct(new, old):
    return float(new / old - 1) * 100 if old else None


def trend_regime(closes, *, name):
    """RISK_ON: close > SMA200 and SMA50 > SMA200; RISK_OFF: close < SMA200 and SMA50 < SMA200."""
    if len(closes) < 200:
        return {"label": "UNKNOWN", "reason": f"{name}: fewer than 200 sessions"}
    values = [float(v) for _, v in closes]
    close, sma50, sma200 = values[-1], _sma(values, 50), _sma(values, 200)
    label = ("RISK_ON" if close > sma200 and sma50 > sma200 else
             "RISK_OFF" if close < sma200 and sma50 < sma200 else "NEUTRAL")
    return {"label": label, "date": closes[-1][0].isoformat(), "close": round(close, 2),
            "sma50": round(sma50, 2), "sma200": round(sma200, 2),
            "rule": "RISK_ON if close>SMA200 and SMA50>SMA200; RISK_OFF if both below; else NEUTRAL"}


def change_regime(closes, *, lookback=20, up, down, labels=("RISING", "FALLING", "FLAT")):
    if len(closes) <= lookback:
        return {"label": "UNKNOWN", "reason": f"fewer than {lookback + 1} observations"}
    change = _pct(closes[-1][1], closes[-1 - lookback][1])
    label = labels[0] if change >= up else labels[1] if change <= -down else labels[2]
    return {"label": label, "change_pct": round(change, 2), "lookback": lookback, "date": closes[-1][0].isoformat(),
            "rule": f"{labels[0]} if Δ{lookback} ≥ +{up}%; {labels[1]} if ≤ −{down}%; else {labels[2]}"}


def fed_stance(target_upper):
    """From the target-range upper bound history: the most recent change within 365 days."""
    if len(target_upper) < 2:
        return {"label": "UNKNOWN", "reason": "no target-range history"}
    latest_day = target_upper[-1][0]
    for (prev_day, prev), (day, value) in reversed(list(zip(target_upper, target_upper[1:]))):
        if value != prev:
            if (latest_day - day).days > 365:
                break
            return {"label": "EASING" if value < prev else "TIGHTENING", "last_change": day.isoformat(),
                    "from": str(prev), "to": str(value), "rule": "direction of the last target change ≤ 365 days"}
    return {"label": "HOLD", "since_at_least": target_upper[0][0].isoformat(), "level": str(target_upper[-1][1]),
            "rule": "no target change within the observed window (≤ 365 days)"}


def usd_terms(index_closes, fx_closes):
    """Index in USD on common dates only (index / USDEGP); no forward-fill."""
    fx = dict(fx_closes)
    return [(day, value / fx[day]) for day, value in index_closes if day in fx and fx[day] > 0]


def build_regimes(markets, macro_obs, portwatch):
    spx, vix = markets.get("SPX") or [], macro_obs.get("VIXCLS") or []
    egx30, usdegp = markets.get("EGX30") or [], markets.get("USDEGP") or []
    regimes = {"version": VERSION}
    us = trend_regime(spx, name="S&P 500")
    if vix and us["label"] != "UNKNOWN":
        us["vix"] = float(vix[-1][1])
        us["vix_date"] = vix[-1][0].isoformat()
        if us["vix"] >= 25 and us["label"] == "RISK_ON":
            us["label"] = "NEUTRAL"
            us["vix_override"] = "VIX ≥ 25 downgrades RISK_ON to NEUTRAL"
    regimes["us_equity"] = us
    egx = trend_regime(egx30, name="EGX30")
    egx_usd = usd_terms(egx30, usdegp)
    if len(egx_usd) > 60:
        egx["egx30_usd_change_60_pct"] = round(_pct(egx_usd[-1][1], egx_usd[-61][1]), 2)
        egx["egx30_usd_common_dates"] = len(egx_usd)
    regimes["egx_equity"] = egx
    regimes["egp"] = change_regime(usdegp, up=2, down=2, labels=("EGP_WEAKENING", "EGP_STRENGTHENING", "STABLE"))
    regimes["gold"] = change_regime(markets.get("XAUUSD") or [], up=3, down=3)
    regimes["brent"] = change_regime(markets.get("UKOIL") or [], up=5, down=5)
    regimes["fed"] = fed_stance(macro_obs.get("DFEDTARU") or [])
    two, ten = dict(macro_obs.get("DGS2") or []), macro_obs.get("DGS10") or []
    common = [(d, v - two[d]) for d, v in ten if d in two]
    regimes["curve"] = ({"label": "INVERTED" if common[-1][1] < 0 else "POSITIVE",
                         "spread_bp": float(common[-1][1] * 100), "date": common[-1][0].isoformat()}
                        if common else {"label": "UNKNOWN", "reason": "no same-dated 2y/10y"})
    suez = ((portwatch or {}).get("chokepoints") or {}).get("Suez Canal") or {}
    change = suez.get("change_7d_vs_90d_pct")
    regimes["suez"] = ({"label": "UNKNOWN", "reason": "no PortWatch evidence"} if change is None else
                       {"label": "DISRUPTED" if change <= -25 else "ELEVATED" if change >= 25 else "NORMAL",
                        "change_7d_vs_90d_pct": change, "date": suez.get("latest_date"),
                        "rule": "DISRUPTED if 7-day mean transits ≤ −25% vs 90-day mean; ELEVATED if ≥ +25%"})
    regimes["chokepoints"] = {}
    for name, item in ((portwatch or {}).get("chokepoints") or {}).items():
        change = item.get("change_7d_vs_90d_pct")
        regimes["chokepoints"][name] = ({"label": "UNKNOWN"} if change is None else
                                        {"label": "DISRUPTED" if change <= -25 else "ELEVATED" if change >= 25
                                         else "NORMAL", "change_7d_vs_90d_pct": change,
                                         "change_7d_vs_year_ago_pct": item.get("change_7d_vs_year_ago_pct"),
                                         "date": item.get("latest_date")})
    hormuz = regimes["chokepoints"].get("Strait of Hormuz", {}).get("label") == "DISRUPTED"
    regimes["risk"] = {
        "EGX": _risk([("EGX30 regime RISK_OFF", egx["label"] == "RISK_OFF"),
                      ("EGP weakening (USD/EGP Δ20 ≥ +2%)", regimes["egp"]["label"] == "EGP_WEAKENING"),
                      ("Suez transits disrupted", regimes["suez"]["label"] == "DISRUPTED"),
                      ("Energy chokepoint disrupted (Strait of Hormuz)", hormuz)],
                     unknown=egx["label"] == "UNKNOWN"),
        "US": _risk([("S&P 500 regime RISK_OFF", us["label"] == "RISK_OFF"),
                     ("VIX ≥ 25", (us.get("vix") or 0) >= 25),
                     ("Treasury curve inverted", regimes["curve"]["label"] == "INVERTED"),
                     ("Fed tightening", regimes["fed"]["label"] == "TIGHTENING"),
                     ("Energy chokepoint disrupted (Strait of Hormuz)", hormuz)],
                    unknown=us["label"] == "UNKNOWN"),
    }
    return regimes


def _risk(flags, *, unknown):
    active = [name for name, on in flags if on]
    if unknown:
        return {"label": "UNKNOWN", "flags": active, "reason": "equity regime unknown"}
    return {"label": "HIGH" if len(active) >= 2 else "ELEVATED" if active else "LOW", "flags": active,
            "rule": "count of adverse context flags: 0 LOW, 1 ELEVATED, ≥2 HIGH"}


PAIRS = (("EGX30", "SPX"), ("EGX30", "USDEGP"), ("EGX30", "XAUUSD"), ("EGX30", "UKOIL"), ("SPX", "VIXCLS"),
         ("SPX", "XAUUSD"), ("SPX", "UKOIL"), ("XAUUSD", "DTWEXBGS"), ("SPX", "DGS10"), ("XAUUSD", "DGS10"))


def cross_market(series, *, window=60, min_overlap=40):
    """Return correlations on common dates (descriptive; not causal or predictive).

    Yields (DGS10) use first differences in percentage points instead of returns.
    """
    changes = {}
    for key, observations in series.items():
        if key in ("DGS10", "DGS2"):
            changes[key] = {d: float(v - p) for (_, p), (d, v) in zip(observations, observations[1:])}
        else:
            changes[key] = returns(observations)
    out = []
    for a, b in PAIRS:
        if a in changes and b in changes:
            out.append({"pair": f"{a} ~ {b}", "window": window,
                        **correlation(changes[a], changes[b], window=window, min_overlap=min_overlap)})
        else:
            out.append({"pair": f"{a} ~ {b}", "value": None, "n": 0, "reason": "series unavailable"})
    return out

