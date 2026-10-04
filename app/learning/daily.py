"""Daily session learning and short-horizon upside forecasts (research only; never a trade decision).

Per market, after each completed session:
1. Build the session-aligned dataset (app/learning/dataset.py) from validated
   primary bars.
2. Fit the forecast champion and challengers on matured labels only. Score every
   symbol at the latest session and freeze the forecast file
   ``forecasts/<session>.json`` (written once, never overwritten).
3. Score earlier frozen forecasts whose horizons have matured (live
   out-of-sample evidence, separate from walk-forward backtests).
4. Daily winners of the latest session with the state known before the move:
   features at the previous session, the archived V1 ranking and the frozen
   forecast for that session. Missed-winner and hit records follow.
5. Sector analytics and market-flow proxies (turnover, breadth, activity
   concentration; never "institutional flow").
6. Structured events from the context report, with publication and
   first-seen times. They are logged and attributed only when published
   before the forecast cutoff, and are not model inputs until event history
   exists.
Technical class, trade eligibility and forecasts stay separate concepts.
LIVE_MONEY=DISABLED.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from app.learning import events as event_layer
from app.learning.dataset import FEATURE_SCHEMA, VERSION as DATA_VERSION, build, label_counts
from app.learning.models import CHAMPION, FORECAST_HORIZONS, MODELS
from app.learning.walkforward import evaluate_predictions

SCHEMA = "learning-report-v1"
LIQUIDITY_FLOOR = {"EGX": 1_000_000.0, "US": 20_000_000.0}
TOP_WINNERS = (10, 20)


def _write_once(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as stream:
            stream.write(json.dumps(document, sort_keys=True, default=str) + "\n")
        return True
    except FileExistsError:
        return False


def _write_atomic(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)


def _compact(forecast):
    out = {}
    for h, item in forecast.items():
        if item.get("status") != "OK":
            out[str(h)] = {"status": item.get("status")}
            continue
        out[str(h)] = {"status": "OK", "expected_return": item["expected_return"], "expected_mae": item.get("expected_mae"),
                       "support": item["support"],
                       "p": {t: v["p"] for t, v in item["probabilities"].items()},
                       "p_status": {t: v["status"] for t, v in item["probabilities"].items()},
                       **({"top_contributions": item["top_contributions"]} if item.get("top_contributions") else {}),
                       **({"bucket": item["bucket"]} if item.get("bucket") else {})}
    return out


def confidence(item, walkforward):
    """HIGH / MEDIUM / LOW / UNVALIDATED / INSUFFICIENT_SAMPLE from support and out-of-sample evidence."""
    if not item or item.get("status") != "OK":
        return "INSUFFICIENT_SAMPLE"
    metrics = ((walkforward or {}).get(CHAMPION) or {}).get("metrics", {}).get("3") or \
        ((walkforward or {}).get(CHAMPION) or {}).get("metrics", {}).get(3)
    if not metrics or metrics.get("status") != "OK":
        return "UNVALIDATED"
    skill = (metrics.get("spearman_mean") or 0) > 0.03
    if item["support"] >= 1000 and skill:
        return "HIGH"
    if item["support"] >= 200 and skill:
        return "MEDIUM"
    return "LOW"


def upside_ranking(rows, forecasts, *, market, walkforward, limit=25):
    """Liquid symbols only; ranked by the champion's expected 3-session return, then P(+5%)."""
    floor = LIQUIDITY_FLOOR[market]
    ranked = []
    for row in rows:
        champion = forecasts[row["ticker"]][CHAMPION]
        h3 = champion.get("3") or {}
        if h3.get("status") != "OK":
            continue
        liquid = (row["features"].get("turnover20") or 0) >= floor
        ranked.append({"ticker": row["ticker"], "isin": row.get("isin"), "sector": row["sector"], "liquid": liquid,
                       "expected_r1": (champion.get("1") or {}).get("expected_return"),
                       "expected_r3": h3["expected_return"], "expected_mae3": h3.get("expected_mae"),
                       "p": h3["p"], "p_status": h3["p_status"], "support": h3["support"],
                       "confidence": confidence({"status": "OK", "support": h3["support"]}, walkforward),
                       "challengers": {name: (forecasts[row["ticker"]][name].get("3") or {}).get("expected_return")
                                       for name in MODELS if name != CHAMPION},
                       "rvol": row["features"].get("rvol"), "turnover20": row["features"].get("turnover20"),
                       "breakout20": row["features"].get("breakout20"), "r20": row["features"].get("r20"),
                       "high52_dist": row["features"].get("high52_dist"), "sector_breadth5": row["features"].get("sector_breadth5"),
                       "sector_turnover_accel": row["features"].get("sector_turnover_accel")})
    # Champion expected return first; the bucket champion is coarse, so ties are broken by the continuous
    # ridge challenger estimate, then by the champion's P(+5%). Documented in docs/LEARNING_AND_FORECASTING.md.
    eligible = sorted((r for r in ranked if r["liquid"]),
                      key=lambda r: (-r["expected_r3"], -(r["challengers"].get("FC-RIDGE-v1") or 0),
                                     -r["p"].get("5", 0), r["ticker"]))
    for index, item in enumerate(eligible, 1):
        item["forecast_rank"] = index
    return eligible[:limit], len(ranked) - len(eligible)


