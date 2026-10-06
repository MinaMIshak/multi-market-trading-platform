"""Walk-forward evaluation of the Decision-Fusion arms DF0–DF5 and factor research (chronological, columnar).

For each monthly fold starting at session S:
- the quant edges and the forecast champion are fitted on rows before S, with
  labels that matured before S;
- DF5 weights are learned only from earlier folds' out-of-sample component
  scores whose 3-session labels matured before S. Without enough of them
  (< 500 rows), DF5 uses the DF4 weights and is flagged;
- every arm scores the same rows (same symbols, sessions, evidence cutoff).

Ranking metrics per arm, on liquid symbols with valid labels, per session then
averaged:
- leader capture: recall of the actual Top-10 in the predicted Top-10 and
  Top-20, and of the Top-20 in the Top-20; Precision@5/10/20; Spearman (rank
  IC);
- returns: mean and median realised next-session and 3-session return of the
  predicted Top-10 vs the equal-weight benchmark; mean 3-session MAE of the
  Top-10; false-positive share;
- after the liquidity gate (Top-10 by score, liquid only): expectancy and
  profit factor of the 3-session return, and the maximum drawdown of the
  cumulative mean next-session return;
- splits by market regime (breadth above/below its median).

The incremental view compares each arm with the previous one. Factor research
covers conditional forward-return statistics with sample sizes and stability
by year and by regime; it is descriptive (in-sample) and labelled as such.
"""
from __future__ import annotations

from array import array
from math import isnan, nan
from statistics import median

from app.learning import fusion
from app.learning.models import BucketModel
from app.learning.walkforward import MIN_TRAIN_SESSIONS, folds, spearman

LIQUIDITY_FLOOR = {"EGX": 1_000_000.0, "US": 20_000_000.0}
METRIC_KEYS = ("spearman_r3", "recall_top10_in_top10", "recall_top10_in_top20", "recall_top20_in_top20",
               "top10_mean_r3", "top10_mean_r1")


class Store:
    """Out-of-sample evaluation rows (compact arrays)."""

    def __init__(self, components=fusion.COMPONENTS):
        self.components = components
        self.session = array("i")
        self.row = array("i")
        self.scores = {c: array("d") for c in components}
        self.r1, self.r3, self.mae3, self.end3 = array("d"), array("d"), array("d"), array("i")
        self.liquid, self.tier = array("b"), array("b")

    def add(self, ds, k, i, scores, liquid, tier=-1):
        self.session.append(k)
        self.row.append(i)
        self.tier.append(tier)
        for c in self.components:
            v = scores.get(c)
            self.scores[c].append(nan if v is None else v)
        ok1, ok3 = ds.status[1][i] == 0, ds.status[3][i] == 0
        self.r1.append(ds.ret[1][i] if ok1 else nan)
        self.r3.append(ds.ret[3][i] if ok3 else nan)
        self.mae3.append(ds.mae[3][i] if ok3 else nan)
        self.end3.append(ds.end[3][i] if ok3 else -1)
        self.liquid.append(1 if liquid else 0)

    def score_dict(self, j):
        return {c: (None if isnan(self.scores[c][j]) else self.scores[c][j]) for c in self.components}


def components_for_session(ds, members, *, market, quant, forecast_model, regime_value):
    """Point-in-time component scores. US fundamentals/catalyst are UNKNOWN in backtests (no history), never 0."""
    tech = [fusion.technical_score(lambda name, i=i: ds.f[name][i], market) for i in members]
    quant_scores = quant.scores(ds, members) if quant else [None] * len(members)
    sector = fusion.sector_scores(ds, members)
    expected = []
    for i in members:
        item = forecast_model.predict(ds.features(i)).get(3, {}) if forecast_model else {}
        expected.append(item.get("expected_return") if item.get("status") == "OK" else None)
    forecast_pct = fusion.percentiles(expected)
    out = []
    for k in range(len(members)):
        row = {"technical": tech[k], "quant": quant_scores[k], "sector": sector[k], "forecast": forecast_pct[k],
               "catalyst": None, "regime": regime_value}
        if market == "US":
            row["fundamentals"] = None
        out.append(row)
    return out


