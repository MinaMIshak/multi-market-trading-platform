"""Short-horizon forecast models (reproducible, interpretable, pure Python, columnar fitting).

FC-BASE-v1 (forecast champion): a hierarchical bucket-frequency model. Each
training row is bucketed by 20-session momentum tercile × relative-volume
bucket × EMA trend stack. Probabilities are smoothed empirical frequencies,
calibrated by construction. The expected return and adverse excursion are
winsorised bucket means. A bucket with fewer than ``MIN_BUCKET`` rows falls
back to the momentum-tercile bucket, then to the global rate.

FC-BASE-SECTOR-v1 (challenger): the same, with a sector-breadth tercile added
to the finest bucket.

FC-RIDGE-v1 (challenger): ridge regression of the h-session return on the
standardised FEAT-v1 features (missing → feature mean). Threshold
probabilities come from the empirical training-residual distribution.

``fit(ds, rows, cutoff=None)`` reads columns of a LEARN-DATA-v2 dataset. With a
cutoff (a session index), a label counts only if it matured strictly before
that session (no look-ahead). A probability with fewer than ``MIN_EVENTS``
training events is flagged ``LOW_EVENT_COUNT``. Without support a forecast is
``INSUFFICIENT_SAMPLE``. No model is an LLM; nothing here guarantees a return.
"""
from __future__ import annotations

from array import array
from bisect import bisect_left
from math import isnan

from app.learning.dataset import FEATURES, HORIZONS, THRESHOLD_KEYS, THRESHOLDS

MIN_BUCKET = 50
MIN_EVENTS = 30
FORECAST_HORIZONS = (1, 2, 3)


def _terciles(values):
    ordered = sorted(v for v in values if v is not None and not isnan(v))
    if len(ordered) < 3:
        return None
    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def _bucket(value, cuts):
    if value is None or cuts is None or isnan(value):
        return None
    return 0 if value < cuts[0] else 1 if value < cuts[1] else 2


def _winsor_mean(values, q=0.01):
    ordered = sorted(values)
    lo, hi = ordered[int(q * (len(ordered) - 1))], ordered[int((1 - q) * (len(ordered) - 1))]
    return sum(min(max(v, lo), hi) for v in ordered) / len(ordered)


class BucketModel:
    version = "FC-BASE-v1"
    use_sector = False

    def _keys(self, get):
        momentum = _bucket(get("r20"), self.cuts["r20"])
        rvol = get("rvol")
        rvol_bucket = None if rvol is None or isnan(rvol) else 0 if rvol < 1.0 else 1 if rvol < 1.5 else 2
        fine = ("fine", momentum, rvol_bucket, get("ema_stack"))
        if self.use_sector:
            fine = fine + (_bucket(get("sector_breadth5"), self.cuts["sector_breadth5"]),)
        return (fine, ("coarse", momentum), ("global",))

    def fit(self, ds, rows=None, cutoff=None):
        rows = range(ds.n) if rows is None else rows
        f = ds.f
        self.cuts = {"r20": _terciles(f["r20"][i] for i in rows),
                     "sector_breadth5": _terciles(f["sector_breadth5"][i] for i in rows)}
        cells = {}
        for i in rows:
            keys = self._keys(lambda name: f[name][i])
            for h in FORECAST_HORIZONS:
                if not ds.valid(h, i, cutoff):
                    continue
                ret, mae = ds.ret[h][i], ds.mae[h][i]
                for key in keys:
                    cell = cells.get((key, h))
                    if cell is None:
                        cell = cells[(key, h)] = (array("d"), array("d"))
                    cell[0].append(ret)
                    cell[1].append(mae)
        self.stats = {}
        for key, (rets, maes) in cells.items():
            hits = {k: sum(1 for r in rets if r >= t) for t, k in zip(THRESHOLDS, THRESHOLD_KEYS)}
            self.stats[key] = {"n": len(rets), "hits": hits, "mean_ret": _winsor_mean(rets),
                               "mean_mae": _winsor_mean(maes)}
        self.train_rows = len(rows)
        return self

    def predict(self, features):
        out = {}
        for h in FORECAST_HORIZONS:
            cell, key = None, None
            for candidate in self._keys(lambda name: features.get(name)):
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

    def _vector(self, get):
        out = [1.0]
        for k in FEATURES:
            value = get(k)
            if value is None or isnan(value):
                value = self.means[k]
            out.append((value - self.means[k]) / self.stds[k])
        return out

    def fit(self, ds, rows=None, cutoff=None):
        rows = range(ds.n) if rows is None else rows
        f = ds.f
        self.means, self.stds = {}, {}
        for k in FEATURES:
            column = f[k]
            values = [column[i] for i in rows if not isnan(column[i])]
            mean = sum(values) / len(values) if values else 0.0
            var = sum((v - mean) ** 2 for v in values) / len(values) if values else 1.0
            self.means[k], self.stds[k] = mean, (var ** 0.5) or 1.0
        self.coef, self.residuals, self.events = {}, {}, {}
        p = len(FEATURES) + 1
        for h in FORECAST_HORIZONS:
            xtx = [[0.0] * p for _ in range(p)]
            xty = [0.0] * p
            used = array("i")
            for i in rows:
                if not ds.valid(h, i, cutoff):
                    continue
                x = self._vector(lambda name: f[name][i])
                y = max(min(ds.ret[h][i], 1.0), -0.6)  # clip extreme labels for stability
                used.append(i)
                for a in range(p):
                    xa = x[a]
                    xty[a] += xa * y
                    row_a = xtx[a]
                    for b in range(a, p):
                        row_a[b] += xa * x[b]
            if len(used) < MIN_BUCKET:
                continue
            for a in range(p):
                for b in range(a):
                    xtx[a][b] = xtx[b][a]
                if a:
                    xtx[a][a] += self.lam
            coef = _solve(xtx, xty)
            self.coef[h] = coef
            self.events[h] = {k: sum(1 for i in used if ds.ret[h][i] >= t) for t, k in zip(THRESHOLDS, THRESHOLD_KEYS)}
            self.residuals[h] = array("d", sorted(
                ds.ret[h][i] - sum(c * v for c, v in zip(coef, self._vector(lambda name: f[name][i]))) for i in used))
        self.train_rows = len(rows)
        return self

    def predict(self, features):
        out = {}
        x = self._vector(lambda name: features.get(name))
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
                events = self.events[h][key]
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
