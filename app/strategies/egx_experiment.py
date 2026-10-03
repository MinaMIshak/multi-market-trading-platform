"""EGX champion vs challenger experiment (EGX-EXP-v1): pure decision, execution and metrics logic.

Technical class (EGX-RANK-v1, unchanged) is separate from trade eligibility
(challenger gates) and from the final Shadow action:

    technical class    STRONG_CANDIDATE / CANDIDATE / WATCHLIST / NO_TRADE   (V1, never modified)
    trade eligibility  ELIGIBLE / BLOCKED / UNKNOWN                          (per arm, decision time)
    final action       PAPER_ENTRY / WATCH / NO_TRADE / EXPIRED / CHASE_BLOCKED / INVALIDATED

Decision-time evidence uses only bars dated on or before the candidate
session (no lookahead). Execution states use only the first bar after the
session (the entry session), in the order a trader would see it: the open
first. Fills and exits reuse the V1 simulator (same costs), so arms differ only
by their gates.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from statistics import mean, median, pstdev

from app.paper.system_candidates import COMMISSION, SLIPPAGE, simulate

D0 = Decimal(0)


def _d(value):
    return Decimal(str(value))


def _q(value, places="0.01"):
    return None if value is None else str(Decimal(value).quantize(Decimal(places)))


# --- decision-time evidence ---------------------------------------------------------------------------

def liquidity_evidence(bars):
    """Volume and turnover facts from bars up to the decision session (EGP turnover = close × volume)."""
    if len(bars) < 21:
        return {"status": "UNKNOWN", "reason": "NOT_READY: fewer than 21 sessions"}
    if any(b.volume is None or b.volume < 0 for b in bars[-21:]):
        return {"status": "UNKNOWN", "reason": "NOT_READY: missing volume in the last 21 sessions"}
    current = bars[-1]
    avg_volume_20 = sum(b.volume for b in bars[-21:-1]) / 20          # prior 20 (V1 relative-volume basis)
    avg_turnover_20 = sum(b.close * b.volume for b in bars[-20:]) / 20  # last 20 incl. current (V1 basis)
    return {"status": "AVAILABLE", "session": current.date, "volume": str(current.volume),
            "avg_volume_20": _q(avg_volume_20), "relative_volume": _q(current.volume / avg_volume_20)
            if avg_volume_20 else None, "turnover_egp": _q(current.close * current.volume),
            "avg_turnover_20_egp": _q(avg_turnover_20)}


def position_size(entry, stop, cfg):
    """Risk-based Paper sizing at the intended entry (the top of the entry zone, the worst in-zone fill)."""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"status": "INVALID", "reason": "stop not below entry"}
    capital = cfg["model_capital_egp"]
    budget = capital * cfg["risk_pct_per_trade"] / 100
    by_risk = (budget / risk_per_share).to_integral_value(rounding=ROUND_DOWN)
    by_cap = (capital * cfg["max_position_pct_of_capital"] / 100 / entry).to_integral_value(rounding=ROUND_DOWN)
    quantity = min(by_risk, by_cap)
    return {"status": "SIZED" if quantity > 0 else "SIZE_ZERO", "model_capital_egp": str(capital),
            "risk_pct_per_trade": str(cfg["risk_pct_per_trade"]), "risk_budget_egp": _q(budget),
            "intended_entry": str(entry), "stop": str(stop), "risk_per_share": _q(risk_per_share),
            "quantity": int(quantity), "position_value_egp": _q(quantity * entry),
            "planned_risk_egp": _q(quantity * risk_per_share), "capped_by": "CAPITAL_CAP" if by_cap < by_risk else "RISK"}


def liquidity_gate(liquidity, sizing, cfg):
    if liquidity["status"] != "AVAILABLE":
        return {"result": "UNKNOWN", "reason": liquidity["reason"]}
    avg = _d(liquidity["avg_turnover_20_egp"])
    if avg <= 0:
        return {"result": "FAIL", "reason": "LIQUIDITY_FAIL: no turnover in 20 sessions"}
    if sizing["status"] != "SIZED":
        return {"result": "UNKNOWN", "reason": f"NOT_READY: sizing {sizing['status']}"}
    pct = _d(sizing["position_value_egp"]) / avg * 100
    detail = {"position_pct_of_avg_turnover": _q(pct), "max_pct": str(cfg["max_position_pct_of_avg_turnover"]),
              "min_avg_turnover_egp": str(cfg["min_avg_turnover_egp"])}
    if _d(liquidity["volume"]) <= 0:
        return {"result": "FAIL", "reason": "LIQUIDITY_FAIL: no volume on the decision session", **detail}
    if avg < cfg["min_avg_turnover_egp"]:
        return {"result": "FAIL", "reason": "LIQUIDITY_FAIL: average turnover below floor", **detail}
    if pct > cfg["max_position_pct_of_avg_turnover"]:
        return {"result": "FAIL", "reason": f"LIQUIDITY_FAIL: position {pct:.2f}% of average turnover", **detail}
    return {"result": "PASS", "reason": "PASS", **detail}


def pivot_highs(bars, *, half_width):
    """Confirmed pivot highs: high ≥ every high within ``half_width`` sessions on both sides (all on or
    before the last bar, so a pivot is only known once its right side has printed)."""
    out = []
    for i in range(half_width, len(bars) - half_width):
        window = bars[i - half_width:i + half_width + 1]
        if bars[i].high >= max(b.high for b in window) and bars[i].high > max(
                b.high for b in window[:half_width]):
            out.append((bars[i].date, bars[i].high))
    return out


def resistance_gate(bars, entry, stop, cfg):
    """Nearest confirmed pivot high above the intended entry within the lookback (prior sessions only)."""
    prior = bars[:-1][-cfg["resistance_lookback_sessions"]:]
    risk = entry - stop
    if risk <= 0:
        return {"result": "UNKNOWN", "reason": "NOT_READY: stop not below entry"}
    if len(prior) < 2 * cfg["pivot_half_width"] + 1:
        return {"result": "UNKNOWN", "reason": "NOT_READY: too few prior sessions for pivots"}
    above = [(day, high) for day, high in pivot_highs(prior, half_width=cfg["pivot_half_width"]) if high > entry]
    base = {"intended_entry": str(entry), "stop": str(stop), "risk_per_share": _q(risk),
            "required_r": str(cfg["min_room_to_resistance_r"]), "lookback_sessions": len(prior)}
    if not above:
        return {"result": "PASS", "reason": "PASS: no confirmed pivot high above entry in lookback",
                "nearest_resistance": None, "room_r": None, **base}
    day, level = min(above, key=lambda item: item[1])
    room = (level - entry) / risk
    result = "PASS" if room >= cfg["min_room_to_resistance_r"] else "FAIL"
    return {"result": result, "reason": "PASS" if result == "PASS" else
            f"RESISTANCE_RR_FAIL: {room:.2f}R to {level} < {cfg['min_room_to_resistance_r']}R",
            "nearest_resistance": str(level), "resistance_date": day, "reward_to_resistance": _q(level - entry),
            "room_r": _q(room), **base}


def decide(record, bars, cfg, *, config_version, strategy):
    """Decision-time evidence and gates for one V1 candidate (bars dated ≤ session only)."""
    session = record["session"]
    upto = [b for b in bars if b.date <= session]
    low, high = (_d(v) for v in record["entry_zone"])
    stop = _d(record["stop"])
    stale = None
    if record.get("freshness", "CURRENT") != "CURRENT":
        stale = "STALE_DATA: V1 freshness not CURRENT"
    elif not upto or upto[-1].date != session:
        stale = "STALE_DATA: newest decision bar is not the candidate session"
    liquidity = liquidity_evidence(upto)
    sizing = position_size(high, stop, cfg)
    return {"candidate_id": record["candidate_id"], "ticker": record["ticker"], "session": session,
            "strategy": strategy, "config_version": config_version, "technical_class": record["classification"],
            "technical_score": record.get("score"), "artifact_id": record.get("artifact_id"),
            "entry_zone": [str(low), str(high)], "stop": str(stop), "target_1": record.get("target_1"),
            "target_2": record.get("target_2"), "decision_bars": len(upto), "stale": stale,
            "liquidity": liquidity, "sizing": sizing, "liquidity_gate": liquidity_gate(liquidity, sizing, cfg),
            "resistance_gate": resistance_gate(upto, high, stop, cfg)}


GATES_BY_ARM = {"V1": (), "V2A": ("liquidity_gate",), "V2B": ("liquidity_gate", "resistance_gate"),
                "V2C": ("liquidity_gate", "resistance_gate"), "V2D": ("liquidity_gate", "resistance_gate")}


def eligibility(decision, arm):
    """(ELIGIBLE / BLOCKED / UNKNOWN, attribution code) at decision time for one arm."""
    if decision["stale"] and arm != "V1":
        return "BLOCKED", "STALE_DATA"
    for gate in GATES_BY_ARM[arm]:
        result = decision[gate]["result"]
        if result == "FAIL":
            return "BLOCKED", decision[gate]["reason"].split(":")[0]
        if result == "UNKNOWN":
            return "UNKNOWN", "NOT_READY"
    return "ELIGIBLE", "PASS"


# --- execution (entry session only) --------------------------------------------------------------------

def entry_state(decision, later, cfg):
    """V2C/V2D entry validity on the first bar after the session; never uses later bars."""
    low, high = (_d(v) for v in decision["entry_zone"])
    stop = _d(decision["stop"])
    if not later:
        return {"state": "WAITING_FOR_ENTRY", "expiry_session": "next EGX session after " + decision["session"]}
    bar = later[0]
    gap_pct = (bar.open / high - 1) * 100
    base = {"entry_session": bar.date, "expiry_session": bar.date, "open": str(bar.open),
            "entry_zone": [str(low), str(high)], "gap_vs_zone_top_pct": _q(gap_pct),
            "max_chase_pct": str(cfg["max_chase_pct_above_zone"])}
    if bar.open <= stop:
        return {"state": "INVALIDATED", **base}
    if bar.open < low:
        return {"state": "BELOW_ENTRY", **base}
    if bar.open <= high:
        return {"state": "ENTRY_VALID", **base}
    if gap_pct > cfg["max_chase_pct_above_zone"]:
        return {"state": "CHASE_BLOCKED", **base}
    if bar.low <= high:
        return {"state": "ENTRY_VALID", "via": "ABOVE_ENTRY_ZONE_THEN_PULLBACK", **base}
    return {"state": "SETUP_EXPIRED", "via": "ABOVE_ENTRY_ZONE", **base}


def lifecycle(candidate, later, *, slippage=SLIPPAGE, commission=COMMISSION):
    return simulate(candidate, later, slippage=slippage, commission=commission)


def net_r(item, stop):
    """Net R after costs: net return × entry / (entry − stop)."""
    if not item.get("net_return_pct") or not item.get("entry"):
        return None
    entry = _d(item["entry"])
    risk = entry - _d(stop)
    return float(_d(item["net_return_pct"]) / 100 * entry / risk) if risk > 0 else None


# --- metrics -------------------------------------------------------------------------------------------------

def checkpoint(closed):
    return ("INSUFFICIENT_SAMPLE" if closed < 20 else "EARLY_CHECKPOINT_20" if closed < 50 else
            "CHECKPOINT_50" if closed < 100 else "CHECKPOINT_100")


def performance(trades, *, eligible, blocked):
    """trades: dicts with status, events, net_r, net_r_stressed, mae_r, mfe_r, sessions_held, session."""
    closed = [t for t in trades if t["status"].startswith("CLOSED") and t.get("net_r") is not None]
    closed.sort(key=lambda t: (t["exit_date"], t["ticker"]))
    rs = [t["net_r"] for t in closed]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    events = lambda name: sum(1 for t in closed if any(e["event"] == name for e in t["events"]))
    n = len(closed)
    summary = {"eligible_setups": eligible, "blocked": blocked,
               "simulated_trades": sum(1 for t in trades if t["status"] not in (
                   "PENDING_ENTRY", "NO_FILL_GAP_DOWN", "NO_FILL_GAP_UP", "CHASE_BLOCKED", "INVALIDATED",
                   "BELOW_ENTRY", "SETUP_EXPIRED", "BLOCKED", "WAITING_FOR_ENTRY")),
               "closed": n, "open": sum(1 for t in trades if t["status"] == "OPEN"),
               "checkpoint": checkpoint(n), "promotion": "NOT_ELIGIBLE: champion stays until ≥ 50 closed trades "
                                                        "and robust expectancy, profit factor and drawdown"}
    if not n:
        return {**summary, "status": "INSUFFICIENT_SAMPLE"}
    sd = pstdev(rs) if n > 1 else 0.0
    stressed = [t["net_r_stressed"] for t in closed if t.get("net_r_stressed") is not None]
    summary.update(
        status=checkpoint(n), win_rate=round(len(wins) / n, 3), expectancy_r=round(mean(rs), 3),
        average_r=round(mean(rs), 3), median_r=round(median(rs), 3),
        average_winner_r=round(mean(wins), 3) if wins else None,
        average_loser_r=round(mean(losses), 3) if losses else None,
        profit_factor=round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else None,
        cumulative_r=round(sum(rs), 3), max_drawdown_r=round(drawdown, 3),
        t1_hit_rate=round(events("PARTIAL_T1") / n, 3), t2_hit_rate=round(events("EXIT_T2") / n, 3),
        stop_hit_rate=round(events("EXIT_STOP") / n, 3),
        average_mae_r=round(mean(float(t["mae_r"]) for t in closed), 3),
        average_mfe_r=round(mean(float(t["mfe_r"]) for t in closed), 3),
        average_holding_sessions=round(mean(t["sessions_held"] for t in closed), 2),
        expectancy_r_double_costs=round(mean(stressed), 3) if stressed else None,
        expectancy_ci95_approx=([round(mean(rs) - 1.96 * sd / n ** 0.5, 3), round(mean(rs) + 1.96 * sd / n ** 0.5, 3)]
                                if n >= 20 else "NOT_COMPUTED_BELOW_20"))
    return summary
