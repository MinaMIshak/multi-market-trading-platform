"""EGX one-position-per-ticker challenger (EGX-EXP-V2E-ONE-POSITION-v1): pure decision and metrics logic.

Pre-registered on 2026-10-15 from the EGX strategy audit (docs/EGX_STRATEGY_AUDIT_2026-10-14.md).
The hypothesis is that blocking concurrent duplicate exposure to one ticker improves risk-adjusted
Paper/Shadow performance. The rule was fixed before any forward outcome existed and was not tuned on
the audit sample. It has no cooldown, re-entry delay, ticker exception or position limit other than one.

Rule (arm V2E): V2E inherits the champion V1 unchanged:
- the same candidates (EGX-RANK-v1 STRONG_CANDIDATE / CANDIDATE);
- the same eligibility;
- the same entry zone, stop, targets and lifecycle simulator;
- the same costs.

It adds exactly one rule. When the decision for a candidate on session S is made (after S
closes), the ticker may already hold a V2E lifecycle that has not reached a terminal state by S
(still pending entry, or filled and not exited). The candidate is then BLOCKED with
ALREADY_OPEN_POSITION and opens no new position. The signal itself is kept, with its technical
class and score, the position that blocked it and a diagnostic outcome. Once that lifecycle closes
or fails to fill, a later candidate on the ticker is eligible again. Only event dates on or before S
decide the state, so nothing after the decision is used.

V2E does NOT include the V2A–V2D gates. It is compared with them, never combined with them.

Samples:
- RETROSPECTIVE_DIAGNOSTIC: candidates generated before the deployment timestamp. It only checks
  that the implementation behaves as specified and is never part of the official scorecard.
- FORWARD_OUT_OF_SAMPLE: candidates generated at or after the deployment timestamp, for sessions
  on or after the first forward session. The forward book starts flat.

Paper/Shadow research only; LIVE_MONEY=DISABLED; no automatic promotion.
"""
from __future__ import annotations

from decimal import Decimal
from statistics import mean

CHALLENGER = "EGX-EXP-V2E-ONE-POSITION-v1"
ARM = "V2E"
CONFIG_VERSION = "EGX-EXP-V2E-CFG-1"
REGISTERED_ON = "2026-10-15"
DEFINITION = ("V1 (champion EGX-RANK-v1, unchanged) + one open Paper/Shadow position per ticker: a candidate on a "
              "ticker whose earlier V2E lifecycle is still pending or open at decision time is BLOCKED "
              "(ALREADY_OPEN_POSITION); the signal is kept for research. No V2A-V2D gates.")
BLOCK_CODE = "ALREADY_OPEN_POSITION"
RETRO, FORWARD = "RETROSPECTIVE_DIAGNOSTIC", "FORWARD_OUT_OF_SAMPLE"
TERMINAL_EVENTS = {"EXIT_STOP", "EXIT_BREAKEVEN", "EXIT_T2", "EXIT_TIME", "NO_FILL_GAP_DOWN", "NO_FILL_GAP_UP"}
WIN_EVENTS = ("PARTIAL_T1", "EXIT_T2", "EXIT_TIME")
CHECKPOINTS = (20, 50, 100)
PROMOTION_POLICY = ("No auto-promotion. INSUFFICIENT_SAMPLE below 20 forward sessions or 20 closed V2E positions. "
                    "PROMOTION_CANDIDATE needs at least 50 forward sessions that span more than one market regime, "
                    "net expectancy above the champion's on the same forward candidates and a profit factor at "
                    "least as high. Lower drawdown alone never qualifies. The operator decides.")


def state_as_of(row, session):
    """TERMINAL / OPEN / PENDING_ENTRY of a lifecycle using only events dated on or before ``session``."""
    events = [e for e in row.get("events") or [] if e.get("date") and e["date"] <= session]
    if any(e["event"] in TERMINAL_EVENTS for e in events):
        return "TERMINAL"
    return "OPEN" if any(e["event"] == "ENTRY_FILLED" for e in events) else "PENDING_ENTRY"


def _order(candidates):
    return sorted(candidates, key=lambda c: (c["session"], -(c.get("score") or 0), c["ticker"]))


