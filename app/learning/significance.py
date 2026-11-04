"""Significance testing for learning evaluation metrics (evidence for review; never a promotion rule by itself).

The inputs are session-level metric series in chronological order, one value per session (rank IC, Top-N excess
return over the universe, challenger minus champion). They are autocorrelated, and h-session forward returns
overlap for h > 1. A naive iid t-test overstates significance, so two methods are reported side by side:

- Newey-West HAC: the mean with a Bartlett-kernel long-run variance. The lag is
  ``max(h - 1, floor(4 * (n / 100) ** (2 / 9)))``, capped at ``n - 1``: at least the label overlap, otherwise the
  Newey-West (1994) plug-in rule. The p-value and CI use Student t with ``n - 1`` degrees of freedom, which is
  conservative in small samples.
- Circular moving-block bootstrap of the mean. The block length is ``max(h, ceil(n ** (1 / 3)))``, capped at
  ``n // 2``, so a block always spans the label overlap. The generator is seeded, so results are reproducible.
  The CI is the percentile interval. The two-sided p-value comes from the bootstrap distribution centred on the
  estimate (H0: mean = 0).

A result is ``distinguishable`` only if *both* methods reject at ``alpha``. Missing observations (None or NaN) are
dropped and counted, and the remaining values keep their chronological order. Fewer than ``MIN_OBSERVATIONS``
values give INSUFFICIENT_SAMPLE. A zero-variance series gives DEGENERATE_ZERO_VARIANCE and is never called
significant.

Comparisons:
- ``paired``: challenger minus champion on the sessions where both exist (a one-sample test of the difference);
- ``early_vs_recent``: the chronological halves (the earlier half gets ``n // 2`` values). Estimate = recent mean
  minus early mean. HAC: the halves are treated as independent, se = sqrt(se1² + se2²), with df = min(n1, n2) - 1.
  Bootstrap: independent block resampling within each half.
"""
from __future__ import annotations

import random
from math import ceil, exp, floor, isnan, lgamma, log, sqrt

METHOD = "SIG-v1"
ALPHA = 0.05
MIN_OBSERVATIONS = 8
REPLICATIONS = 2000
SEED = 20261104
NOTE = "statistical significance is evidence for review, not sufficient for promotion"


def _clean(values):
    present = [float(v) for v in values if v is not None and not isnan(float(v))]
    return present, len(values) - len(present)


def _mean(values):
    return sum(values) / len(values)


def hac_lag(n, horizon=1):
    return max(0, min(n - 1, max(horizon - 1, floor(4 * (n / 100) ** (2 / 9)))))


