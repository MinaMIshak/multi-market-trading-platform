"""Chronological walk-forward evaluation of the forecast models (never shuffled; columnar, memory-bounded).

Folds are calendar months of observed sessions after ``MIN_TRAIN_SESSIONS``.
For a fold starting at session S, training uses only rows before S and labels
that matured strictly before S (``label_end < S``), so no outcome known after
the cutoff can leak into the model. Each fold's predictions are frozen into
typed arrays and then scored against realised labels.

Metrics per model and horizon:
- error: MAE and median absolute error; directional accuracy;
- ranking: mean per-session Spearman rank correlation; Precision@K (predicted
  Top-K ∩ actual Top-K, over K); recall of the actual Top-10/Top-20 gainers
  within the predicted Top-10/Top-20; mean and median realised return of the
  predicted Top-K vs the equal-weight universe benchmark; the false-positive
  share;
- calibration: Brier score vs the climatology Brier, calibration buckets, event
  counts.

Only liquid symbols are ranked, so illiquid names cannot dominate.
"""
from __future__ import annotations

from array import array
from math import isnan, nan
from statistics import median

from app.learning.dataset import THRESHOLD_KEYS
from app.learning.models import FORECAST_HORIZONS, MODELS

MIN_TRAIN_SESSIONS = 120
K_VALUES = (5, 10, 20)


def folds(sessions, *, min_train=MIN_TRAIN_SESSIONS):
    out, current = [], None
    for k, day in enumerate(sessions[min_train:], start=min_train):
        if current is None or current["month"] != day[:7]:
            current = {"month": day[:7], "start": day, "start_index": k, "sessions": []}
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


class Predictions:
    """Parallel arrays: one entry per scored (session, symbol)."""

    def __init__(self):
        self.session, self.ticker, self.turnover20 = [], [], array("d")
        self.label = {h: array("d") for h in FORECAST_HORIZONS}       # realised return, NaN if invalid
        self.pred = {h: array("d") for h in FORECAST_HORIZONS}        # expected return, NaN if no forecast
        self.prob = {h: {t: array("d") for t in THRESHOLD_KEYS} for h in FORECAST_HORIZONS}

    def add(self, session, ticker, turnover20, labels, forecast):
        self.session.append(session)
        self.ticker.append(ticker)
        self.turnover20.append(nan if turnover20 is None else turnover20)
        for h in FORECAST_HORIZONS:
            label = labels.get(h) or {}
            self.label[h].append(label["ret"] if label.get("status") == "VALID" else nan)
            item = forecast.get(h) or {}
            ok = item.get("status") == "OK"
            self.pred[h].append(item["expected_return"] if ok else nan)
            for t in THRESHOLD_KEYS:
                self.prob[h][t].append(item["probabilities"][t]["p"] if ok else nan)

    def __len__(self):
        return len(self.session)


def evaluate(preds: Predictions, *, liquidity_floor, thresholds=None):
    from app.learning.dataset import THRESHOLDS
    thresholds = dict(zip(THRESHOLD_KEYS, THRESHOLDS))
    report = {}
    for h in FORECAST_HORIZONS:
        scored = [i for i in range(len(preds)) if not isnan(preds.label[h][i]) and not isnan(preds.pred[h][i])]
        if not scored:
            report[h] = {"status": "INSUFFICIENT_SAMPLE", "n": 0}
            continue
        errors = [abs(preds.pred[h][i] - preds.label[h][i]) for i in scored]
        direction = [(preds.pred[h][i] > 0) == (preds.label[h][i] > 0) for i in scored]
        by_session = {}
        for i in scored:
            by_session.setdefault(preds.session[i], []).append(i)
        spearmans, recall10, recall20, bench = [], [], [], []
        precision, top_returns, false_pos = ({k: [] for k in K_VALUES} for _ in range(3))
        for members in by_session.values():
            liquid = [i for i in members if not isnan(preds.turnover20[i]) and preds.turnover20[i] >= liquidity_floor]
            if len(liquid) < 25:
                continue
            p = [preds.pred[h][i] for i in liquid]
            a = [preds.label[h][i] for i in liquid]
            rho = spearman(p, a)
            if rho is not None:
                spearmans.append(rho)
            by_pred = sorted(range(len(liquid)), key=lambda j: (-p[j], preds.ticker[liquid[j]]))
            by_actual = sorted(range(len(liquid)), key=lambda j: (-a[j], preds.ticker[liquid[j]]))
            bench.append(sum(a) / len(a))
            for k in K_VALUES:
                top_pred, top_act = set(by_pred[:k]), set(by_actual[:k])
                precision[k].append(len(top_pred & top_act) / k)
                top_returns[k].append(sum(a[j] for j in by_pred[:k]) / k)
                false_pos[k].append(sum(1 for j in by_pred[:k] if a[j] < 0) / k)
            recall10.append(len(set(by_pred[:10]) & set(by_actual[:10])) / 10)
            recall20.append(len(set(by_pred[:20]) & set(by_actual[:20])) / 20)
        calibration = {}
        for t in THRESHOLD_KEYS:
            pairs = [(preds.prob[h][t][i], 1.0 if preds.label[h][i] >= thresholds[t] else 0.0) for i in scored
                     if not isnan(preds.prob[h][t][i])]
            if not pairs:
                calibration[t] = {"status": "INSUFFICIENT_SAMPLE", "n": 0}
                continue
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


def evaluate_predictions(items, *, liquidity_floor):
    """Dict-based entry point (live frozen-forecast scoring): items with session, ticker, features, labels, forecast."""
    preds = Predictions()
    for item in items:
        preds.add(item["session"], item["ticker"], (item.get("features") or {}).get("turnover20"), item["labels"],
                  item["forecast"])
    return evaluate(preds, liquidity_floor=liquidity_floor)


def run(ds, *, liquidity_floor, model_names=tuple(MODELS), min_train=MIN_TRAIN_SESSIONS):
    plan = folds(ds.sessions, min_train=min_train)
    by_session = ds.by_session()
    results = {}
    for name in model_names:
        preds = Predictions()
        for fold in plan:
            train = array("i", (i for i in range(ds.n) if ds.row_session[i] < fold["start_index"]))
            if len(train) < 500:
                continue
            model = MODELS[name]().fit(ds, train, cutoff=fold["start_index"])
            del train
            for day in fold["sessions"]:
                for i in by_session.get(ds.session_index[day], []):
                    preds.add(day, ds.ticker(i), ds.value("turnover20", i), ds.labels(i),
                              model.predict(ds.features(i)))
            del model
        results[name] = {"folds": len(plan), "predictions": len(preds),
                         "evaluation_start": plan[0]["start"] if plan else None,
                         "evaluation_end": plan[-1]["sessions"][-1] if plan else None,
                         "training_start": ds.sessions[0] if ds.sessions else None,
                         "metrics": evaluate(preds, liquidity_floor=liquidity_floor)}
        del preds
    return results
