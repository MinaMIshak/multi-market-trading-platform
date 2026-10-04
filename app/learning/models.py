"""Short-horizon forecast models (reproducible, interpretable, pure Python).

FC-BASE-v1 (forecast champion): a hierarchical bucket-frequency model. Each
training row is bucketed by 20-session momentum tercile × relative-volume
bucket × EMA trend stack. Probabilities are smoothed empirical frequencies,
calibrated by construction. The expected return and adverse excursion are
winsorised bucket means. A bucket with fewer than ``MIN_BUCKET`` rows falls
back to the coarser momentum-tercile bucket, then to the global rate.

FC-BASE-SECTOR-v1 (challenger): the same, with a sector-breadth tercile added
to the finest bucket.

FC-RIDGE-v1 (challenger): ridge regression of the h-session return on the
standardised FEAT-v1 features (missing → feature mean). Threshold
probabilities come from the empirical distribution of training residuals:
P(r ≥ t) = share of residuals ≥ t − prediction.

A probability is reported only when its training support has at least
``MIN_EVENTS`` threshold events and ``MIN_BUCKET`` rows; otherwise it is
``INSUFFICIENT_SAMPLE``. No model is an LLM; nothing here guarantees a return.
"""
from __future__ import annotations

from bisect import bisect_left

from app.learning.dataset import FEATURES, HORIZONS, THRESHOLDS

MIN_BUCKET = 50
MIN_EVENTS = 30
FORECAST_HORIZONS = (1, 2, 3)
THRESHOLD_KEYS = tuple(f"{int(t * 100)}" for t in THRESHOLDS)


def _valid(row, h, cutoff=None):
    """A usable training label: VALID and, under a walk-forward cutoff, matured strictly before it."""
    label = row["labels"].get(h) or {}
    return label.get("status") == "VALID" and (cutoff is None or label["label_end"] < cutoff)


def _winsor(values, q=0.01):
    if not values:
        return []
    ordered = sorted(values)
    lo, hi = ordered[int(q * (len(ordered) - 1))], ordered[int((1 - q) * (len(ordered) - 1))]
    return [min(max(v, lo), hi) for v in values]


def _terciles(values):
    ordered = sorted(v for v in values if v is not None)
    if len(ordered) < 3:
        return None
    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def _bucket(value, cuts):
    if value is None or cuts is None:
        return None
    return 0 if value < cuts[0] else 1 if value < cuts[1] else 2


class BucketModel:
    version = "FC-BASE-v1"
    use_sector = False

    def fit(self, rows, cutoff=None):
        self.cuts = {"r20": _terciles([r["features"]["r20"] for r in rows]),
                     "sector_breadth5": _terciles([r["features"].get("sector_breadth5") for r in rows])}
        self.stats = {}
        self.train_rows = len(rows)
        for row in rows:
            for key in self._keys(row["features"]):
                for h in FORECAST_HORIZONS:
                    if not _valid(row, h, cutoff):
                        continue
                    cell = self.stats.setdefault((key, h), {"n": 0, "hits": dict.fromkeys(THRESHOLD_KEYS, 0),
                                                            "rets": [], "maes": []})
                    label = row["labels"][h]
                    cell["n"] += 1
                    cell["rets"].append(label["ret"])
                    cell["maes"].append(label["mae"])
                    for t in THRESHOLD_KEYS:
                        cell["hits"][t] += label["hits"][t]
        for cell in self.stats.values():
            rets, maes = _winsor(cell.pop("rets")), _winsor(cell.pop("maes"))
            cell["mean_ret"] = sum(rets) / len(rets)
            cell["mean_mae"] = sum(maes) / len(maes)
        return self

    def _keys(self, f):
        momentum = _bucket(f["r20"], self.cuts["r20"])
        rvol = None if f.get("rvol") is None else 0 if f["rvol"] < 1.0 else 1 if f["rvol"] < 1.5 else 2
        fine = ("fine", momentum, rvol, f["ema_stack"])
        if self.use_sector:
            fine = fine + (_bucket(f.get("sector_breadth5"), self.cuts["sector_breadth5"]),)
        return [fine, ("coarse", momentum), ("global",)]

    def predict(self, features):
        out = {}
        for h in FORECAST_HORIZONS:
            cell, key = None, None
            for candidate in self._keys(features):
                found = self.stats.get((candidate, h))
                if found and found["n"] >= MIN_BUCKET and None not in candidate:
                    cell, key = found, candidate
                    break
            if cell is None:
                out[h] = {"status": "INSUFFICIENT_SAMPLE"}
                continue
            probabilities = {t: {"p": round((cell["hits"][t] + 1) / (cell["n"] + 2), 4), "events": cell["hits"][t],
                                 "status": "OK" if cell["hits"][t] >= MIN_EVENTS else "LOW_EVENT_COUNT"}
                             for t in THRESHOLD_KEYS}
            out[h] = {"status": "OK", "expected_return": round(cell["mean_ret"], 5),
                      "expected_mae": round(cell["mean_mae"], 5), "support": cell["n"], "bucket": list(key),
                      "probabilities": probabilities}
        return out


