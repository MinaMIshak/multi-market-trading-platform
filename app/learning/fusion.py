"""EGX-DECISION-FUSION-v1: Opportunity Score research (0–100), separate from Technical Score and trade gates.

Sequence: data quality → technical quality → quant edge → sector/flow →
catalyst → regime → forecast → Opportunity Score → confidence and evidence
quality → hard tradeability gates (outside the score) → final Paper/Shadow
action. A high Opportunity Score never overrides a failed gate.

Components, each 0–100 and point in time (bars ≤ t; learned parts from
training labels matured before the cutoff only):
- technical: the EGX-RANK-v1 score, rebuilt exactly from features (DF0 is the
  technical baseline);
- quant: factor-group edge. Correlated factors are grouped (momentum: r20,
  rs20, breakout distance; participation: RVOL, turnover acceleration;
  position: distance from the 52-week high; risk: 20-session volatility
  inverted). Each group's within-session percentile is binned into quintiles,
  and the bin's mean forward 3-session return is learned from training
  rows. A group whose training rank IC is ≤ 0 gets no weight (incremental
  value), and the component is the within-session percentile of the summed
  group edges;
- sector: within-session percentile of the mean of sector 5-session return,
  sector breadth and sector turnover acceleration (one capped group; a
  market-flow proxy, not institutional flow);
- forecast: within-session percentile of the forecast champion's expected
  3-session return;
- catalyst: live only. A sourced event for the ISIN within 5 sessions counts
  only with market confirmation (RVOL ≥ 1.5 or sector turnover acceleration
  > 1). Narrative alone is capped at 50. In backtests it is UNKNOWN (no event
  history), never 0;
- regime: time-series percentile of market breadth (the share of symbols with
  an UP EMA stack) against prior sessions only. It is constant across a
  session, so it never changes a session's ranking.

The Opportunity Score is the weighted mean of the available components; the
weights are renormalised over what is available. Missing components are
listed in the evidence quality, never scored as zero.

Arms (DF-CFG-1, versioned; an older arm is never edited):
- DF0: technical only;
- DF1: + quant;
- DF2: + sector;
- DF3: + forecast;
- DF4: + catalyst + regime (baseline weights 25/25/20/15/8/7);
- DF5: non-negative ridge weights over the six components, learned per fold
  from training data, each capped at ``DF5_CAP`` (the redundancy control:
  correlated components share weight instead of stacking).
"""
from __future__ import annotations

from math import isnan

STRATEGY = "EGX-DECISION-FUSION-v1"
CONFIG = "DF-CFG-1"
EFFECTIVE_DATE = "2026-10-06"
COMPONENTS = ("technical", "quant", "sector", "forecast", "catalyst", "regime")
BASE_WEIGHTS = {"technical": 0.25, "quant": 0.25, "sector": 0.20, "forecast": 0.15, "catalyst": 0.08, "regime": 0.07}
ARMS = {
    "DF0": ("technical",),
    "DF1": ("technical", "quant"),
    "DF2": ("technical", "quant", "sector"),
    "DF3": ("technical", "quant", "sector", "forecast"),
    "DF4": COMPONENTS,
    "DF5": COMPONENTS,  # learned constrained weights
}
DF5_CAP = 0.35
QUANT_GROUPS = {"momentum": (("r20", 1), ("rs20", 1), ("breakout_dist", 1)),
                "participation": (("rvol", 1), ("turnover_accel", 1)),
                "position": (("high52_dist", 1),), "risk": (("vol20", -1),)}

# US-DECISION-FUSION-v1: a separate family with its own components, weights and factor groups.
# Never trained on EGX rows (each market's dataset is built from that market's series only).
US_STRATEGY = "US-DECISION-FUSION-v1"
US_CONFIG = "US-DF-CFG-1"
US_COMPONENTS = ("technical", "quant", "sector", "fundamentals", "forecast", "catalyst", "regime")
US_BASE_WEIGHTS = {"technical": 0.20, "quant": 0.20, "fundamentals": 0.20, "sector": 0.15, "forecast": 0.15,
                   "catalyst": 0.05, "regime": 0.05}
