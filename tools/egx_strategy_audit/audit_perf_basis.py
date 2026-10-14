"""Reconcile V1 headline expectancy/drawdown: gross vs net R and list order vs exit-date order."""
import json

STATE = "/home/egx-agent/er1-autopilot/state"
report = json.load(open(f"{STATE}/egx-ranking/egx-ranking.json"))
exp = json.load(open(f"{STATE}/egx-experiment/egx-experiment.json"))
lives = report["lifecycles"]
closed = [l for l in lives if l["status"].startswith("CLOSED")]
print("lifecycle list order keys (first 6):", [(l["ticker"], l.get("session")) for l in lives[:6]])


def dd(values):
    equity = peak = low = 0.0
    for v in values:
        equity += v
        peak = max(peak, equity)
        low = min(low, equity - peak)
    return round(low, 3)


gross_list = [float(l["r_multiple_gross"]) for l in closed]
by_exit = sorted(closed, key=lambda l: (l["events"][-1]["date"], l["ticker"]))
gross_exit = [float(l["r_multiple_gross"]) for l in by_exit]
net = {(i["session"], i["ticker"]): i["net_r"] for i in exp["rows"]["V1"] if i.get("net_r") is not None}
net_exit = [net[(l["session"], l["ticker"])] for l in by_exit if (l["session"], l["ticker"]) in net]
print("headline (report):", report["performance"]["expectancy_r"], report["performance"]["max_drawdown_r"])
print("gross R, list order: expectancy", round(sum(gross_list) / len(gross_list), 3), "DD", dd(gross_list))
print("gross R, exit order: DD", dd(gross_exit))
print("net R (experiment), exit order: n", len(net_exit), "expectancy", round(sum(net_exit) / len(net_exit), 3),
      "DD", dd(net_exit))
loss = next(l for l in closed if l["status"] == "CLOSED_STOP")
key = (loss["session"], loss["ticker"])
print("example:", key, "gross R", loss["r_multiple_gross"], "net return %", loss["net_return_pct"], "net R", net.get(key))
