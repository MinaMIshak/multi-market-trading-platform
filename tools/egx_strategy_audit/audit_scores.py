"""Score bands, winner/loser cohorts, daily top-winner recall and miss reasons (pre-move evidence only)."""
import glob
import json
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

STATE = "/home/egx-agent/er1-autopilot/state"
exp = json.load(open(f"{STATE}/egx-experiment/egx-experiment.json"))
v1 = [i for i in exp["rows"]["V1"]]
closed = [i for i in v1 if i["status"].startswith("CLOSED") and i.get("net_r") is not None]
print("TECHNICAL SCORE BANDS (closed V1)")
for lo, hi in ((0, 65), (65, 75), (75, 85), (85, 101)):
    band = [i for i in closed if lo <= (i["technical_score"] or 0) < hi]
    if band:
        print(f"  [{lo},{hi}) n={len(band)} wins={sum(1 for i in band if i['net_r'] > 0)} "
              f"exp_R={round(mean(i['net_r'] for i in band), 3)} mfe={round(mean(float(i['mfe_r']) for i in band), 2)} "
              f"mae={round(mean(float(i['mae_r']) for i in band), 2)}")
for cls in ("STRONG_CANDIDATE", "CANDIDATE"):
    band = [i for i in closed if i["technical_class"] == cls]
    print(f"  {cls}: n={len(band)} exp_R={round(mean(i['net_r'] for i in band), 3) if band else None}")
print("BY CANDIDATE SESSION (closed + open V1)")
for session in sorted({i["session"] for i in v1}):
    sub = [i for i in v1 if i["session"] == session]
    c = [i for i in sub if i["status"].startswith("CLOSED")]
    print(f"  {session}: candidates={len(sub)} closed={len(c)} wins={sum(1 for i in c if (i.get('net_r') or 0) > 0)} "
          f"open={sum(1 for i in sub if i['status'] == 'OPEN')}")

# Cohort features at decision time (from the immutable decision ledger).
decisions = {(d["session"], d["ticker"]): d for d in exp["decisions"]}
ranking_archive = {}
for path in glob.glob(f"{STATE}/learning/egx/archive/ranking-*.json"):
    rep = json.load(open(path))
    ranking_archive[rep["session"]] = {r["ticker"]: (k, r) for k, r in enumerate(rep["symbols"], 1)}
cohorts = {"WINNERS": [i for i in closed if i["net_r"] > 0], "LOSERS": [i for i in closed if i["net_r"] <= 0],
           "OPEN": [i for i in v1 if i["status"] == "OPEN"]}
print("COHORTS (decision-time evidence)")
for name, items in cohorts.items():
    feats = []
    for i in items:
        rec = (ranking_archive.get(i["session"]) or {}).get(i["ticker"])
        d = decisions.get((i["session"], i["ticker"])) or {}
        if rec:
            r = rec[1]
            feats.append({"score": r.get("score"), "mom": r.get("momentum_20_pct"), "rvol": r.get("volume_ratio_20"),
                          "risk_pct": float(r.get("risk_pct") or 0), "atr_pct": r.get("atr_pct"),
                          "breakout": r.get("breakout_20"),
                          "room_r": float(d.get("resistance_gate", {}).get("room_r") or 0)
                          if d.get("resistance_gate", {}).get("room_r") else None})
    if feats:
        def avg(k):
            vals = [f[k] for f in feats if isinstance(f[k], (int, float)) and f[k] is not None]
            return round(mean(vals), 2) if vals else None
        print(f"  {name}: n={len(items)} with_archive={len(feats)} score={avg('score')} mom20={avg('mom')} "
              f"rvol={avg('rvol')} stop_dist%={avg('risk_pct')} atr%={avg('atr_pct')} "
              f"breakout_share={round(sum(1 for f in feats if f['breakout']) / len(feats), 2)} room_R={avg('room_r')}")

# Daily top-winner recall against archived pre-move ranking.
repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
closes = {t: {b["date"]: float(b["close"]) for b in repo.load(a).bars} for t, a in latest.items()}
sessions = sorted({d for c in closes.values() for d in c if d >= "2026-09-28"})
print("DAILY TOP-WINNER RECALL (prior archived V1 ranking; predicted = rank order of the report)")
reasons_all = {}
totals = {"r10@10": [], "r10@20": [], "r20@20": [], "p10": [], "p20": []}
for prev, day in zip(sessions, sessions[1:]):
    archive = ranking_archive.get(prev)
    if not archive:
        continue
    moves = {t: c[day] / c[prev] - 1 for t, c in closes.items() if day in c and prev in c and c[prev] > 0}
    actual = sorted(moves, key=lambda t: -moves[t])
    predicted = [t for t, _ in sorted(archive.items(), key=lambda kv: kv[1][0]) if t in moves]
    a10, a20, p10, p20 = set(actual[:10]), set(actual[:20]), set(predicted[:10]), set(predicted[:20])
    totals["r10@10"].append(len(a10 & p10) / 10)
    totals["r10@20"].append(len(a10 & p20) / 10)
    totals["r20@20"].append(len(a20 & p20) / 20)
    totals["p10"].append(len(p10 & a10) / 10)
    totals["p20"].append(len(p20 & a20) / 20)
    for t in actual[:10]:
        if t in p20:
            continue
        rank, rec = archive.get(t, (None, None))
        if rec is None:
            reason = "OUT_OF_UNIVERSE"
        else:
            codes = set(rec.get("rejection_reasons") or [])
            reason = ("LIQUIDITY_GATE" if "ILLIQUID" in codes else "STALE_DATA" if codes & {"DATA_NOT_CURRENT",
                      "LAST_BAR_NOT_SESSION"} else "MISSING_FEATURE" if "INSUFFICIENT_HISTORY" in codes
                      else "TECH_SCORE_TOO_LOW" if codes & {"DOWNTREND", "LOW_SCORE"} else "RANK_TOO_LOW")
        reasons_all.setdefault(reason, []).append(moves[t])
    print(f"  {day} (prior {prev}): recall top10@10={len(a10 & p10)}/10 top10@20={len(a10 & p20)}/10 "
          f"top20@20={len(a20 & p20)}/20 · top winner {actual[0]} {moves[actual[0]] * 100:+.1f}% (prior rank "
          f"{archive.get(actual[0], (None,))[0]})")
print("  MEAN:", {k: round(mean(v), 3) for k, v in totals.items() if v}, "sessions", len(totals["p10"]))
print("MISSED TOP-10 WINNER REASONS (next-session return)")
for reason, values in sorted(reasons_all.items(), key=lambda kv: -len(kv[1])):
    print(f"  {reason}: n={len(values)} mean={mean(values) * 100:.2f}% median={median(values) * 100:.2f}%")
