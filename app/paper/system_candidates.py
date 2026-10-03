"""System-generated Paper/Shadow candidate lifecycle (operator decision 2026-10-01).

Candidates come from EGX-RANK-v1 (STRONG_CANDIDATE / CANDIDATE) on admitted,
session-current data. Human review is optional (``human_review`` field), not a
prerequisite. These are simulation records only: no orders, no broker, no
live money.

The lifecycle is recomputed on every run from immutable candidate records and
only the bars dated **after** the candidate session, so it cannot look ahead
and re-running it is idempotent. Rules:
- entry: the next session only. Open inside the entry zone fills at the
  open. Open above the zone fills at the zone top only if the bar trades
  down to it, otherwise NO_FILL_GAP_UP. Open below the zone invalidates the
  setup (NO_FILL_GAP_DOWN). No bar yet means PENDING_ENTRY.
- costs: adverse slippage ``SLIPPAGE`` on every fill and exit, plus
  ``COMMISSION`` per side, both in the net result;
- exits on the fill bar and after: if the stop and a target trade in the same
  bar, the stop is assumed first. Half exits at T1, then the remainder's stop
  moves to the entry price. The rest exits at T2. A gap through a level fills
  at the open. Otherwise the position closes at the close of the
  ``MAX_HOLD``-th session after the fill bar;
- results: gross R-multiple, net return % after costs, MAE/MFE in R, holding
  sessions.

Performance uses CLOSED trades only and reports INSUFFICIENT_SAMPLE below
``MIN_SAMPLE`` closed trades.
"""
from __future__ import annotations

from decimal import Decimal

SLIPPAGE = Decimal("0.001")
COMMISSION = Decimal("0.0015")
MAX_HOLD = 10
MIN_SAMPLE = 20
CANDIDATE_CLASSES = ("STRONG_CANDIDATE", "CANDIDATE")


def _d(value) -> Decimal:
    return Decimal(str(value))


def simulate(candidate: dict, later_bars: list, *, slippage: Decimal = SLIPPAGE,
             commission: Decimal = COMMISSION) -> dict:
    """later_bars: Bar-like objects strictly after candidate['session'], ascending.

    Costs default to the EGX assumptions; other markets pass their own.
    """
    low_zone, high_zone = (_d(v) for v in candidate["entry_zone"])
    stop, t1, t2 = _d(candidate["stop"]), _d(candidate["target_1"]), _d(candidate["target_2"])
    result = {"status": "PENDING_ENTRY", "events": []}
    if not later_bars:
        return result
    first = later_bars[0]
    if first.open < low_zone:
        return {"status": "NO_FILL_GAP_DOWN", "events": [{"date": first.date, "event": "NO_FILL_GAP_DOWN"}]}
    if first.open <= high_zone:
        raw_entry = first.open
    elif first.low <= high_zone:
        raw_entry = high_zone
    else:
        return {"status": "NO_FILL_GAP_UP", "events": [{"date": first.date, "event": "NO_FILL_GAP_UP"}]}
    entry = raw_entry * (1 + slippage)
    risk = raw_entry - stop
    events = [{"date": first.date, "event": "ENTRY_FILLED", "price": str(raw_entry)}]
    remaining, exits = Decimal("1"), []
    current_stop, took_t1 = stop, False
    mae = mfe = Decimal("0")
    held = 0
    for index, bar in enumerate(later_bars):
        held = index
        mae = min(mae, (bar.low - raw_entry) / risk)
        mfe = max(mfe, (bar.high - raw_entry) / risk)
        if bar.low <= current_stop:
            price = min(bar.open, current_stop) if index else current_stop
            exits.append((remaining, price))
            events.append({"date": bar.date, "event": "EXIT_STOP" if not took_t1 else "EXIT_BREAKEVEN",
                           "price": str(price)})
            remaining = Decimal("0")
            break
        if not took_t1 and bar.high >= t1:
            price = max(bar.open, t1) if index else t1
            exits.append((Decimal("0.5"), price))
            events.append({"date": bar.date, "event": "PARTIAL_T1", "price": str(price)})
            remaining, took_t1, current_stop = Decimal("0.5"), True, raw_entry
        if took_t1 and bar.high >= t2:
            price = max(bar.open, t2) if index else t2
            exits.append((remaining, price))
            events.append({"date": bar.date, "event": "EXIT_T2", "price": str(price)})
            remaining = Decimal("0")
            break
        if index >= MAX_HOLD:
            exits.append((remaining, bar.close))
            events.append({"date": bar.date, "event": "EXIT_TIME", "price": str(bar.close)})
            remaining = Decimal("0")
            break
    if remaining > 0:
        return {"status": "OPEN", "events": events, "entry": str(raw_entry),
                "mae_r": str(round(mae, 2)), "mfe_r": str(round(mfe, 2)), "sessions_held": held + 1}
    gross_exit = sum(weight * price for weight, price in exits)
    net_exit = sum(weight * price * (1 - slippage) for weight, price in exits)
    net_return = (net_exit * (1 - commission) - entry * (1 + commission)) / (entry * (1 + commission))
    last = events[-1]["event"]
    status = {"EXIT_STOP": "CLOSED_STOP", "EXIT_BREAKEVEN": "CLOSED_BREAKEVEN",
              "EXIT_T2": "CLOSED_T2", "EXIT_TIME": "CLOSED_TIME"}[last]
    return {"status": status, "events": events, "entry": str(raw_entry),
            "r_multiple_gross": str(round((gross_exit - raw_entry) / risk, 3)),
            "net_return_pct": str(round(net_return * 100, 3)),
            "mae_r": str(round(mae, 2)), "mfe_r": str(round(mfe, 2)), "sessions_held": held + 1}


def performance(lifecycles: list[dict]) -> dict:
    closed = [item for item in lifecycles if item["status"].startswith("CLOSED")]
    summary = {"candidates": len(lifecycles), "closed": len(closed),
               "open": sum(1 for i in lifecycles if i["status"] == "OPEN"),
               "pending_entry": sum(1 for i in lifecycles if i["status"] == "PENDING_ENTRY"),
               "no_fill": sum(1 for i in lifecycles if i["status"].startswith("NO_FILL")),
               "status": "INSUFFICIENT_SAMPLE" if len(closed) < MIN_SAMPLE else "MEASURED",
               "minimum_sample": MIN_SAMPLE,
               "basis": "authentic simulated Paper/Shadow lifecycles from stored bars only"}
    if not closed:
        return summary
    returns = [Decimal(i["net_return_pct"]) for i in closed]
    r_values = [Decimal(i["r_multiple_gross"]) for i in closed]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    equity, peak, drawdown = Decimal("0"), Decimal("0"), Decimal("0")
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    summary.update(
        wins=len(wins), losses=len(losses),
        win_rate_pct=str(round(Decimal(len(wins)) / len(closed) * 100, 1)),
        average_win_pct=str(round(sum(wins) / len(wins), 3)) if wins else None,
        average_loss_pct=str(round(sum(losses) / len(losses), 3)) if losses else None,
        expectancy_r=str(round(sum(r_values) / len(r_values), 3)),
        net_return_sum_pct=str(round(sum(returns), 3)),
        max_drawdown_r=str(round(drawdown, 3)),
        average_holding_sessions=str(round(Decimal(sum(i["sessions_held"] for i in closed)) / len(closed), 1)))
    return summary