def book(candidates, v1_rows, *, sample):
    """One V2E book that starts flat: candidate_id -> row. ``v1_rows`` are the champion's rows (not modified)."""
    rows, held = {}, {}
    for candidate in _order(candidates):
        cid = candidate["candidate_id"]
        v1 = v1_rows[cid]
        base = {"arm": ARM, "strategy": CHALLENGER, "config_version": CONFIG_VERSION, "sample": sample,
                "ticker": candidate["ticker"], "session": candidate["session"],
                "technical_class": candidate["classification"], "technical_score": candidate.get("score")}
        prior = held.get(candidate["ticker"])
        if prior is not None:
            state = state_as_of(rows[prior], candidate["session"])
            if state != "TERMINAL":
                blocker = rows[prior]
                rows[cid] = {**base, "eligibility": "BLOCKED", "attribution": BLOCK_CODE, "status": "BLOCKED",
                             "events": [], "blocking_position": {
                                 "candidate_id": prior, "signal_session": blocker["session"],
                                 "entry_date": blocker.get("entry_date"), "state_at_decision": state}}
                continue
        rows[cid] = {**{k: v for k, v in v1.items() if k not in ("arm", "strategy", "config_version")}, **base,
                     "inherits": "V1"}
        if v1.get("eligibility") == "ELIGIBLE" and v1.get("status") != "BLOCKED":
            held[candidate["ticker"]] = cid
    return rows


def split(candidates, registration):
    """(retrospective, forward) candidates. Forward needs generation at/after deployment and a forward session."""
    deployed = (registration or {}).get("deployed_at")
    first = (registration or {}).get("first_forward_session")
    retro, forward = [], []
    for candidate in candidates:
        is_forward = bool(deployed and first and candidate.get("generated_at")
                          and candidate["generated_at"] >= deployed and candidate["session"] >= first)
        (forward if is_forward else retro).append(candidate)
    return retro, forward


# --- metrics ---------------------------------------------------------------------------------------------------

def scorecard(trades, performance):
    """The shared arm metrics plus signal counts and an explicit gross/net basis (reporting only)."""
    closed = [t for t in trades if t["status"].startswith("CLOSED") and t.get("net_r") is not None]
    gross = [float(t["r_multiple_gross"]) for t in closed if t.get("r_multiple_gross") is not None]
    perf = performance(trades, eligible=sum(1 for t in trades if t.get("eligibility") == "ELIGIBLE"),
                       blocked=sum(1 for t in trades if t["status"] == "BLOCKED"))
    return {**perf, "candidate_signals": len(trades),
            "duplicate_ticker_blocks": sum(1 for t in trades if t.get("attribution") == BLOCK_CODE),
            "independent_entries": sum(1 for t in trades if t.get("entry_date")),
            "losses": sum(1 for t in closed if t["net_r"] <= 0), "wins": sum(1 for t in closed if t["net_r"] > 0),
            "no_fills": sum(1 for t in trades if t["status"].startswith("NO_FILL")),
            "pending_entries": sum(1 for t in trades if t["status"] in ("PENDING_ENTRY", "WAITING_FOR_ENTRY")),
            "expectancy_r_gross": round(mean(gross), 3) if gross else None,
            "expectancy_r_net": perf.get("expectancy_r"),
            "r_basis": "expectancy_r / average_r / median_r / cumulative_r / max_drawdown_r are NET of the V1 cost "
                       "model; expectancy_r_gross is before costs"}


def _forward_return(bars, session, horizon):
    after = [b for b in bars if b.date > session]
    base = next((b for b in reversed(bars) if b.date <= session), None)
    if base is None or base.date != session or len(after) < horizon or not base.close:
        return None
    return round(float(after[horizon - 1].close / base.close - 1), 5)


def _win_date(row):
    return next((e["date"] for e in row.get("events") or [] if e["event"] in WIN_EVENTS), None)