class BucketSectorModel(BucketModel):
    version = "FC-BASE-SECTOR-v1"
    use_sector = True


def _solve(matrix, vector):
    """Gaussian elimination with partial pivoting (small symmetric positive-definite systems)."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-12:
            raise ValueError("singular system")
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


class RidgeModel:
    version = "FC-RIDGE-v1"
    lam = 10.0

    def _vector(self, f):
        return [1.0] + [((f.get(k) if f.get(k) is not None else self.means[k]) - self.means[k]) / self.stds[k]
                        for k in FEATURES]

    def fit(self, rows, cutoff=None):
        self.means, self.stds = {}, {}
        for k in FEATURES:
            values = [r["features"].get(k) for r in rows if r["features"].get(k) is not None]
            mean = sum(values) / len(values) if values else 0.0
            var = sum((v - mean) ** 2 for v in values) / len(values) if values else 1.0
            self.means[k], self.stds[k] = mean, (var ** 0.5) or 1.0
        self.coef, self.residuals, self.support = {}, {}, {}
        p = len(FEATURES) + 1
        for h in FORECAST_HORIZONS:
            xtx = [[0.0] * p for _ in range(p)]
            xty = [0.0] * p
            data = []
            for row in rows:
                if not _valid(row, h, cutoff):
                    continue
                x = self._vector(row["features"])
                y = max(min(row["labels"][h]["ret"], 1.0), -0.6)  # clip extreme labels for stability
                data.append((x, row["labels"][h]["ret"]))
                for i in range(p):
                    xty[i] += x[i] * y
                    xi = x[i]
                    row_i = xtx[i]
                    for j in range(i, p):
                        row_i[j] += xi * x[j]
            if len(data) < MIN_BUCKET:
                continue
            for i in range(p):
                for j in range(i):
                    xtx[i][j] = xtx[j][i]
                if i:
                    xtx[i][i] += self.lam
            coef = _solve(xtx, xty)
            self.coef[h] = coef
            residuals = sorted(y - sum(c * v for c, v in zip(coef, x)) for x, y in data)
            self.residuals[h] = residuals
            self.support[h] = len(data)
        self.train_rows = len(rows)
        return self

    def predict(self, features):
        out = {}
        x = self._vector(features)
        for h in FORECAST_HORIZONS:
            if h not in self.coef:
                out[h] = {"status": "INSUFFICIENT_SAMPLE"}
                continue
            pred = sum(c * v for c, v in zip(self.coef[h], x))
            residuals = self.residuals[h]
            n = len(residuals)
            probabilities = {}
            for t, key in zip(THRESHOLDS, THRESHOLD_KEYS):
                above = n - bisect_left(residuals, t - pred)
                events = sum(1 for r in residuals if r >= t)  # unconditional support for the threshold
                probabilities[key] = {"p": round((above + 1) / (n + 2), 4), "events": events,
                                      "status": "OK" if events >= MIN_EVENTS else "LOW_EVENT_COUNT"}
            contributions = sorted(((FEATURES[i - 1], round(self.coef[h][i] * x[i], 5)) for i in range(1, len(x))),
                                   key=lambda kv: -abs(kv[1]))[:4]
            out[h] = {"status": "OK", "expected_return": round(pred, 5), "expected_mae": None, "support": n,
                      "probabilities": probabilities, "top_contributions": contributions}
        return out


MODELS = {"FC-BASE-v1": BucketModel, "FC-BASE-SECTOR-v1": BucketSectorModel, "FC-RIDGE-v1": RidgeModel}
CHAMPION = "FC-BASE-v1"
__all__ = ["MODELS", "CHAMPION", "FORECAST_HORIZONS", "THRESHOLD_KEYS", "HORIZONS"]
