"""Reproducible case cards: decision-time evidence, entry/fill/stop/targets, observed bars, outcome, per-arm decision."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
exp = json.load(open(f"{STATE}/egx-experiment/egx-experiment.json"))
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
decisions = {(d["session"], d["ticker"]): d for d in exp["decisions"]}
arm_rows = {}
for arm, items in exp["rows"].items():
    for i in items:
        arm_rows.setdefault((i["session"], i["ticker"]), {})[arm] = i
v1 = exp["rows"]["V1"]
closed = [i for i in v1 if i["status"].startswith("CLOSED") and i.get("net_r") is not None]


def pick():
    cases = [("V1_WINNER", i) for i in closed if i["net_r"] > 0]
    losers = sorted((i for i in closed if i["net_r"] <= 0), key=lambda i: i["net_r"])
    cases += [("V1_LOSER_WORST", losers[0]), ("V1_LOSER_MEDIAN", losers[len(losers) // 2])]
    v2d_pass = [i for i in exp["rows"]["V2D"] if i["status"].startswith("CLOSED")]
    cases += [("V2D_PASS_CLOSED", i) for i in v2d_pass[:2]]
    cap = [i for i in exp["rows"]["V2D"] if i["attribution"] == "PORTFOLIO_RISK_CAP"]
    cap_closed = [i for i in cap if arm_rows[(i["session"], i["ticker"])]["V1"]["status"].startswith("CLOSED")]
    cases += [("PORTFOLIO_CAP_BLOCK", i) for i in cap_closed[:2]]
    res = [i for i in exp["rows"]["V2B"] if i["attribution"] == "RESISTANCE_RR_FAIL"]
    res_win = [i for i in res if (arm_rows[(i["session"], i["ticker"])]["V1"].get("net_r") or 0) > 0]
    res_loss = [i for i in res if (arm_rows[(i["session"], i["ticker"])]["V1"].get("net_r") or 0) < 0]
    cases += [("RESISTANCE_FAIL_V1_WON", i) for i in res_win[:1]] + [("RESISTANCE_FAIL_V1_LOST", i) for i in res_loss[:1]]
    return cases


for label, item in pick():
    key = (item["session"], item["ticker"])
    d = decisions.get(key, {})
    rows = arm_rows[key]
    v = rows["V1"]
    print(f"=== {label}: {item['ticker']} candidate session {item['session']} score {item['technical_score']} "
          f"{item['technical_class']}")
    rg, lg, sz = d.get("resistance_gate", {}), d.get("liquidity_gate", {}), d.get("sizing", {})
    print(f"  plan: zone {d.get('entry_zone')} stop {d.get('stop')} T1 {d.get('target_1')} T2 {d.get('target_2')} "
          f"decided_at {d.get('decided_at')}")
    print(f"  gates: liquidity {lg.get('result')} ({lg.get('position_pct_of_avg_turnover')}% of avg turnover); "
          f"resistance {rg.get('result')} room {rg.get('room_r')}R to {rg.get('nearest_resistance')} "
          f"({rg.get('resistance_date')}); sizing {sz.get('status')} qty {sz.get('quantity')}")
    print("  arms:", {a: f"{r['status']}/{r.get('attribution')}/{r.get('net_r')}" for a, r in sorted(rows.items())})
    events = [(e["date"], e["event"], e.get("price")) for e in v.get("events", [])]
    print("  V1 events:", events, "MFE", v.get("mfe_r"), "MAE", v.get("mae_r"))
    art = latest.get(item["ticker"])
    if art and v.get("entry_date"):
        end = v.get("exit_date") or "9999"
        bars = [b for b in repo.load(art).bars if v["entry_date"] <= b["date"] <= end][:8]
        print("  bars (date O H L C):", [(b["date"], float(b["open"]), float(b["high"]), float(b["low"]),
                                         float(b["close"])) for b in bars])
        stop = float(d.get("stop") or 0)
        hit = next((b["date"] for b in bars if float(b["low"]) <= stop), None)
        print(f"  check: first bar low<=stop {hit}; V1 exit {v.get('exit_date')} {v['status']}")
