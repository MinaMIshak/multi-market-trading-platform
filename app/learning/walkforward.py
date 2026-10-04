"""Chronological walk-forward evaluation of the forecast models (never shuffled).

Folds are calendar months of observed sessions after ``MIN_TRAIN_SESSIONS``.
For a fold starting on session S, training uses only rows whose label for the
horizon matured strictly before S (``label_end < S``), so no outcome known
after the cutoff can leak into the model. Each fold's predictions are frozen
and then scored against realised labels.

Metrics per model and horizon:
- error: MAE and median absolute error of the expected return; directional
  accuracy;
- ranking: mean per-session Spearman rank correlation; Precision@K (predicted
  Top-K ∩ actual Top-K, over K); recall of the actual Top-10/Top-20 gainers
  within the predicted Top-10/Top-20; mean and median realised return of the
  predicted Top-K vs the equal-weight universe benchmark; the false-positive
  share (predicted Top-K that fell);
- calibration: Brier score and calibration buckets per threshold, with event
  counts.

Only liquid symbols are ranked (20-session average turnover ≥ the market
floor), so illiquid names cannot dominate on percentage forecasts.
"""
from __future__ import annotations

from statistics import median

from app.learning.models import FORECAST_HORIZONS, MODELS, THRESHOLD_KEYS

MIN_TRAIN_SESSIONS = 120
K_VALUES = (5, 10, 20)


def folds(sessions, *, min_train=MIN_TRAIN_SESSIONS):
    out, current = [], None
    for day in sessions[min_train:]:
        month = day[:7]
        if current is None or current["month"] != month:
            current = {"month": month, "start": day, "sessions": []}
            out.append(current)
        current["sessions"].append(day)
    return out


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def brier(pairs):
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 5) if pairs else None


def calibration_buckets(pairs, edges=(0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 1.01)):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        cell = [(p, y) for p, y in pairs if lo <= p < hi]
        if cell:
            out.append({"range": [lo, min(hi, 1.0)], "n": len(cell),
                        "predicted": round(sum(p for p, _ in cell) / len(cell), 4),
                        "realized": round(sum(y for _, y in cell) / len(cell), 4)})
    return out


def evaluate_predictions(predictions, *, liquidity_floor):
    """predictions: [{session, ticker, features, labels, forecast}] for one model."""
    report = {}
    for h in FORECAST_HORIZONS:
        scored = [p for p in predictions if p["labels"].get(h, {}).get("status") == "VALID"
                  and p["forecast"].get(h, {}).get("status") == "OK"]
        if not scored:
            report[h] = {"status": "INSUFFICIENT_SAMPLE", "n": 0}
            continue
        errors = [abs(p["forecast"][h]["expected_return"] - p["labels"][h]["ret"]) for p in scored]
        direction = [(p["forecast"][h]["expected_return"] > 0) == (p["labels"][h]["ret"] > 0) for p in scored]
        by_session = {}
        for p in scored:
            by_session.setdefault(p["session"], []).append(p)
        spearmans, precision, recall10, recall20, top_returns, bench, false_pos = [], {k: [] for k in K_VALUES}, [], [], {k: [] for k in K_VALUES}, [], {k: [] for k in K_VALUES}
        for members in by_session.values():
            liquid = [m for m in members if (m["features"].get("turnover20") or 0) >= liquidity_floor]
            if len(liquid) < 25:
                continue
            preds = [m["forecast"][h]["expected_return"] for m in liquid]
            actual = [m["labels"][h]["ret"] for m in liquid]
            rho = spearman(preds, actual)
            if rho is not None:
                spearmans.append(rho)
            by_pred = sorted(range(len(liquid)), key=lambda i: (-preds[i], liquid[i]["ticker"]))
            by_actual = sorted(range(len(liquid)), key=lambda i: (-actual[i], liquid[i]["ticker"]))
            bench.append(sum(actual) / len(actual))
            for k in K_VALUES:
                top_pred, top_act = set(by_pred[:k]), set(by_actual[:k])
                precision[k].append(len(top_pred & top_act) / k)
                top_returns[k].append(sum(actual[i] for i in by_pred[:k]) / k)
                false_pos[k].append(sum(1 for i in by_pred[:k] if actual[i] < 0) / k)
            recall10.append(len(set(by_pred[:10]) & set(by_actual[:10])) / 10)
            recall20.append(len(set(by_pred[:20]) & set(by_actual[:20])) / 20)
        calibration = {}
        for t in THRESHOLD_KEYS:
            pairs = [(p["forecast"][h]["probabilities"][t]["p"], 1.0 if p["labels"][h]["hits"][t] else 0.0)
                     for p in scored]
            events = int(sum(y for _, y in pairs))
            calibration[t] = {"brier": brier(pairs), "events": events, "n": len(pairs),
                              "base_rate": round(events / len(pairs), 4),
                              "brier_climatology": brier([(events / len(pairs), y) for _, y in pairs]),
                              "buckets": calibration_buckets(pairs),
                              "status": "OK" if events >= 30 else "INSUFFICIENT_SAMPLE"}
        sessions = len(spearmans)
        report[h] = {
            "status": "OK" if sessions >= 20 else "INSUFFICIENT_SAMPLE", "n": len(scored), "ranked_sessions": sessions,
            "mae": round(sum(errors) / len(errors), 5), "median_ae": round(median(errors), 5),
            "directional_accuracy": round(sum(direction) / len(direction), 4),
            "spearman_mean": round(sum(spearmans) / sessions, 4) if sessions else None,
            "precision_at_k": {k: round(sum(v) / len(v), 4) for k, v in precision.items() if v},
            "recall_top10_in_pred10": round(sum(recall10) / len(recall10), 4) if recall10 else None,
            "recall_top20_in_pred20": round(sum(recall20) / len(recall20), 4) if recall20 else None,
            "topk_mean_return": {k: round(sum(v) / len(v), 5) for k, v in top_returns.items() if v},
            "topk_median_return": {k: round(median(v), 5) for k, v in top_returns.items() if v},
            "benchmark_mean_return": round(sum(bench) / len(bench), 5) if bench else None,
            "false_positive_share": {k: round(sum(v) / len(v), 4) for k, v in false_pos.items() if v},
            "calibration": calibration}
    return report


def run(dataset, *, liquidity_floor, model_names=tuple(MODELS), min_train=MIN_TRAIN_SESSIONS):
    sessions = dataset["sessions"]
    plan = folds(sessions, min_train=min_train)
    by_session = {}
    for row in dataset["rows"]:
        by_session.setdefault(row["session"], []).append(row)
    results = {}
    for name in model_names:
        predictions = []
        for fold in plan:
            # Rows before the fold; labels count only if they matured before the fold start (no copies made).
            train = [r for r in dataset["rows"] if r["session"] < fold["start"]]
            if len(train) < 500:
                continue
            model = MODELS[name]().fit(train, cutoff=fold["start"])
            del train
            for day in fold["sessions"]:
                for row in by_session.get(day, []):
                    predictions.append({"session": day, "ticker": row["ticker"], "features": row["features"],
                                        "labels": row["labels"], "forecast": model.predict(row["features"])})
        evaluated = evaluate_predictions(predictions, liquidity_floor=liquidity_floor)
        results[name] = {"folds": len([f for f in plan]), "predictions": len(predictions),
                         "evaluation_start": plan[0]["start"] if plan else None,
                         "evaluation_end": plan[-1]["sessions"][-1] if plan else None,
                         "training_start": sessions[0] if sessions else None, "metrics": evaluated}
    return results