def _session_metrics(scores, r1, r3, mae3):
    n = len(scores)
    by_score = sorted(range(n), key=lambda j: -scores[j])
    by_r1 = sorted(range(n), key=lambda j: -r1[j])
    out = {"spearman_r3": spearman(scores, r3), "benchmark_r1": sum(r1) / n, "benchmark_r3": sum(r3) / n}
    top10, top20 = set(by_score[:10]), set(by_score[:20])
    act10, act20 = set(by_r1[:10]), set(by_r1[:20])
    out.update(recall_top10_in_top10=len(top10 & act10) / 10, recall_top10_in_top20=len(top20 & act10) / 10,
               recall_top20_in_top20=len(top20 & act20) / 20)
    for k in (5, 10, 20):
        out[f"precision_at_{k}"] = len(set(by_score[:k]) & set(by_r1[:k])) / k
    out["top10_r1"] = [r1[j] for j in by_score[:10]]
    out["top10_r3"] = [r3[j] for j in by_score[:10]]
    out["top10_mae3"] = [mae3[j] for j in by_score[:10]]
    return out


def arm_metrics(store, weights_by_session, arm, *, sessions_filter=None, tier=None, benchmarks=None):
    sessions = {}
    for j in range(len(store.session)):
        if not store.liquid[j] or isnan(store.r1[j]) or isnan(store.r3[j]):
            continue
        if tier is not None and store.tier[j] != tier:
            continue
        k = store.session[j]
        if sessions_filter is not None and not sessions_filter(k):
            continue
        sessions.setdefault(k, []).append(j)
    per = []
    for k in sorted(sessions):
        members = sessions[k]
        weights = weights_by_session(arm, k)
        scored = []
        for j in members:
            value, _ = fusion.opportunity(store.score_dict(j), weights)
            if value is not None:
                scored.append((value, store.r1[j], store.r3[j], store.mae3[j]))
        if len(scored) < 25:
            continue
        metrics = _session_metrics([s[0] for s in scored], [s[1] for s in scored], [s[2] for s in scored],
                                   [s[3] for s in scored])
        for name, series in (benchmarks or {}).items():
            bench = series.get(k)
            if bench is not None:
                metrics.setdefault("excess", {})[name] = sum(metrics["top10_r3"]) / 10 - bench
        per.append(metrics)
    if not per:
        return {"status": "INSUFFICIENT_SAMPLE", "sessions": 0}
    mean = lambda key: round(sum(p[key] for p in per if p[key] is not None) / max(1, sum(1 for p in per if p[key] is not None)), 4)
    top10_r3 = [v for p in per for v in p["top10_r3"]]
    wins, losses = [v for v in top10_r3 if v > 0], [v for v in top10_r3 if v <= 0]
    equity = peak = drawdown = 0.0
    for p in per:
        equity += sum(p["top10_r1"]) / len(p["top10_r1"])
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    status = "INSUFFICIENT_SAMPLE" if len(per) < 20 else "EARLY_CHECKPOINT_20" if len(per) < 50 else (
        "CHECKPOINT_50" if len(per) < 100 else "CHECKPOINT_100")
    return {"status": "OK" if len(per) >= 20 else "INSUFFICIENT_SAMPLE", "sample_label": status,
            "sessions": len(per), "spearman_mean": mean("spearman_r3"),
            "recall_top10_in_top10": mean("recall_top10_in_top10"), "recall_top10_in_top20": mean("recall_top10_in_top20"),
            "recall_top20_in_top20": mean("recall_top20_in_top20"), "missed_winner_rate_top10": round(1 - mean("recall_top10_in_top10"), 4),
            "precision_at_5": mean("precision_at_5"), "precision_at_10": mean("precision_at_10"),
            "precision_at_20": mean("precision_at_20"),
            "top10_mean_r1": round(sum(sum(p["top10_r1"]) / 10 for p in per) / len(per), 5),
            "top10_mean_r3": round(sum(top10_r3) / len(top10_r3), 5), "top10_median_r3": round(median(top10_r3), 5),
            "top10_mean_mae3": round(sum(v for p in per for v in p["top10_mae3"]) / len(top10_r3), 5),
            "benchmark_r1": round(sum(p["benchmark_r1"] for p in per) / len(per), 5),
            "benchmark_r3": round(sum(p["benchmark_r3"] for p in per) / len(per), 5),
            "false_positive_share": round(len(losses) / len(top10_r3), 4),
            "expectancy_r3_after_liquidity_gate": round(sum(top10_r3) / len(top10_r3), 5),
            "profit_factor_r3": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else None,
            "max_drawdown_cum_r1": round(drawdown, 4),
            "top10_excess_r3_vs": {name: round(sum(v) / len(v), 5) for name, v in _excess(per).items()}}