US_ARMS = {
    "US-DF0": ("technical",),
    "US-DF1": ("technical", "quant"),
    "US-DF2": ("technical", "quant", "sector"),
    "US-DF3": ("technical", "quant", "sector", "fundamentals"),
    "US-DF4": ("technical", "quant", "sector", "fundamentals", "forecast"),
    "US-DF5": US_COMPONENTS,
    "US-DF6": US_COMPONENTS,  # learned constrained weights (redundancy control)
}
US_QUANT_GROUPS = {"momentum": (("r20", 1), ("r60", 1), ("rs20", 1)),
                   "reversal": (("r1", -1), ("r5", -1)),
                   "volume_surprise": (("rvol", 1), ("turnover_accel", 1)),
                   "position": (("high52_dist", 1), ("breakout_dist", 1)),
                   "risk": (("vol20", -1), ("atr_pct", -1)),
                   "gap": (("gap1", 1),)}
MARKETS = {
    "EGX": {"strategy": STRATEGY, "config": CONFIG, "components": COMPONENTS, "weights": BASE_WEIGHTS, "arms": ARMS,
            "learned_arm": "DF5", "full_fixed_arm": "DF4", "quant_groups": QUANT_GROUPS},
    "US": {"strategy": US_STRATEGY, "config": US_CONFIG, "components": US_COMPONENTS, "weights": US_BASE_WEIGHTS,
           "arms": US_ARMS, "learned_arm": "US-DF6", "full_fixed_arm": "US-DF5", "quant_groups": US_QUANT_GROUPS},
}
LIQUIDITY_TIERS = {"EGX": (20_000_000, 5_000_000, 1_000_000), "US": (500_000_000, 100_000_000, 20_000_000)}
RATIONALE = {
    "weights": "Research starting point from the mission brief, not fitted and not declared optimal; DF5 learns "
               "constrained weights out of sample for comparison.",
    "DF5_CAP": "No single component may carry more than 35% of the learned weight.",
    "catalyst": "Narrative without market confirmation is capped at 50 (neutral).",
}


def technical_score(get, market="EGX"):
    """EGX-RANK-v1 score rebuilt from FEAT-v1 features (same thresholds as app/strategies/egx_ranking.score)."""
    stack, r20, breakout = get("ema_stack"), get("r20"), get("breakout_dist")
    rvol, turnover = get("rvol"), get("turnover20")
    if any(v is None or isnan(v) for v in (stack, r20, breakout, turnover)):
        return None
    trend = 30 if stack == 1 else 0 if stack == -1 else 10
    momentum = max(0.0, min(25.0, r20 / 0.15 * 25))
    breakout_points = 15 if breakout >= 0 else 7 if breakout >= -0.03 else 0
    volume = 0 if rvol is None or isnan(rvol) else 15 if rvol >= 1.5 else 8 if rvol >= 1.0 else 0
    top, middle, floor = LIQUIDITY_TIERS[market]
    liquidity = 15 if turnover >= top else 10 if turnover >= middle else 5 if turnover >= floor else 0
    return round(trend + momentum + breakout_points + volume + liquidity, 2)


def percentiles(values):
    """Within-group percentile (0–100) with ties averaged; None stays None."""
    present = sorted((v, k) for k, v in enumerate(values) if v is not None and not isnan(v))
    out = [None] * len(values)
    if not present:
        return out
    n = len(present)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = 100.0 * ((i + j) / 2) / (n - 1) if n > 1 else 50.0
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def _group_percentiles(ds, members, group, groups=None):
    columns = []
    for name, sign in (groups or QUANT_GROUPS)[group]:
        column = [ds.f[name][i] for i in members]
        columns.append(percentiles([None if isnan(v) else sign * v for v in column]))
    out = []
    for k in range(len(members)):
        values = [c[k] for c in columns if c[k] is not None]
        out.append(sum(values) / len(values) if values else None)
    return out


