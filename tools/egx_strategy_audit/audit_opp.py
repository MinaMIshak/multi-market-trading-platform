"""Opportunity vs technical rank on frozen fusion snapshots (next-session return; tiny sample)."""
import glob
import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

S = "/home/egx-agent/er1-autopilot/state/learning/egx"
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
closes = {t: {b["date"]: float(b["close"]) for b in repo.load(a).bars}
          for t, a in repo.latest_by_symbol("tradingview_tvdatafeed_egx").items()}
sessions = sorted({d for c in closes.values() for d in c if d >= "2026-10-01"})


def rank_ic(xs, ys):
    def r(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0] * len(v)
        for k, i in enumerate(order):
            out[i] = k
        return out
    a, b = r(xs), r(ys)
    ma, mb = mean(a), mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5
    return num / den if den else 0


first = True
ARMS = ['DF0', 'DF1', 'DF2', 'DF3', 'DF4', 'DF5']
TOT, BENCH = {}, []
for p in sorted(glob.glob(f"{S}/fusion/*.json")):
    d = json.load(open(p))
    s = d["session"]
    scores = d["scores"]
    if first:
        sample = list(scores.items())[:1] if isinstance(scores, dict) else scores[:1]
        print("arm", d["arm"], "sample", json.dumps(sample)[:900])
        first = False
    if s not in sessions or sessions.index(s) + 1 >= len(sessions):
        print(s, "no next session yet")
        continue
    nxt = sessions[sessions.index(s) + 1]
    items = scores.items() if isinstance(scores, dict) else [(r["ticker"], r) for r in scores]
    rows = []
    for t, r in items:
        c = closes.get(t, {})
        if s in c and nxt in c:
            arms = r.get("opportunity_by_arm") or {}
            if r.get("liquid") and all(arms.get(a) is not None for a in ARMS):
                rows.append(tuple(arms[a] for a in ARMS) + (c[nxt] / c[s] - 1,))
    if len(rows) < 20:
        print(s, "rows", len(rows))
        continue
    ret = [x[-1] for x in rows]
    line = []
    for k, a in enumerate(ARMS):
        ic = rank_ic([x[k] for x in rows], ret)
        t10 = mean(x[-1] for x in sorted(rows, key=lambda x: -x[k])[:10])
        TOT.setdefault(a, []).append((ic, t10))
        line.append(f"{a} IC {ic:+.3f} top10 {t10 * 100:+.2f}%")
    BENCH.append(mean(ret))
    print(s, "->", nxt, "liquid n", len(rows), "bench", f"{mean(ret) * 100:+.2f}%", "|", "; ".join(line))
print("MEAN over", len(BENCH), "transitions; bench", f"{mean(BENCH) * 100:+.2f}%")
for a, v in TOT.items():
    print(" ", a, "IC", f"{mean(x[0] for x in v):+.3f}", "top10 r1", f"{mean(x[1] for x in v) * 100:+.2f}%")