def duplicates(v2e_rows, v1_rows, bars_by_ticker, *, opportunity=None):
    """Every blocked duplicate with its diagnostic V1 outcome and whether the open position already had the move."""
    out = []
    for cid, row in v2e_rows.items():
        if row.get("attribution") != BLOCK_CODE:
            continue
        hypo, blocker = v1_rows[cid], v2e_rows[row["blocking_position"]["candidate_id"]]
        bars = bars_by_ticker.get(row["ticker"], [])
        if hypo["status"].startswith("NO_FILL"):
            verdict = "WOULD_NOT_HAVE_FILLED"
        elif not hypo["status"].startswith("CLOSED") or hypo.get("net_r") is None:
            verdict = "UNRESOLVED"
        elif hypo["net_r"] <= 0:
            verdict = "DUPLICATE_LOSS_AVOIDED"
        else:
            day = _win_date(hypo) or hypo.get("exit_date")
            held = bool(blocker.get("entry_date") and day and blocker["entry_date"] <= day
                        and (blocker.get("exit_date") is None or blocker["exit_date"] >= day))
            verdict = "MOVE_ALREADY_CAPTURED_BY_OPEN_POSITION" if held else "INCREMENTAL_WINNER_EXPOSURE_BLOCKED"
        out.append({"candidate_id": cid, "ticker": row["ticker"], "signal_session": row["session"],
                    "sample": row.get("sample"), "technical_score": row.get("technical_score"),
                    "technical_class": row.get("technical_class"),
                    "opportunity_score": (opportunity or {}).get((row["session"], row["ticker"])),
                    "existing_position": {**row["blocking_position"], "status_now": blocker["status"],
                                          "exit_date": blocker.get("exit_date"), "net_r": blocker.get("net_r")},
                    "return_1s": _forward_return(bars, row["session"], 1),
                    "return_2s": _forward_return(bars, row["session"], 2),
                    "return_3s": _forward_return(bars, row["session"], 3),
                    "hypothetical_v1_status": hypo["status"], "hypothetical_v1_net_r": hypo.get("net_r"),
                    "mfe_r": hypo.get("mfe_r"), "mae_r": hypo.get("mae_r"), "classification": verdict})
    out.sort(key=lambda d: (d["signal_session"], d["ticker"]))
    counts = {}
    for item in out:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1

    def r_sum(name):
        values = [d["hypothetical_v1_net_r"] for d in out if d["classification"] == name]
        return round(sum(values), 3) if values else 0.0
    return {"blocked_duplicates": len(out), "by_classification": counts,
            "losses_avoided_r": r_sum("DUPLICATE_LOSS_AVOIDED"),
            "incremental_winners_blocked_r": r_sum("INCREMENTAL_WINNER_EXPOSURE_BLOCKED"),
            "captured_winners_r": r_sum("MOVE_ALREADY_CAPTURED_BY_OPEN_POSITION"), "items": out}


