"""Non-overlapping exposure view: count a lifecycle only if no earlier lifecycle of the same ticker is still held."""
import json
from statistics import mean

STATE = "/home/egx-agent/er1-autopilot/state"
exp = json.load(open(f"{STATE}/egx-experiment/egx-experiment.json"))


def first_entries(items):
    kept, held_until = [], {}
    for item in sorted(items, key=lambda i: (i.get("entry_date") or "9999", i["ticker"])):
        entry = item.get("entry_date")
        if not entry:
            continue
        until = held_until.get(item["ticker"])
        if until is not None and (until == "OPEN" or until >= entry):
            continue
        kept.append(item)
        held_until[item["ticker"]] = "OPEN" if item["status"] == "OPEN" else item.get("exit_date") or entry
    return kept


for arm, items in exp["rows"].items():
    kept = first_entries(items)
    closed = [i for i in kept if i["status"].startswith("CLOSED") and i.get("net_r") is not None]
    rs = [i["net_r"] for i in closed]
    print(arm, "non-overlapping entries", len(kept), "closed", len(closed), "wins", sum(1 for r in rs if r > 0),
          "expectancy_R", round(mean(rs), 3) if rs else None, "cum_R", round(sum(rs), 2) if rs else 0,
          "open", sum(1 for i in kept if i["status"] == "OPEN"))