def archive_rankings(state_dir, snapshots_root, current_report):
    """Immutable per-session copies of the V1 ranking report (from published bundles and the current file)."""
    archive = Path(state_dir) / "archive"
    sources = sorted(glob.glob(str(Path(snapshots_root) / "published-*" / "egx-ranking.json"))) if snapshots_root else []
    for path in sources + ([current_report] if current_report else []):
        try:
            report = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        if report.get("schema") == "egx-ranking-report-v1" and report.get("session"):
            _write_once(archive / f"ranking-{report['session']}.json", report)
    out = {}
    for path in glob.glob(str(archive / "ranking-*.json")):
        out[Path(path).stem.replace("ranking-", "")] = path
    return out


def _v1_state(archives, session):
    path = archives.get(session)
    if not path:
        return None
    report = json.loads(Path(path).read_text())
    state = {}
    for index, record in enumerate(report["symbols"], 1):
        state[record["ticker"]] = {"v1_rank": index, "technical_class": record.get("classification"),
                                   "technical_score": record.get("score"),
                                   "rejection_reasons": record.get("rejection_reasons") or []}
    return state


def missed_reasons(prior, v1, forecast_entry, prior_events, *, market):
    reasons = []
    if prior is None:
        return ["OUT_OF_UNIVERSE"]
    f = prior["features"]
    if (f.get("turnover20") or 0) < LIQUIDITY_FLOOR[market]:
        reasons.append("LIQUIDITY_GATE")
    if v1:
        codes = set(v1["rejection_reasons"])
        if "ILLIQUID" in codes:
            reasons.append("LIQUIDITY_GATE")
        if codes & {"DOWNTREND", "LOW_SCORE"}:
            reasons.append("MOMENTUM_THRESHOLD")
        if codes & {"DATA_NOT_CURRENT", "LAST_BAR_NOT_SESSION"}:
            reasons.append("STALE_DATA")
        if codes & {"INSUFFICIENT_HISTORY"}:
            reasons.append("FEATURE_UNAVAILABLE")
        if v1["technical_class"] in ("WATCHLIST", "NO_TRADE") and not f.get("breakout20"):
            reasons.append("BREAKOUT_THRESHOLD")
    if forecast_entry is None:
        reasons.append("INSUFFICIENT_SOURCE_EVIDENCE" if v1 is None else "MISSED_WINNER_RANK_TOO_LOW")
    else:
        reasons.append("MODEL_UNDERESTIMATION")
    if not prior_events:
        reasons.append("NO_CATALYST")
    return sorted(set(reasons))