def exposure(rows, sizing_by_id, sector_by_id, days):
    """Held positions at each session close, using the EGX-EXP-CFG-1 model sizing as a common notional."""
    positions = []
    for cid, row in rows.items():
        if not row.get("entry_date"):
            continue
        size = sizing_by_id.get(cid) or {}
        sized = size.get("status") == "SIZED"
        positions.append({"ticker": row["ticker"], "sector": sector_by_id.get(cid) or "UNKNOWN",
                          "entry": row["entry_date"], "exit": row.get("exit_date"),
                          "value": Decimal(size["position_value_egp"]) if sized else Decimal(0),
                          "risk": Decimal(size["planned_risk_egp"]) if sized else Decimal(0), "sized": sized})
    if not positions:
        return {"positions": 0, "status": "NO_POSITIONS"}
    start = min(p["entry"] for p in positions)
    series = []
    for day in (d for d in days if d >= start):
        held = [p for p in positions if p["entry"] <= day and (p["exit"] is None or p["exit"] > day)]
        gross = sum((p["value"] for p in held), Decimal(0))
        by_ticker, by_sector, count_ticker = {}, {}, {}
        for p in held:
            by_ticker[p["ticker"]] = by_ticker.get(p["ticker"], Decimal(0)) + p["value"]
            by_sector[p["sector"]] = by_sector.get(p["sector"], Decimal(0)) + p["value"]
            count_ticker[p["ticker"]] = count_ticker.get(p["ticker"], 0) + 1
        hhi = float(sum((v / gross) ** 2 for v in by_ticker.values())) if gross else None
        series.append({"day": day, "positions": len(held), "tickers": len(by_ticker), "gross": gross,
                       "risk": sum((p["risk"] for p in held), Decimal(0)),
                       "max_ticker_value": max(by_ticker.values(), default=Decimal(0)),
                       "max_ticker_positions": max(count_ticker.values(), default=0),
                       "max_sector_share": float(max(by_sector.values()) / gross) if gross else None, "hhi": hhi})
    if not series:
        return {"positions": len(positions), "status": "NO_SESSIONS"}

    def avg(key):
        values = [s[key] for s in series if s[key] is not None]
        return round(float(sum(values) / len(values)), 3) if values else None
    return {"status": "MEASURED", "positions": len(positions), "unsized_positions": sum(1 for p in positions if not p["sized"]),
            "sessions": len(series), "first_session": series[0]["day"], "last_session": series[-1]["day"],
            "max_simultaneous_positions": max(s["positions"] for s in series),
            "mean_simultaneous_positions": avg("positions"),
            "max_positions_same_ticker": max(s["max_ticker_positions"] for s in series),
            "max_gross_exposure_egp": str(max(s["gross"] for s in series)),
            "mean_gross_exposure_egp": str(round(Decimal(str(avg("gross") or 0)), 2)),
            "max_open_planned_risk_egp": str(max(s["risk"] for s in series)),
            "max_single_ticker_exposure_egp": str(max(s["max_ticker_value"] for s in series)),
            "max_sector_share_of_gross": round(max((s["max_sector_share"] or 0) for s in series), 3),
            "mean_ticker_hhi": avg("hhi"),
            "basis": "Held at each session close (entry ≤ day < exit), sized with the EGX-EXP-CFG-1 model "
                     "(0.5% risk of 1M EGP); a measurement notional, not an account."}


def checkpoint(sessions):
    return ("BEFORE_CHECKPOINT_20" if sessions < 20 else "CHECKPOINT_20" if sessions < 50 else
            "CHECKPOINT_50" if sessions < 100 else "CHECKPOINT_100")


def verdict(forward_scores, sessions, regimes):
    """Forward-only verdict for V2E vs the champion on the same forward candidates; never promotes."""
    v2e, v1 = forward_scores.get(ARM) or {}, forward_scores.get("V1") or {}
    closed = v2e.get("closed") or 0
    if sessions < CHECKPOINTS[0] or closed < 20:
        return {"verdict": "INSUFFICIENT_SAMPLE", "checkpoint": checkpoint(sessions),
                "reason": f"{sessions} forward sessions, {closed} closed V2E positions (need ≥ 20 each)"}
    better = (v2e.get("expectancy_r") is not None and v1.get("expectancy_r") is not None
              and v2e["expectancy_r"] > v1["expectancy_r"] and (v2e.get("profit_factor") or 0) >= (v1.get("profit_factor") or 0))
    if not better:
        return {"verdict": "DOES_NOT_BEAT_CHAMPION", "checkpoint": checkpoint(sessions),
                "reason": "net expectancy or profit factor not better than V1 on the same forward candidates"}
    if sessions >= CHECKPOINTS[1] and len(regimes) >= 2:
        return {"verdict": "PROMOTION_CANDIDATE", "checkpoint": checkpoint(sessions),
                "reason": "ahead of V1 across ≥ 50 forward sessions spanning more than one regime; operator decision"}
    return {"verdict": "INSUFFICIENT_SAMPLE", "checkpoint": checkpoint(sessions),
            "reason": "ahead of V1, but not yet ≥ 50 forward sessions spanning more than one regime"}


def regime(report_v1):
    """Measurement label for the checkpoint policy only (never a trade filter): universe 20-session momentum sign."""
    value = report_v1.get("universe_momentum_median_20_pct")
    if value is None:
        return "UNKNOWN"
    return "RISING_BREADTH" if float(value) >= 0 else "FALLING_BREADTH"
