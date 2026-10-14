"""Audit V1 closed lifecycles: reconstruct each trade from stored bars and cross-check stop days officially."""
import glob
import json
import sys
from pathlib import Path
from collections import Counter
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.daily_cross_check import load_session_evidence  # noqa: E402
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
report = json.load(open(f"{STATE}/egx-ranking/egx-ranking.json"))
ledger = {json.loads(l)["candidate_id"]: json.loads(l) for l in open(f"{STATE}/egx-ranking/system-candidates.jsonl")}
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
bars = {t: {b["date"]: b for b in repo.load(a).bars} for t, a in latest.items() if t in {c["ticker"] for c in ledger.values()}}
official = {}
for path in sorted(glob.glob(f"{STATE}/market-watch-evidence/*")):
    day = path.rsplit("/", 1)[1]
    ev = load_session_evidence(f"{STATE}/market-watch-evidence", date.fromisoformat(day))
    official[day] = ev
print("official sessions:", {d: (e.status, len(e.rows)) for d, e in official.items()})
rows = []
for life in report["lifecycles"]:
    c = ledger[life["candidate_id"]]
    if not life["status"].startswith("CLOSED"):
        continue
    events = life["events"]
    entry_ev = next(e for e in events if e["event"] == "ENTRY_FILLED")
    exit_ev = events[-1]
    entry, stop, close_ref = float(entry_ev["price"]), float(c["stop"]), float(c["entry_reference"])
    exit_price = float(exit_ev["price"])
    b = bars[c["ticker"]]
    entry_bar, exit_bar = b.get(entry_ev["date"]), b.get(exit_ev["date"])
    sessions_between = sorted(d for d in b if c["session"] < d <= exit_ev["date"])
    path = [(d, float(b[d]["open"]), float(b[d]["low"]), float(b[d]["close"])) for d in sessions_between]
    isin = c.get("isin")
    off = official.get(exit_ev["date"])
    off_row = off.rows.get(isin) if off else None
    rows.append({"ticker": c["ticker"], "session": c["session"], "class": c["classification"], "score": c["score"],
                 "ref": close_ref, "entry": entry, "stop": stop, "stop_pct": round((stop / close_ref - 1) * 100, 2),
                 "exit_date": exit_ev["date"], "exit": exit_price, "event": exit_ev["event"],
                 "ret_pct": round((exit_price / entry - 1) * 100, 2), "held": len(path),
                 "path": path[:6], "entry_bar_open": entry_bar and float(entry_bar["open"]),
                 "exit_bar_low": exit_bar and float(exit_bar["low"]),
                 "official_exit_low": off_row and off_row.get("low"), "official_exit_close": off_row and (off_row.get("lastPrice") or off_row.get("closePrice")),
                 "official_status": off.status if off else "NO_CAPTURE"})
print("closed", len(rows), Counter(r["event"] for r in rows))
print("stop distance % (ref→stop):", sorted(r["stop_pct"] for r in rows)[:5], "...", sorted(r["stop_pct"] for r in rows)[-5:],
      "median", sorted(r["stop_pct"] for r in rows)[len(rows) // 2])
print("exit dates:", Counter(r["exit_date"] for r in rows))
print("candidate sessions:", Counter(r["session"] for r in rows))
print("held sessions:", Counter(r["held"] for r in rows))
mism = [r for r in rows if r["official_exit_low"] is not None and r["exit_bar_low"]
        and abs(r["exit_bar_low"] / float(r["official_exit_low"]) - 1) > 0.02]
print("stop-day low disagrees with official by > 2%:", len(mism), "of", sum(1 for r in rows if r["official_exit_low"] is not None))
for r in mism[:8]:
    print("   ", r["ticker"], r["exit_date"], "primary low", r["exit_bar_low"], "official low", r["official_exit_low"], r["official_status"])
for r in rows[:12]:
    print(json.dumps(r)[:400])
json.dump(rows, open("/home/egx-agent/.claude/jobs/723150e8/tmp/audit_closed.json", "w"))