def block_length(n, horizon=1):
    return max(1, min(n // 2, max(horizon, ceil(n ** (1 / 3)))))


def newey_west_variance(values, lag):
    """Long-run variance of the mean (Bartlett weights): (γ0 + 2 Σ w_j γ_j) / n."""
    n = len(values)
    m = _mean(values)
    d = [v - m for v in values]
    long_run = sum(x * x for x in d) / n
    for j in range(1, lag + 1):
        gamma = sum(d[t] * d[t - j] for t in range(j, n)) / n
        long_run += 2 * (1 - j / (lag + 1)) * gamma
    return max(long_run, 0.0) / n


# --- Student t distribution (pure Python: regularised incomplete beta, Numerical Recipes continued fraction) ---------

def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t, df):
    return _betai(df / 2, 0.5, df / (df + t * t))


def t_quantile(q, df):
    """Upper quantile: P(T <= x) = q for 0.5 < q < 1 (bisection on the CDF)."""
    lo, hi = 0.0, 1e3
    target = 2 * (1 - q)
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- methods -------------------------------------------------------------------------------------------------------

def _resample_mean(values, block, rng):
    n = len(values)
    total, count = 0.0, 0
    while count < n:
        start = rng.randrange(n)
        for j in range(min(block, n - count)):
            total += values[(start + j) % n]
            count += 1
    return total / n


def _percentile(ordered, q):
    position = q * (len(ordered) - 1)
    low = floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap(parts, block_lengths, estimate, *, replications, seed, alpha):
    rng = random.Random(seed)
    draws = []
    for _ in range(replications):
        means = [_resample_mean(p, b, rng) for p, b in zip(parts, block_lengths)]
        draws.append(means[0] if len(means) == 1 else means[1] - means[0])
    exceed = sum(1 for d in draws if abs(d - estimate) >= abs(estimate))
    draws.sort()
    return {"p_value": round((1 + exceed) / (replications + 1), 4),
            "ci": [round(_percentile(draws, alpha / 2), 6), round(_percentile(draws, 1 - alpha / 2), 6)]}


def _result(metric, estimate, n, missing, status, horizon, hac=None, boot=None, alpha=ALPHA, extra=None):
    out = {"metric": metric, "method": METHOD, "status": status, "n": n, "n_missing": missing, "horizon": horizon,
           "alpha": alpha, "estimate": None if estimate is None else round(estimate, 6),
           "primary_method": "newey_west_hac", "newey_west": hac, "block_bootstrap": boot,
           "distinguishable": False, "methods_agree": None, "note": NOTE}
    if extra:
        out.update(extra)
    if status == "OK":
        reject = [hac["p_value"] < alpha, boot["p_value"] < alpha]
        out["distinguishable"] = all(reject)
        out["methods_agree"] = reject[0] == reject[1]
    return out


def mean_test(values, *, metric="mean", horizon=1, alpha=ALPHA, replications=REPLICATIONS, seed=SEED,
              min_observations=MIN_OBSERVATIONS):
    """H0: the series mean is 0 (rank IC, excess return, or a paired difference)."""
    present, missing = _clean(values)
    n = len(present)
    if n < min_observations:
        return _result(metric, _mean(present) if present else None, n, missing, "INSUFFICIENT_SAMPLE", horizon,
                       alpha=alpha, extra={"min_observations": min_observations})
    estimate = _mean(present)
    lag, block = hac_lag(n, horizon), block_length(n, horizon)
    se = sqrt(newey_west_variance(present, lag))
    if se == 0:
        return _result(metric, estimate, n, missing, "DEGENERATE_ZERO_VARIANCE", horizon, alpha=alpha,
                       hac={"lag": lag, "se": 0.0}, boot={"block_length": block})
    df = n - 1
    t = estimate / se
    half = t_quantile(1 - alpha / 2, df) * se
    hac = {"lag": lag, "lag_rule": "max(h-1, floor(4(n/100)^(2/9)))", "se": round(se, 6), "t": round(t, 3),
           "df": df, "p_value": round(t_two_sided_p(t, df), 4), "ci": [round(estimate - half, 6), round(estimate + half, 6)]}
    boot = {"block_length": block, "block_rule": "max(h, ceil(n^(1/3))), circular moving blocks",
            "replications": replications, "seed": seed,
            **_bootstrap([present], [block], estimate, replications=replications, seed=seed, alpha=alpha)}
    return _result(metric, estimate, n, missing, "OK", horizon, hac, boot, alpha)


def paired(challenger, champion, *, metric="challenger_minus_champion", horizon=1, **kwargs):
    """challenger, champion: {session: value}. Tests the per-session difference on the sessions both have."""
    sessions = sorted(s for s in set(challenger) & set(champion))
    diffs = [None if challenger[s] is None or champion[s] is None else challenger[s] - champion[s] for s in sessions]
    out = mean_test(diffs, metric=metric, horizon=horizon, **kwargs)
    out["common_sessions"] = len(sessions)
    return out


def early_vs_recent(values, *, metric="recent_minus_early", horizon=1, alpha=ALPHA, replications=REPLICATIONS,
                    seed=SEED, min_observations=MIN_OBSERVATIONS):
    """Chronological halves; estimate = mean(recent) - mean(early)."""
    present, missing = _clean(values)
    split = len(present) // 2
    early, recent = present[:split], present[split:]
    extra = {"n_early": len(early), "n_recent": len(recent),
             "early_mean": round(_mean(early), 6) if early else None,
             "recent_mean": round(_mean(recent), 6) if recent else None}
    if min(len(early), len(recent)) < min_observations:
        estimate = _mean(recent) - _mean(early) if early and recent else None
        return _result(metric, estimate, len(present), missing, "INSUFFICIENT_SAMPLE", horizon, alpha=alpha,
                       extra={**extra, "min_observations_per_half": min_observations})
    estimate = _mean(recent) - _mean(early)
    lags = [hac_lag(len(p), horizon) for p in (early, recent)]
    blocks = [block_length(len(p), horizon) for p in (early, recent)]
    se = sqrt(newey_west_variance(early, lags[0]) + newey_west_variance(recent, lags[1]))
    if se == 0:
        return _result(metric, estimate, len(present), missing, "DEGENERATE_ZERO_VARIANCE", horizon, alpha=alpha,
                       hac={"lag": lags, "se": 0.0}, boot={"block_length": blocks}, extra=extra)
    df = min(len(early), len(recent)) - 1
    t = estimate / se
    half = t_quantile(1 - alpha / 2, df) * se
    hac = {"lag": lags, "se": round(se, 6), "t": round(t, 3), "df": df, "p_value": round(t_two_sided_p(t, df), 4),
           "ci": [round(estimate - half, 6), round(estimate + half, 6)], "assumption": "halves independent"}
    boot = {"block_length": blocks, "replications": replications, "seed": seed,
            **_bootstrap([early, recent], blocks, estimate, replications=replications, seed=seed, alpha=alpha)}
    return _result(metric, estimate, len(present), missing, "OK", horizon, hac, boot, alpha, extra)


def summary(series_by_session, *, metric, horizon, **kwargs):
    """Mean test plus early-vs-recent for one {session: value} series (sessions sorted chronologically)."""
    values = [series_by_session[s] for s in sorted(series_by_session)]
    return {"mean": mean_test(values, metric=metric, horizon=horizon, **kwargs),
            "early_vs_recent": early_vs_recent(values, metric=f"{metric}:recent_minus_early", horizon=horizon, **kwargs)}


def compact(result):
    """One-line view for UIs and printed summaries."""
    if result.get("status") != "OK":
        return f'{result.get("metric")}: {result.get("status")} (n={result.get("n")})'
    hac, boot = result["newey_west"], result["block_bootstrap"]
    return (f'{result["metric"]}: {result["estimate"]:+.4f} n={result["n"]} HAC p={hac["p_value"]} '
            f'CI[{hac["ci"][0]:+.4f},{hac["ci"][1]:+.4f}] boot p={boot["p_value"]} '
            f'{"DISTINGUISHABLE" if result["distinguishable"] else "not distinguishable"}')
