"""Case cards for the biggest daily winners: prior-session archived rank, score, rejection codes and components."""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
closes = {t: {b["date"]: float(b["close"]) for b in repo.load(a).bars}
          for t, a in repo.latest_by_symbol("tradingview_tvdatafeed_egx").items()}
archive = {}
for path in glob.glob(f"{STATE}/learning/egx/archive/ranking-*.json"):
    rep = json.load(open(path))
    archive[rep["session"]] = {r["ticker"]: (k, r) for k, r in enumerate(rep["symbols"], 1)}
sessions = sorted({d for c in closes.values() for d in c if d >= "2026-10-04"})
for prev, day in zip(sessions, sessions[1:]):
    arch = archive.get(prev)
    if not arch:
        continue
    moves = {t: c[day] / c[prev] - 1 for t, c in closes.items() if day in c and prev in c and c[prev] > 0}
    for t in sorted(moves, key=lambda x: -moves[x])[:2]:
        rank, r = arch.get(t, (None, {}))
        print(f"{day} {t} {moves[t] * 100:+.1f}% | prior {prev} rank {rank}/{len(arch)} score {r.get('score')} "
              f"class {r.get('classification') or r.get('class')} codes {r.get('rejection_reasons')} "
              f"components {r.get('components')} mom20 {r.get('momentum_20_pct')} rvol {r.get('volume_ratio_20')} "
              f"freshness {r.get('freshness')}")