def winners_review(dataset, *, market, archives, frozen_prior, events, cutoff_iso):
    sessions = dataset["sessions"]
    if len(sessions) < 2:
        return {"status": "INSUFFICIENT_SAMPLE"}
    latest, previous = sessions[-1], sessions[-2]
    today = {r["ticker"]: r for r in dataset["rows"] if r["session"] == latest}
    prior = {r["ticker"]: r for r in dataset["rows"] if r["session"] == previous}
    movers = sorted((r for r in today.values() if r["ticker"] in prior), key=lambda r: (-r["features"]["r1"], r["ticker"]))
    v1 = _v1_state(archives, previous) if market == "EGX" else None
    forecast_ranks = {}
    if frozen_prior:
        forecast_ranks = {item["ticker"]: item for item in frozen_prior.get("upside_top", [])}
    caught_top10 = 0
    winners = []
    for rank, row in enumerate(movers[:TOP_WINNERS[1]], 1):
        before = prior[row["ticker"]]
        v1_state = (v1 or {}).get(row["ticker"])
        forecast_entry = forecast_ranks.get(row["ticker"])
        known_events = [e for e in events.get("by_isin", {}).get(row.get("isin"), [])
                        if e.get("published_at") and max(e["published_at"], e.get("first_seen") or e["published_at"])
                        <= cutoff_iso]
        caught = bool((forecast_entry and forecast_entry["forecast_rank"] <= 20) or
                      (v1_state and v1_state["technical_class"] in ("STRONG_CANDIDATE", "CANDIDATE")))
        if rank <= 10 and caught:
            caught_top10 += 1
        f = before["features"]
        winners.append({"rank": rank, "ticker": row["ticker"], "sector": row["sector"],
                        "actual_return": round(row["features"]["r1"], 5), "turnover": row["features"]["turnover"],
                        "relative_volume_session": row["features"].get("rvol"),
                        "prior_session": previous, "prior_v1": v1_state,
                        "prior_forecast": ({k: forecast_entry.get(k) for k in ("forecast_rank", "expected_r1", "expected_r3", "p")}
                                           if forecast_entry else None),
                        "prior_forecast_status": "AVAILABLE" if forecast_entry else (
                            "NOT_IN_PRIOR_TOP" if frozen_prior else "NO_FROZEN_FORECAST_FOR_PRIOR_SESSION"),
                        "caught": caught,
                        "miss_reasons": [] if caught else missed_reasons(before, v1_state, forecast_entry, known_events,
                                                                         market=market),
                        "prior_state": {k: (round(f[k], 5) if isinstance(f.get(k), float) else f.get(k)) for k in (
                            "rvol", "turnover20", "turnover_accel", "ema_stack", "r20", "breakout20", "high52_dist",
                            "rs20", "sector_r5", "sector_breadth5", "sector_turnover_accel")},
                        "known_events_before_cutoff": [{k: e.get(k) for k in ("event_type", "published_at", "heading")}
                                                       for e in known_events[:3]]})
    hits = []
    for item in (frozen_prior or {}).get("upside_top", [])[:10]:
        row = today.get(item["ticker"])
        if row is None:
            continue
        realized = row["features"]["r1"]
        before = prior.get(item["ticker"])
        hits.append({"ticker": item["ticker"], "forecast_rank": item["forecast_rank"], "predicted_p": item["p"],
                     "expected_r1": item.get("expected_r1"), "actual_return_1": round(realized, 5),
                     "strong_result": realized >= 0.03, "sector": item.get("sector"),
                     "prior_state": {k: before["features"].get(k) for k in ("rvol", "turnover_accel", "breakout20",
                                                                            "r20", "sector_breadth5")} if before else None,
                     "contributions": item.get("challengers")})
    top_turnover = sorted(today.values(), key=lambda r: -r["features"]["turnover"])[:10]
    top_rvol = sorted((r for r in today.values() if r["features"].get("rvol")), key=lambda r: -r["features"]["rvol"])[:10]
    return {"status": "AVAILABLE", "session": latest, "prior_session": previous, "winners": winners,
            "losers": [{"ticker": r["ticker"], "actual_return": round(r["features"]["r1"], 5)} for r in movers[-10:][::-1]],
            "top_turnover": [{"ticker": r["ticker"], "turnover": round(r["features"]["turnover"])} for r in top_turnover],
            "top_relative_volume": [{"ticker": r["ticker"], "rvol": round(r["features"]["rvol"], 2)} for r in top_rvol],
            "caught_top10": caught_top10, "v1_archive_available": v1 is not None, "hits": hits,
            "frozen_forecast_available": frozen_prior is not None}