def _excess(per):
    out = {}
    for p in per:
        for name, value in (p.get("excess") or {}).items():
            out.setdefault(name, []).append(value)
    return out


def run(ds, *, market, min_train=MIN_TRAIN_SESSIONS, benchmarks=None):
    """benchmarks: {name: {session_index: forward 3-session return}} (US: SPY, QQQ), from that market only."""
    config = fusion.MARKETS[market]
    by_session = ds.by_session()
    regime, breadth = fusion.regime_series(ds, by_session)
    plan = folds(ds.sessions, min_train=min_train)
    store = Store(config["components"])
    learned_by_fold, fold_of_session = {}, {}
    for fold in plan:
        cutoff = fold["start_index"]
        train_sessions = [k for k in by_session if k < cutoff]
        quant = fusion.QuantEdge(config["quant_groups"]).fit(ds, by_session, train_sessions, cutoff)
        train_rows = array("i", (i for i in range(ds.n) if ds.row_session[i] < cutoff))
        forecast_model = BucketModel().fit(ds, train_rows, cutoff) if len(train_rows) >= 500 else None
        del train_rows
        prior = [{"scores": store.score_dict(j), "ret3": store.r3[j]} for j in range(len(store.session))
                 if store.liquid[j] and not isnan(store.r3[j]) and store.end3[j] < cutoff]
        learned_by_fold[fold["start"]] = fusion.learn_weights(prior, components=config["components"])
        del prior
        for day in fold["sessions"]:
            k = ds.session_index[day]
            fold_of_session[k] = fold["start"]
            members = by_session.get(k, [])
            if not members:
                continue
            comps = components_for_session(ds, members, market=market, quant=quant, forecast_model=forecast_model,
                                           regime_value=regime.get(k))
            tiers = fusion.percentiles([ds.f["turnover20"][i] for i in members])
            for i, scores, pct in zip(members, comps, tiers):
                tier = -1 if pct is None else 0 if pct < 100 / 3 else 1 if pct < 200 / 3 else 2
                store.add(ds, k, i, scores, (ds.value("turnover20", i) or 0) >= LIQUIDITY_FLOOR[market], tier)

    def weights_for(arm, k):
        return fusion.arm_weights(arm, learned_by_fold.get(fold_of_session.get(k)), market)

    values = [b for b in breadth.values() if b is not None]
    mid = median(values) if values else None
    results, incremental = {}, {}
    for arm in config["arms"]:
        results[arm] = {"all": arm_metrics(store, weights_for, arm, benchmarks=benchmarks)}
        if mid is not None:
            results[arm]["broad_participation"] = arm_metrics(store, weights_for, arm,
                                                              sessions_filter=lambda k: (breadth.get(k) or 0) > mid)
            results[arm]["narrow_participation"] = arm_metrics(store, weights_for, arm,
                                                               sessions_filter=lambda k: (breadth.get(k) or 0) <= mid)
        if market == "US":
            for tier, label in ((0, "lower_turnover_tier"), (1, "middle_turnover_tier"), (2, "upper_turnover_tier")):
                results[arm][label] = arm_metrics(store, weights_for, arm, tier=tier)
    names = list(config["arms"])
    for previous, current in zip(names, names[1:]):
        a, b = results[previous]["all"], results[current]["all"]
        if a.get("status") != "OK" or b.get("status") != "OK":
            incremental[f"{current} vs {previous}"] = {"status": "INSUFFICIENT_SAMPLE"}
            continue
        incremental[f"{current} vs {previous}"] = {key: round(b[key.replace("top10_mean_r3", "top10_mean_r3")] - a[key], 5)
                                                   for key in ("spearman_mean", "recall_top10_in_top10",
                                                               "recall_top10_in_top20", "top10_mean_r3", "top10_mean_r1",
                                                               "top10_mean_mae3", "max_drawdown_cum_r1")}
    learned = [w for w in learned_by_fold.values() if w]
    return {"market": market, "strategy": config["strategy"], "config": config["config"],
            "arms": {k: list(v) for k, v in config["arms"].items()}, "learned_arm": config["learned_arm"],
            "base_weights": config["weights"], "df5_cap": fusion.DF5_CAP,
            "segmentation": ("dollar-turnover tiers within session (cap proxy; research only, no segment model "
                             "deployed)" if market == "US" else None),
            "df5_latest_weights": learned[-1] if learned else None,
            "df5_folds_with_learned_weights": len(learned), "folds": len(plan),
            "evaluation_start": plan[0]["start"] if plan else None,
            "evaluation_end": plan[-1]["sessions"][-1] if plan else None,
            "training_start": ds.sessions[0] if ds.sessions else None, "evaluated_rows": len(store.session),
            "results": results, "incremental": incremental,
            "redundancy": correlations(store), "catalyst_in_backtest": "UNKNOWN (no event history before 2026-10)"}


