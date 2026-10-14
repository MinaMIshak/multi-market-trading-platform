"""Per-arm realized performance, gate counterfactuals and resistance follow-through (stored data only)."""
import json
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
exp = json.load(open(f"{STATE}/egx-experiment/egx-experiment.json"))
decisions = {d["candidate_id"]: d for d in exp["decisions"]}
rows = exp["rows"]
print("experiment session", exp["session"], "config", exp["config_version"], "decisions", len(decisions))


def pct(item):
    if not item.get("entry") or not item.get("net_return_pct"):
        return None
    return float(item["net_return_pct"])


def metrics(items):
    closed = [i for i in items if i["status"].startswith("CLOSED") and i.get("net_r") is not None]
    closed.sort(key=lambda i: (i.get("exit_date") or "", i["ticker"]))
    rs = [i["net_r"] for i in closed]
    wins = [i for i in closed if i["net_r"] > 0]
    losses = [i for i in closed if i["net_r"] <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    ev = lambda name: sum(1 for i in closed if any(e["event"] == name for e in i["events"]))
    hold = [i["sessions_held"] for i in closed]
    out = {"candidates": len(items), "eligible": sum(1 for i in items if i["eligibility"] == "ELIGIBLE"),
           "blocked": sum(1 for i in items if i["status"] == "BLOCKED"),
           "entries": sum(1 for i in items if any(e["event"] == "ENTRY_FILLED" for e in i.get("events", []))),
           "no_fill": sum(1 for i in items if i["status"] in ("NO_FILL_GAP_DOWN", "NO_FILL_GAP_UP", "BELOW_ENTRY",
                                                                 "SETUP_EXPIRED", "CHASE_BLOCKED", "INVALIDATED")),
           "pending": sum(1 for i in items if i["status"] in ("PENDING_ENTRY", "WAITING_FOR_ENTRY")),
           "open": sum(1 for i in items if i["status"] == "OPEN"), "closed": len(closed),
           "wins": len(wins), "losses": len(losses)}
    if closed:
        wp = [pct(i) for i in wins if pct(i) is not None]
        lp = [pct(i) for i in losses if pct(i) is not None]
        out.update(win_rate=round(len(wins) / len(closed), 3),
                   avg_win_pct=round(mean(wp), 2) if wp else None, median_win_pct=round(median(wp), 2) if wp else None,
                   avg_loss_pct=round(mean(lp), 2) if lp else None, median_loss_pct=round(median(lp), 2) if lp else None,
                   expectancy_r=round(mean(rs), 3), median_r=round(median(rs), 3), cum_r=round(sum(rs), 2),
                   profit_factor=round(sum(r for r in rs if r > 0) / abs(sum(r for r in rs if r <= 0)), 3)
                   if any(r <= 0 for r in rs) and sum(r for r in rs if r <= 0) else None,
                   max_dd_r=round(dd, 2), avg_hold=round(mean(hold), 2), median_hold=median(hold),
                   t1_rate=round(ev("PARTIAL_T1") / len(closed), 3), t2_rate=round(ev("EXIT_T2") / len(closed), 3),
                   stop_rate=round(ev("EXIT_STOP") / len(closed), 3),
                   avg_mfe_r=round(mean(float(i["mfe_r"]) for i in closed), 2),
                   avg_mae_r=round(mean(float(i["mae_r"]) for i in closed), 2))
    return out


arm_items = {arm: list(items) for arm, items in rows.items()}
for arm, items in arm_items.items():
    print(arm, json.dumps(metrics(items)))

v1 = {(i["session"], i["ticker"]): i for i in arm_items["V1"]}
print("\nGATE COUNTERFACTUALS vs V1 closed trades")
for arm in ("V2A", "V2B", "V2C", "V2D"):
    codes = {}
    for item in arm_items[arm]:
        if item.get("attribution") == "PASS" and item["status"] != "BLOCKED":
            continue
        base = v1.get((item["session"], item["ticker"]))
        codes.setdefault(item["attribution"], []).append(base)
    for code, bases in codes.items():
        closed = [b for b in bases if b and b["status"].startswith("CLOSED") and b.get("net_r") is not None]
        print(f"  {arm} {code}: blocked={len(bases)} V1-closed={len(closed)} losers_avoided="
              f"{sum(1 for b in closed if b['net_r'] <= 0)} winners_excluded={sum(1 for b in closed if b['net_r'] > 0)} "
              f"avg_V1_R={round(mean(b['net_r'] for b in closed), 3) if closed else None} "
              f"avg_MFE={round(mean(float(b['mfe_r']) for b in closed), 2) if closed else None} "
              f"avg_MAE={round(mean(float(b['mae_r']) for b in closed), 2) if closed else None} "
              f"V1_R_removed={round(sum(b['net_r'] for b in closed), 2) if closed else 0}")

print("\nRESISTANCE FOLLOW-THROUGH (V1 closed trades failing V2B resistance)")
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
reached = broke = 0
samples = []
for item in arm_items["V2B"]:
    if item.get("attribution") != "RESISTANCE_RR_FAIL":
        continue
    base = v1.get((item["session"], item["ticker"]))
    d = next((x for x in exp["decisions"] if x["ticker"] == item["ticker"] and x["session"] == item["session"]), None)
    if not base or not base["status"].startswith("CLOSED") or not d:
        continue
    level = d["resistance_gate"].get("nearest_resistance")
    if level is None:
        continue
    bars = [b for b in repo.load(latest[item["ticker"]]).bars if item["session"] < b["date"] <= (base.get("exit_date") or "9")]
    hi = max(float(b["high"]) for b in bars) if bars else None
    entry = float(base["entry"])
    level = float(level)
    if hi is not None and hi >= level * 0.99:
        reached += 1
        if hi > level:
            broke += 1
    samples.append((item["ticker"], item["session"], round(entry, 3), level, round(hi, 3) if hi else None,
                    d["resistance_gate"]["room_r"], round(base["net_r"], 2)))
print("resistance-fail closed V1 trades:", len(samples), "reached within 1% of resistance:", reached, "broke above:", broke)
for s in samples[:12]:
    print("   ", s)