def sector_analytics(dataset):
    latest = dataset["sessions"][-1]
    rows = [r for r in dataset["rows"] if r["session"] == latest]
    total_turnover = sum(r["features"]["turnover"] for r in rows) or 1.0
    benchmark = sum(r["features"]["r1"] for r in rows) / len(rows) if rows else None
    sectors = {}
    for r in rows:
        sectors.setdefault(r["sector"] or "UNMAPPED", []).append(r)
    out = []
    for name, members in sectors.items():
        f = [m["features"] for m in members]
        r1s = [x["r1"] for x in f]
        accel = [x["turnover_accel"] for x in f if x.get("turnover_accel")]
        out.append({"sector": name, "members": len(members), "return_1": round(sum(r1s) / len(r1s), 5),
                    "return_5": round(sum(x["r5"] for x in f) / len(f), 5),
                    "relative_to_universe_1": round(sum(r1s) / len(r1s) - benchmark, 5) if benchmark is not None else None,
                    "advancing": sum(1 for v in r1s if v > 0), "declining": sum(1 for v in r1s if v < 0),
                    "breadth": round(sum(1 for v in r1s if v > 0) / len(r1s), 3),
                    "turnover": round(sum(x["turnover"] for x in f)),
                    "turnover_share": round(sum(x["turnover"] for x in f) / total_turnover, 4),
                    "turnover_acceleration": round(sum(accel) / len(accel), 3) if accel else None,
                    "median_relative_volume": round(median([x["rvol"] for x in f if x.get("rvol")]), 2)
                    if any(x.get("rvol") for x in f) else None,
                    "breakouts": sum(1 for x in f if x.get("breakout20")),
                    "high_rvol_names": sum(1 for x in f if (x.get("rvol") or 0) >= 1.5),
                    "volatility_20": round(median([x["vol20"] for x in f]), 4)})
    out.sort(key=lambda s: -s["return_1"])
    return {"session": latest, "benchmark": "equal-weight universe mean (not an index)",
            "universe_return_1": round(benchmark, 5) if benchmark is not None else None, "sectors": out,
            "terminology": "market-flow proxies from turnover, breadth and activity; not institutional flow"}


def live_scoring(state_dir, dataset, *, market):
    by_key = {(r["session"], r["ticker"]): r for r in dataset["rows"]}
    per_model = {name: [] for name in MODELS}
    files = sorted(glob.glob(str(Path(state_dir) / "forecasts" / "*.json")))
    for path in files:
        document = json.loads(Path(path).read_text())
        session = document["session"]
        for ticker, forecasts in document["forecasts"].items():
            row = by_key.get((session, ticker))
            if row is None:
                continue
            for name, compact in forecasts.items():
                if name not in per_model:
                    continue
                forecast = {int(h): ({"status": "OK", "expected_return": v["expected_return"],
                                      "probabilities": {t: {"p": p} for t, p in v["p"].items()}}
                                     if v.get("status") == "OK" else {"status": v.get("status")}) for h, v in compact.items()}
                per_model[name].append({"session": session, "ticker": ticker, "features": row["features"],
                                        "labels": row["labels"], "forecast": forecast})
    return {"frozen_forecast_files": len(files),
            "models": {name: evaluate_predictions(preds, liquidity_floor=LIQUIDITY_FLOOR[market])
                       for name, preds in per_model.items()}}