def correlations(store, sample_every=7):
    """Spearman correlation between component scores on a deterministic sample (redundancy evidence)."""
    idx = [j for j in range(0, len(store.session), sample_every)]
    out = {}
    names = [c for c in store.components if c not in ("catalyst", "fundamentals")]
    for a_pos, a in enumerate(names):
        for b in names[a_pos + 1:]:
            pairs = [(store.scores[a][j], store.scores[b][j]) for j in idx
                     if not isnan(store.scores[a][j]) and not isnan(store.scores[b][j])]
            if len(pairs) >= 100:
                out[f"{a}~{b}"] = round(spearman([p[0] for p in pairs], [p[1] for p in pairs]) or 0, 3)
    return out


def factor_research(ds):
    """Descriptive (in-sample) conditional 3-session statistics with sample sizes and stability."""
    by_session = ds.by_session()
    regime, breadth = fusion.regime_series(ds, by_session)
    values = [b for b in breadth.values() if b is not None]
    mid = median(values) if values else None
    conditions = {}

    def add(name, i):
        conditions.setdefault(name, array("i")).append(i)

    for k, members in by_session.items():
        valid = [i for i in members if ds.status[3][i] == 0]
        if len(valid) < 20:
            continue
        rvol = fusion.percentiles([ds.f["rvol"][i] for i in valid])
        sector = fusion.percentiles([ds.f["sector_r5"][i] for i in valid])
        rs = fusion.percentiles([ds.f["rs20"][i] for i in valid])
        for pos, i in enumerate(valid):
            breakout = ds.f["breakout20"][i] == 1
            if rvol[pos] is not None:
                add("rvol_top_quintile" if rvol[pos] >= 80 else "rvol_bottom_quintile" if rvol[pos] < 20 else "rvol_middle", i)
            if sector[pos] is not None and sector[pos] >= 80:
                add("sector_strength_top_quintile", i)
                if breakout:
                    add("breakout_and_sector_strength", i)
            if breakout:
                add("breakout_any", i)
                if sector[pos] is not None and sector[pos] < 80:
                    add("breakout_without_sector_strength", i)
            if rs[pos] is not None:
                add("rs20_top_quintile" if rs[pos] >= 80 else "rs20_bottom_quintile" if rs[pos] < 20 else "rs20_middle", i)
            add("all", i)
    out = {}
    for name, rows in conditions.items():
        out[name] = _stats(ds, rows, breadth, mid)
    out["catalyst_with_confirmation"] = {"status": "INSUFFICIENT_EVIDENCE", "reason": "no historical event data"}
    out["note"] = "descriptive, in-sample; for factor efficacy only; walk-forward metrics are the decision evidence"
    return out