def _bin(pct):
    return min(4, int(pct // 20))


class QuantEdge:
    """Training-only factor-group edges (mean forward 3-session return by within-session quintile)."""

    def __init__(self, groups=None):
        self.groups = groups or QUANT_GROUPS

    def fit(self, ds, by_session, sessions_idx, cutoff):
        QUANT = self.groups
        sums = {g: [[0.0, 0] for _ in range(5)] for g in QUANT}
        ic = {g: [] for g in QUANT}
        for k in sessions_idx:
            members = [i for i in by_session.get(k, []) if ds.valid(3, i, cutoff)]
            if len(members) < 20:
                continue
            rets = [ds.ret[3][i] for i in members]
            for g in QUANT:
                pcts = _group_percentiles(ds, members, g, QUANT)
                pairs = [(p, r) for p, r in zip(pcts, rets) if p is not None]
                for p, r in pairs:
                    cell = sums[g][_bin(p)]
                    cell[0] += r
                    cell[1] += 1
                if len(pairs) >= 20:
                    from app.learning.walkforward import spearman
                    rho = spearman([p for p, _ in pairs], [r for _, r in pairs])
                    if rho is not None:
                        ic[g].append(rho)
        self.edges, self.ic = {}, {}
        for g in QUANT:
            means = [s / n if n else None for s, n in sums[g]]
            self.ic[g] = round(sum(ic[g]) / len(ic[g]), 4) if ic[g] else None
            usable = self.ic[g] is not None and self.ic[g] > 0 and all(m is not None for m in means)
            self.edges[g] = means if usable else None
        return self

    def scores(self, ds, members):
        total = [0.0] * len(members)
        used = False
        for g, means in self.edges.items():
            if means is None:
                continue
            used = True
            for k, p in enumerate(_group_percentiles(ds, members, g, self.groups)):
                if p is not None:
                    total[k] += means[_bin(p)]
        return percentiles(total) if used else [None] * len(members)


def sector_scores(ds, members):
    columns = [percentiles([ds.f[name][i] for i in members])
               for name in ("sector_r5", "sector_breadth5", "sector_turnover_accel")]
    combined = []
    for k in range(len(members)):
        values = [c[k] for c in columns if c[k] is not None]
        combined.append(sum(values) / len(values) if len(values) == 3 else None)
    return percentiles(combined)


def regime_series(ds, by_session):
    """Point-in-time breadth percentile per session (against prior sessions only)."""
    breadth = {}
    for k, members in by_session.items():
        stacks = [ds.f["ema_stack"][i] for i in members if not isnan(ds.f["ema_stack"][i])]
        breadth[k] = sum(1 for s in stacks if s == 1) / len(stacks) if stacks else None
    out, history = {}, []
    for k in sorted(breadth):
        value = breadth[k]
        if value is None:
            out[k] = None
            continue
        out[k] = round(100.0 * sum(1 for h in history if h <= value) / len(history), 2) if len(history) >= 20 else None
        history.append(value)
    return out, breadth


def opportunity(scores, weights):
    """Weighted mean over available components; returns (score, available weight share)."""
    total = sum(weights.values())
    available = {c: w for c, w in weights.items() if scores.get(c) is not None}
    if not available:
        return None, 0.0
    value = sum(scores[c] * w for c, w in available.items()) / sum(available.values())
    return round(value, 2), round(sum(available.values()) / total, 3)


def arm_weights(arm, learned=None, market="EGX"):
    config = MARKETS[market]
    if arm == config["learned_arm"]:
        return learned or {c: config["weights"][c] for c in config["components"]}
    return {c: config["weights"][c] for c in config["arms"][arm]}


def learn_weights(rows, cap=DF5_CAP, lam=5.0, components=COMPONENTS):
    """Non-negative, capped ridge weights of component scores on forward 3-session return (training rows only)."""
    from app.learning.models import _solve
    data = [(r["scores"], r["ret3"]) for r in rows if r["ret3"] is not None]
    usable = [c for c in components if sum(1 for s, _ in data if s.get(c) is not None) >= 0.5 * max(1, len(data))]
    if len(data) < 500 or not usable:
        return None
    means = {c: sum(s[c] for s, _ in data if s.get(c) is not None) / max(1, sum(1 for s, _ in data if s.get(c) is not None))
             for c in usable}
    p = len(usable)
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for s, y in data:
        x = [((s.get(c) if s.get(c) is not None else means[c]) - 50.0) / 50.0 for c in usable]
        for a in range(p):
            xty[a] += x[a] * y
            for b in range(p):
                xtx[a][b] += x[a] * x[b]
    for a in range(p):
        xtx[a][a] += lam
    coef = _solve(xtx, xty)
    positive = {c: max(0.0, w) for c, w in zip(usable, coef)}
    total = sum(positive.values())
    if total <= 0:
        return None
    return cap_weights({c: w for c, w in positive.items() if w > 0}, cap)


def cap_weights(weights, cap):
    """Water-filling: weights sum to 1 and none exceeds ``cap``. If the cap is infeasible (fewer than 1/cap
    usable components), equal weights are returned."""
    names = [c for c in weights if weights[c] > 0]
    if not names:
        return None
    if len(names) * cap < 1 - 1e-9:
        return {c: round(1 / len(names), 4) for c in names}  # cap infeasible: equal weights (documented)
    fixed, free = {}, {c: weights[c] for c in names}
    while True:
        mass = 1 - cap * len(fixed)
        total = sum(free.values())
        scaled = {c: (mass * w / total if total > 0 else mass / len(free)) for c, w in free.items()}
        over = [c for c, w in scaled.items() if w > cap + 1e-12]
        if not over:
            out = {**{c: cap for c in fixed}, **scaled}
            return {c: round(w, 4) for c, w in out.items()}
        for c in over:
            fixed[c] = cap
            free.pop(c)
        if not free:
            return {c: round(cap, 4) for c in fixed}


def catalyst_score(events, *, rvol, sector_accel):
    """Live-only catalyst component; None when no sourced event exists."""
    if not events:
        return None
    confirmed = (rvol or 0) >= 1.5 or (sector_accel or 0) > 1.0
    positive = any(e.get("direction") == "POSITIVE" for e in events)
    negative = any(e.get("direction") == "NEGATIVE" for e in events)
    if not confirmed:
        return 50.0  # narrative alone: neutral, never a strong score
    if negative and not positive:
        return 25.0
    return 85.0 if positive else 70.0


def confidence(*, available_share, arm_metrics, forecast_support, disagreement, stale):
    if stale:
        return "LOW"
    if not arm_metrics or arm_metrics.get("status") != "OK":
        return "INSUFFICIENT_SAMPLE"
    ic = arm_metrics.get("spearman_mean") or 0
    if ic > 0.05 and available_share >= 0.85 and (forecast_support or 0) >= 1000 and (disagreement or 0) < 25:
        return "HIGH"
    if ic > 0.02 and available_share >= 0.7:
        return "MEDIUM"
    return "LOW"


def evidence_quality(scores, *, fresh, sector_known, forecast_status, calibration_status):
    return {"technical_data": "VERIFIED" if scores.get("technical") is not None else "UNKNOWN",
            "price_freshness": "VERIFIED" if fresh else "STALE",
            "sector_data": "VERIFIED" if sector_known and scores.get("sector") is not None else "UNKNOWN",
            "quant_edge": "AVAILABLE" if scores.get("quant") is not None else "INSUFFICIENT_SAMPLE",
            "catalyst": "AVAILABLE" if scores.get("catalyst") is not None else "UNKNOWN",
            "regime": "AVAILABLE" if scores.get("regime") is not None else "INSUFFICIENT_SAMPLE",
            "forecast": forecast_status, "forecast_calibration": calibration_status,
            "fundamentals": "PARTIAL (EGX disclosures only; US BLOCKED)"}


def explain(scores):
    """+++ / ++ / + / 0 / − per available component; unavailable components are omitted, never invented."""
    out = {}
    for c in scores:
        v = scores.get(c)
        if v is None:
            continue
        out[c] = "+++" if v >= 85 else "++" if v >= 70 else "+" if v >= 55 else "0" if v >= 45 else "−"
    return out


def final_action(*, opportunity_score, eligibility, technical_class, gate_action):
    """Hard gates first: a failed or unknown gate is never overridden by a high Opportunity Score."""
    if technical_class not in ("STRONG_CANDIDATE", "CANDIDATE"):
        return "WATCH" if technical_class == "WATCHLIST" else "NO_TRADE"
    if eligibility != "ELIGIBLE":
        return "BLOCKED"
    return gate_action or "PAPER_ENTRY"


__all__ = ["STRATEGY", "CONFIG", "COMPONENTS", "ARMS", "BASE_WEIGHTS", "technical_score", "percentiles",
           "QuantEdge", "sector_scores", "regime_series", "opportunity", "arm_weights", "learn_weights",
           "catalyst_score", "confidence", "evidence_quality", "explain", "final_action"]


# --- US-specific layers ---------------------------------------------------------------------------------------------

EARNINGS_STATES = ("NO_NEAR_EARNINGS", "EARNINGS_IN_0_1_DAYS", "EARNINGS_IN_2_5_DAYS", "POST_EARNINGS_DAY_1",
                   "POST_EARNINGS_DAY_2_3", "UNKNOWN")


def earnings_state(*, session, next_release, last_release):
    """Earnings state from release dates (NYSE sessions); UNKNOWN without dates. ``session`` is the decision session."""
    from datetime import date as _date, datetime as _datetime, timezone as _tz
    from zoneinfo import ZoneInfo
    from app.us import nyse_calendar
    ny = ZoneInfo("America/New_York")
    day = _date.fromisoformat(session)

    def to_day(stamp):
        return _datetime.fromtimestamp(stamp, _tz.utc).astimezone(ny).date() if stamp else None

    def sessions_between(a, b):
        count, current = 0, a
        while current < b:
            current = nyse_calendar.next_session(current)
            count += 1
        return count

    nxt, last = to_day(next_release), to_day(last_release)
    if nxt is None and last is None:
        return "UNKNOWN"
    if last is not None and last <= day:
        since = sessions_between(last, day)
        if since == 1:
            return "POST_EARNINGS_DAY_1"
        if 2 <= since <= 3:
            return "POST_EARNINGS_DAY_2_3"
    if nxt is not None and nxt > day:
        ahead = sessions_between(day, nxt)
        if ahead <= 1:
            return "EARNINGS_IN_0_1_DAYS"
        if ahead <= 5:
            return "EARNINGS_IN_2_5_DAYS"
    return "NO_NEAR_EARNINGS"


FUNDAMENTAL_FIELDS = (("eps_surprise_pct", 1), ("revenue_surprise_pct", 1), ("revenue_growth_ttm", 1),
                      ("net_margin", 1), ("roe", 1))


def fundamentals_scores(records):
    """Cross-sectional percentile mean of sourced fundamental fields (None when fewer than 3 are present)."""
    columns = [percentiles([None if r.get(name) is None else sign * float(r[name]) for r in records])
               for name, sign in FUNDAMENTAL_FIELDS]
    combined = []
    for k in range(len(records)):
        values = [c[k] for c in columns if c[k] is not None]
        combined.append(sum(values) / len(values) if len(values) >= 3 else None)
    return percentiles(combined)


GAP_THRESHOLD = 0.02


def gap_class(gap, intraday):
    """Classify a completed session: gap = open / previous close − 1; intraday = close / open − 1."""
    if gap is None or intraday is None or isnan(gap) or isnan(intraday):
        return "UNKNOWN"
    if gap >= GAP_THRESHOLD:
        return "GAP_UP_CONTINUATION" if intraday > 0 else "GAP_UP_FADE"
    if gap <= -GAP_THRESHOLD:
        return "GAP_DOWN_REVERSAL" if intraday > 0 else "GAP_DOWN_CONTINUATION"
    return "NO_GAP"


US_GATES = {"min_turnover20_usd": 20_000_000.0, "max_vol20": 0.06, "extended_gap": 0.05, "max_risk_pct": 10.0,
            "max_per_sector": 3}


def us_gates(*, record, features, earnings, freshness):
    """US hard gates (PASS/FAIL/NOT_MEASURABLE/NOT_EVALUATED); they are applied outside the score, never inside it."""
    gates = {}
    gates["stale_data"] = "PASS" if freshness == "CURRENT" else "FAIL"
    turnover = features.get("turnover20")
    gates["min_liquidity"] = "UNKNOWN" if turnover is None else ("PASS" if turnover >= US_GATES["min_turnover20_usd"] else "FAIL")
    gates["earnings_event_risk"] = ("FAIL" if earnings == "EARNINGS_IN_0_1_DAYS" else
                                    "UNKNOWN" if earnings == "UNKNOWN" else "PASS")
    vol = features.get("vol20")
    gates["volatility_limit"] = "UNKNOWN" if vol is None else ("PASS" if vol <= US_GATES["max_vol20"] else "FAIL")
    gap, intraday = features.get("gap1"), features.get("intraday1")
    gates["gap_chase"] = ("UNKNOWN" if gap is None else
                          "FAIL" if gap >= US_GATES["extended_gap"] and (intraday or 0) >= 0 else "PASS")
    risk = record.get("risk_pct")
    gates["stop_feasibility"] = ("UNKNOWN" if risk is None else
                                 "PASS" if float(risk) <= US_GATES["max_risk_pct"] else "FAIL")
    gates["spread"] = "NOT_MEASURABLE"
    gates["portfolio_risk"] = "NOT_EVALUATED"
    gates["sector_concentration"] = "PENDING"
    return gates


def us_eligibility(gates):
    failed = [name for name, value in gates.items() if value == "FAIL"]
    unknown = [name for name, value in gates.items() if value == "UNKNOWN"]
    if failed:
        return "BLOCKED", failed
    if unknown:
        return "UNKNOWN", unknown
    return "ELIGIBLE", []