def run_market(*, market, series, state_dir, now, build_revision, context=None, snapshots_root=None,
               ranking_report=None):
    state_dir = Path(state_dir)
    dataset = build(series)
    sessions = dataset["sessions"]
    latest = sessions[-1]
    walkforward_path = state_dir / "walkforward.json"
    walkforward = json.loads(walkforward_path.read_text()) if walkforward_path.is_file() else None
    models = {name: cls().fit(dataset["rows"]) for name, cls in MODELS.items()}
    counts = label_counts(dataset)
    # Memory: after fitting keep only sessions still needed (latest, prior, frozen-forecast sessions).
    frozen_sessions = {Path(p).stem for p in glob.glob(str(state_dir / "forecasts" / "*.json"))}
    keep = {latest, sessions[-2] if len(sessions) > 1 else latest} | frozen_sessions
    dataset["rows"] = [r for r in dataset["rows"] if r["session"] in keep]
    latest_rows = [r for r in dataset["rows"] if r["session"] == latest]
    forecasts = {r["ticker"]: {name: _compact(model.predict(r["features"])) for name, model in models.items()}
                 for r in latest_rows}
    top, illiquid_excluded = upside_ranking(latest_rows, forecasts, market=market,
                                            walkforward=(walkforward or {}).get("models"))
    events = event_layer.extract(context, market=market)
    frozen = {"schema": "frozen-forecast-v1", "market": market, "session": latest, "generated_at": now.isoformat(),
              "forecast_cutoff": now.isoformat(), "feature_snapshot_session": latest, "build_revision": build_revision,
              "data_version": DATA_VERSION, "feature_schema": FEATURE_SCHEMA, "champion": CHAMPION,
              "models": {name: {"model_version": name, "training_start": sessions[0], "training_end": latest,
                                "train_rows": getattr(model, "train_rows", None)} for name, model in models.items()},
              "forecasts": forecasts, "upside_top": top}
    newly_frozen = _write_once(state_dir / "forecasts" / f"{latest}.json", frozen)
    frozen_files = sorted(glob.glob(str(state_dir / "forecasts" / "*.json")))
    prior_frozen = None
    if len(sessions) >= 2:
        prior_path = state_dir / "forecasts" / f"{sessions[-2]}.json"
        prior_frozen = json.loads(prior_path.read_text()) if prior_path.is_file() else None
    archives = archive_rankings(state_dir, snapshots_root, ranking_report) if market == "EGX" else {}
    cutoff = (prior_frozen or {}).get("forecast_cutoff") or f"{sessions[-2]}T23:59:59+00:00"
    review = winners_review(dataset, market=market, archives=archives, frozen_prior=prior_frozen,
                            events=events, cutoff_iso=cutoff)
    return {"market": market, "latest_session": latest, "data_version": DATA_VERSION, "feature_schema": FEATURE_SCHEMA,
            "labels": counts, "history": {"first_session": sessions[0], "last_session": latest,
                                                         "sessions": len(sessions), "symbols": len(series)},
            "survivorship_note": "universe = symbols with a current admitted series; delisted names absent",
            "champion": CHAMPION, "challengers": [n for n in MODELS if n != CHAMPION],
            "forecast": {"session": latest, "frozen_now": newly_frozen, "frozen_files": len(frozen_files),
                         "upside_top": top, "illiquid_excluded": illiquid_excluded,
                         "liquidity_floor": LIQUIDITY_FLOOR[market]},
            "walkforward": walkforward, "live_scoring": live_scoring(state_dir, dataset, market=market),
            "winners": review, "sectors": sector_analytics(dataset), "events": events["summary"]}


def write_report(path, markets, *, now, build_revision):
    _write_atomic(path, {"schema": SCHEMA, "generated_at": now.isoformat(), "build_revision": build_revision,
                         "live_money": False, "use": "RESEARCH_ONLY_NOT_A_TRADE_DECISION",
                         "markets": markets})


def main(argv=None):
    import argparse
    import sys
    from app.learning import sources
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True, help="absolute learning state root")
    parser.add_argument("--report", required=True)
    parser.add_argument("--egx-db")
    parser.add_argument("--egx-data")
    parser.add_argument("--egx-evidence")
    parser.add_argument("--egx-ranking-report")
    parser.add_argument("--snapshots-root")
    parser.add_argument("--us-data")
    parser.add_argument("--us-report")
    parser.add_argument("--context-report")
    parser.add_argument("--build-revision", default=os.getenv("EGX_BUILD_REVISION"))
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    context = None
    if args.context_report and Path(args.context_report).is_file():
        context = json.loads(Path(args.context_report).read_text())
    markets, status = {}, {}
    previous = {}
    if Path(args.report).is_file():
        try:
            previous = json.loads(Path(args.report).read_text()).get("markets", {})
        except ValueError:
            previous = {}
    for market in ("EGX", "US"):
        try:
            if market == "EGX" and args.egx_db:
                series = sources.load_egx(args.egx_db, args.egx_data, args.egx_evidence)
            elif market == "US" and args.us_data:
                series = sources.load_us(args.us_data, args.us_report)
            else:
                if market in previous:
                    markets[market] = previous[market]
                continue
            markets[market] = run_market(market=market, series=series, state_dir=Path(args.state) / market.lower(),
                                         now=now, build_revision=args.build_revision, context=context,
                                         snapshots_root=args.snapshots_root, ranking_report=args.egx_ranking_report)
            status[market] = "LEARNED"
        except Exception as exc:  # one market failing never blocks the other
            status[market] = f"FAILED:{type(exc).__name__}:{str(exc)[:120]}"
            if market in previous:
                markets[market] = {**previous[market], "stale_since_failure": now.isoformat()}
    write_report(args.report, markets, now=now, build_revision=args.build_revision)
    print(json.dumps({"status": status, "live_money": False}, sort_keys=True))
    return 0 if any(v == "LEARNED" for v in status.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