def _stats(ds, rows, breadth, mid):
    n = len(rows)
    if n < 200:
        return {"status": "INSUFFICIENT_SAMPLE", "n": n}
    rets = [ds.ret[3][i] for i in rows]
    out = {"status": "OK", "n": n, "mean_r3": round(sum(rets) / n, 5), "median_r3": round(median(rets), 5),
           "mean_mfe3": round(sum(ds.mfe[3][i] for i in rows) / n, 5),
           "mean_mae3": round(sum(ds.mae[3][i] for i in rows) / n, 5)}
    for t in (0.03, 0.05, 0.10, 0.20):
        out[f"p_{int(t * 100)}"] = round(sum(1 for r in rets if r >= t) / n, 4)
    by_year = {}
    for i in rows:
        by_year.setdefault(ds.session(i)[:4], []).append(ds.ret[3][i])
    out["mean_r3_by_year"] = {y: {"n": len(v), "mean": round(sum(v) / len(v), 5)} for y, v in sorted(by_year.items())
                              if len(v) >= 100}
    if mid is not None:
        broad = [ds.ret[3][i] for i in rows if (breadth.get(ds.row_session[i]) or 0) > mid]
        narrow = [ds.ret[3][i] for i in rows if (breadth.get(ds.row_session[i]) or 0) <= mid]
        out["by_regime"] = {name: ({"n": len(v), "mean": round(sum(v) / len(v), 5)} if len(v) >= 100
                                   else {"n": len(v), "status": "INSUFFICIENT_SAMPLE"})
                            for name, v in (("broad_participation", broad), ("narrow_participation", narrow))}
    return out


def gap_research(ds):
    """US gap classes of a completed session and the following 1/3-session outcomes (descriptive, labelled)."""
    by_session = ds.by_session()
    regime, breadth = fusion.regime_series(ds, by_session)
    values = [b for b in breadth.values() if b is not None]
    mid = median(values) if values else None
    groups = {}
    for i in range(ds.n):
        if ds.status[3][i] != 0 or ds.status[1][i] != 0:
            continue
        label = fusion.gap_class(ds.value("gap1", i), ds.value("intraday1", i))
        if label in ("UNKNOWN", "NO_GAP"):
            continue
        rvol = ds.value("rvol", i)
        groups.setdefault((label, "all"), array("i")).append(i)
        groups.setdefault((label, "rvol>=1.5" if (rvol or 0) >= 1.5 else "rvol<1.5"), array("i")).append(i)
        if mid is not None:
            broad = (breadth.get(ds.row_session[i]) or 0) > mid
            groups.setdefault((label, "broad_participation" if broad else "narrow_participation"), array("i")).append(i)
    out = {}
    for (label, cut), rows in sorted(groups.items()):
        n = len(rows)
        if n < 100:
            out.setdefault(label, {})[cut] = {"status": "INSUFFICIENT_SAMPLE", "n": n}
            continue
        r1 = [ds.ret[1][i] for i in rows]
        r3 = [ds.ret[3][i] for i in rows]
        out.setdefault(label, {})[cut] = {"n": n, "mean_next_r1": round(sum(r1) / n, 5),
                                          "mean_next_r3": round(sum(r3) / n, 5), "median_next_r3": round(median(r3), 5),
                                          "p_next_r3_positive": round(sum(1 for r in r3 if r > 0) / n, 4),
                                          "mean_mae3": round(sum(ds.mae[3][i] for i in rows) / n, 5)}
    out["note"] = ("descriptive (in-sample); gap = open/prev close − 1 ≥ ±2%; continuation/fade by close vs open; "
                   "earnings and catalyst splits need event history (UNKNOWN before 2026-10)")
    return out
