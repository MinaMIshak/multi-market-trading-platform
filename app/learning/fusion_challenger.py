"""Opt-in DF-CFG-2 research only. Never registered with live arm selection."""
from math import isfinite

from app.learning import fusion
from app.learning.walkforward import spearman


def configuration(market):
    base = fusion.MARKETS[market]
    prefix = "US-" if market == "US" else ""
    return {**base, "config": prefix + "DF-CFG-2",
            "arms": {prefix + "DF0": ("technical",),
                     prefix + "DF-Q2": ("technical", "quant")},
            "components": tuple(c for c in base["components"] if c != "fundamentals"),
            "weights": {c: w for c, w in base["weights"].items() if c != "fundamentals"},
            "learned_arm": None}


def increasing_means(cells):
    """Weighted pool-adjacent-violators fit; missing bins fail closed."""
    if any(n == 0 for _, n in cells):
        return None
    blocks = []
    for total, count in cells:
        blocks.append([total, count, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[i] + right[i] for i in range(3)])
    return [total / count for total, count, width in blocks for _ in range(width)]


class DirectionalQuantEdge:
    """Positive signed-factor IC gate and increasing train-fitted quintile table.

    Risk features retain negative signs: holding other factors fixed, higher
    volatility cannot improve this component. Negative gates disable a group;
    they do not reverse a risk preference. Every active feature is mandatory.
    """

    def __init__(self, groups):
        self.groups = groups

    def _percentiles(self, ds, members, group):
        columns = [fusion.percentiles([sign * ds.f[name][i] if isfinite(ds.f[name][i]) else None
                                       for i in members]) for name, sign in self.groups[group]]
        return [sum(values) / len(values) if all(v is not None for v in values) else None
                for values in zip(*columns)]

    def fit(self, ds, by_session, sessions_idx, cutoff):
        sums = {g: [[0.0, 0] for _ in range(5)] for g in self.groups}
        ics = {g: [] for g in self.groups}
        for k in sorted(set(sessions_idx)):
            if k >= cutoff:
                continue
            members = [i for i in by_session.get(k, [])
                       if ds.row_session[i] < cutoff and ds.valid(3, i, cutoff) and isfinite(ds.ret[3][i])]
            if len(members) < 20:
                continue
            for g in self.groups:
                pairs = [(p, ds.ret[3][i]) for p, i in zip(self._percentiles(ds, members, g), members)
                         if p is not None]
                if len(pairs) < 20:
                    continue
                rho = spearman([p for p, _ in pairs], [r for _, r in pairs])
                if rho is None or not isfinite(rho):
                    continue
                ics[g].append(rho)
                for p, r in pairs:
                    cell = sums[g][fusion._bin(p)]
                    cell[0] += r
                    cell[1] += 1
        self.ic = {g: sum(v) / len(v) if v else None for g, v in ics.items()}
        self.edges = {g: increasing_means(sums[g]) if self.ic[g] is not None and self.ic[g] > 0 else None
                      for g in self.groups}
        self.counts = {g: [n for _, n in cells] for g, cells in sums.items()}
        return self

    def scores(self, ds, members):
        active = {g: means for g, means in self.edges.items() if means is not None}
        if not active:
            return [None] * len(members)
        columns = {g: self._percentiles(ds, members, g) for g in active}
        totals = [sum(active[g][fusion._bin(columns[g][k])] for g in active)
                  if all(columns[g][k] is not None for g in active) else None for k in range(len(members))]
        return fusion.percentiles(totals)
