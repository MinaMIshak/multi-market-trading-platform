"""Audit EGX open semantics, fill tradability and market moves around the losses."""
import glob
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
equal = total = 0
by_year = Counter(), Counter()
session_returns = {}
for ticker, artifact in latest.items():
    bars = repo.load(artifact).bars
    for prev, bar in zip(bars, bars[1:]):
        total += 1
        same = abs(float(bar["open"]) - float(prev["close"])) <= 1e-9 * max(1.0, float(prev["close"]))
        equal += same
        by_year[0][bar["date"][:4]] += same
        by_year[1][bar["date"][:4]] += 1
        if bar["date"] >= "2026-09-20":
            session_returns.setdefault(bar["date"], []).append(float(bar["close"]) / float(prev["close"]) - 1)
print(f"open == previous close: {equal}/{total} = {equal / total:.3f}")
print({y: round(by_year[0][y] / by_year[1][y], 3) for y in sorted(by_year[1])})
print("EGX equal-weight session returns (mean %, breadth):")
for day in sorted(session_returns):
    r = session_returns[day]
    print("  ", day, round(sum(r) / len(r) * 100, 2), round(sum(1 for v in r if v > 0) / len(r), 2), len(r))
rows = json.load(open("/home/egx-agent/.claude/jobs/723150e8/tmp/audit_closed.json"))
ledger = {json.loads(l)["candidate_id"]: json.loads(l) for l in open(f"{STATE}/egx-ranking/system-candidates.jsonl")}
report = json.load(open(f"{STATE}/egx-ranking/egx-ranking.json"))
inside = outside = 0
examples = []
for life in report["lifecycles"]:
    fill = next((e for e in life["events"] if e["event"] == "ENTRY_FILLED"), None)
    if not fill:
        continue
    bars = {b["date"]: b for b in repo.load(latest[life["ticker"]]).bars}
    bar = bars.get(fill["date"])
    price = float(fill["price"])
    if bar and float(bar["low"]) <= price <= float(bar["high"]):
        inside += 1
    else:
        outside += 1
        examples.append((life["ticker"], fill["date"], price, bar and (float(bar["low"]), float(bar["high"]))))
print("fills inside the entry-day traded range:", inside, "outside:", outside, examples[:8])
