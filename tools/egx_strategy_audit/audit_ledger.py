"""Ledger integrity: retrospective candidates, duplicates, overlapping same-ticker lifecycles."""
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

STATE = "/home/egx-agent/er1-autopilot/state"
CAIRO = ZoneInfo("Africa/Cairo")
ledger = [json.loads(l) for l in open(f"{STATE}/egx-ranking/system-candidates.jsonl")]
report = json.load(open(f"{STATE}/egx-ranking/egx-ranking.json"))
lives = {l["candidate_id"]: l for l in report["lifecycles"]}
print("ledger", len(ledger), "unique ids", len({c["candidate_id"] for c in ledger}),
      "unique (ticker,session)", len({(c["ticker"], c["session"]) for c in ledger}))
retro = []
for c in ledger:
    life = lives.get(c["candidate_id"]) or {}
    fill = next((e for e in life.get("events", []) if e["event"] == "ENTRY_FILLED"), None)
    if fill:
        open_time = datetime.fromisoformat(fill["date"] + "T10:00:00").replace(tzinfo=CAIRO)
        generated = datetime.fromisoformat(c["generated_at"])
        if generated >= open_time:
            retro.append((c["ticker"], c["session"], c["generated_at"], fill["date"]))
print("candidates generated at/after their entry session open (retrospective):", len(retro), retro[:5])
by_ticker = Counter(c["ticker"] for c in ledger)
repeats = {t: n for t, n in by_ticker.items() if n > 1}
print("tickers with repeated candidates:", len(repeats), "candidates involved:", sum(repeats.values()))
overlap_losses = 0
closed_losses = [l for l in report["lifecycles"] if l["status"] == "CLOSED_STOP"]
for life in closed_losses:
    entry = next(e["date"] for e in life["events"] if e["event"] == "ENTRY_FILLED")
    exit_ = life["events"][-1]["date"]
    for other in report["lifecycles"]:
        if other is life or other["ticker"] != life["ticker"]:
            continue
        o_entry = next((e["date"] for e in other["events"] if e["event"] == "ENTRY_FILLED"), None)
        if o_entry and o_entry <= exit_ and (other["status"] == "OPEN" or other["events"][-1]["date"] >= entry):
            overlap_losses += 1
            break
print("stop-losses whose ticker had another simultaneously held lifecycle:", overlap_losses, "of", len(closed_losses))
exit_days = Counter(l["events"][-1]["date"] for l in closed_losses)
print("stop-loss exit days:", dict(sorted(exit_days.items())))
distinct = len({(l["ticker"], l["events"][-1]["date"]) for l in closed_losses})
print("distinct (ticker, exit day) stop events:", distinct, "vs stop-loss lifecycles:", len(closed_losses))
