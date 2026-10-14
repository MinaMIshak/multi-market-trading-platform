"""Which top winners were missed as STALE_DATA, and why were they not current?"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
closes = {t: {b["date"]: float(b["close"]) for b in repo.load(a).bars} for t, a in latest.items()}
archive = {}
for path in glob.glob(f"{STATE}/learning/egx/archive/ranking-*.json"):
    rep = json.load(open(path))
    archive[rep["session"]] = {r["ticker"]: r for r in rep["symbols"]}
outcomes = {}
for path in sorted(glob.glob(f"{STATE}/tradingview/outcomes/*.json")):
    doc = json.load(open(path))
    outcomes[doc.get("session") or path] = doc.get("symbol_outcomes") or {}
sessions = sorted({d for c in closes.values() for d in c if d >= "2026-09-28"})
for prev, day in zip(sessions, sessions[1:]):
    arch = archive.get(prev)
    if not arch:
        continue
    moves = {t: c[day] / c[prev] - 1 for t, c in closes.items() if day in c and prev in c}
    for t in sorted(moves, key=lambda x: -moves[x])[:10]:
        rec = arch.get(t)
        if not rec:
            continue
        codes = set(rec.get("rejection_reasons") or [])
        if codes & {"DATA_NOT_CURRENT", "LAST_BAR_NOT_SESSION"}:
            bars = sorted(d for d in closes[t] if d <= prev)
            outcome = outcomes.get(prev, {}).get(t)
            print(day, t, f"{moves[t] * 100:+.1f}%", "codes", sorted(codes), "freshness", rec.get("freshness"),
                  "last bar <= prior", bars[-1] if bars else None, "evidence date", rec.get("evidence_snapshot_date"),
                  "TV outcome", outcome)
print("outcome files:", list(outcomes)[:3], len(outcomes))
