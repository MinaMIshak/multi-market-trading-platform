"""Significance layer (SIG-v1): HAC, block bootstrap, paired and early-vs-recent tests, and report wiring."""
import json
import random
from math import nan, sqrt

import pytest

from app.learning import daily, significance as sig, walkforward
from app.learning.models import CHAMPION, THRESHOLD_KEYS


def noise(n, mean=0.0, sd=0.05, seed=3):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_student_t_reference_values():
    assert sig.t_two_sided_p(2.0, 10) == pytest.approx(0.07339, abs=1e-4)
    assert sig.t_two_sided_p(0.0, 5) == pytest.approx(1.0)
    assert sig.t_quantile(0.975, 10) == pytest.approx(2.2281, abs=1e-3)
    assert sig.t_quantile(0.975, 10_000) == pytest.approx(1.960, abs=2e-3)


def test_calculations_are_deterministic_and_seeded():
    values = noise(60, mean=0.01)
    first, second = sig.mean_test(values, horizon=3), sig.mean_test(values, horizon=3)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    other = sig.mean_test(values, horizon=3, seed=1)
    assert other["newey_west"] == first["newey_west"] and other["block_bootstrap"]["seed"] == 1
    assert first["block_bootstrap"]["replications"] == sig.REPLICATIONS


def test_clear_signal_is_distinguishable_and_pure_noise_is_not():
    strong = sig.mean_test(noise(80, mean=0.03, sd=0.02), horizon=1)
    assert strong["status"] == "OK" and strong["distinguishable"] and strong["methods_agree"]
    lo, hi = strong["newey_west"]["ci"]
    assert lo > 0 and lo < strong["estimate"] < hi
    weak = sig.mean_test([v - sum(noise(80)) / 80 for v in noise(80)], horizon=1)   # exactly mean-zero noise
    assert weak["estimate"] == pytest.approx(0, abs=1e-12) and not weak["distinguishable"]
    assert weak["newey_west"]["p_value"] > 0.9 and weak["block_bootstrap"]["p_value"] > 0.5


def test_insufficient_sample_is_reported_not_tested():
    out = sig.mean_test([0.01] * (sig.MIN_OBSERVATIONS - 1) + [None, nan])
    assert out["status"] == "INSUFFICIENT_SAMPLE" and out["n"] == sig.MIN_OBSERVATIONS - 1 and out["n_missing"] == 2
    assert out["newey_west"] is None and out["distinguishable"] is False
    assert sig.mean_test([])["estimate"] is None
    halves = sig.early_vs_recent(noise(2 * sig.MIN_OBSERVATIONS - 1))
    assert halves["status"] == "INSUFFICIENT_SAMPLE" and halves["n_early"] == sig.MIN_OBSERVATIONS - 1


def test_zero_and_constant_series_are_degenerate_never_significant():
    zero = sig.mean_test([0.0] * 30)
    assert zero["status"] == "DEGENERATE_ZERO_VARIANCE" and zero["estimate"] == 0 and not zero["distinguishable"]
    constant = sig.mean_test([0.02] * 30)
    assert constant["status"] == "DEGENERATE_ZERO_VARIANCE" and constant["estimate"] == pytest.approx(0.02)
    assert constant["distinguishable"] is False
    assert sig.early_vs_recent([0.01] * 30)["status"] == "DEGENERATE_ZERO_VARIANCE"


def test_missing_observations_are_dropped_in_order_and_counted():
    values = noise(40, mean=0.01)
    holed = list(values)
    for k in (3, 10, 25):
        holed[k] = None
    holed[30] = nan
    out = sig.mean_test(holed)
    kept = [v for k, v in enumerate(values) if k not in (3, 10, 25, 30)]
    assert out["n"] == 36 and out["n_missing"] == 4
    assert out["estimate"] == pytest.approx(sum(kept) / len(kept), abs=1e-6)
    assert out == sig.mean_test(kept) | {"n_missing": 4}


def test_overlapping_observations_widen_the_hac_interval():
    base = noise(203, sd=0.02, seed=11)
    overlapping = [base[t] + base[t + 1] + base[t + 2] + 0.004 for t in range(200)]  # 3-session overlapping sums
    out = sig.mean_test(overlapping, horizon=3)
    assert out["newey_west"]["lag"] >= 2 and out["block_bootstrap"]["block_length"] >= 3
    m = sum(overlapping) / len(overlapping)
    iid_se = sqrt(sum((v - m) ** 2 for v in overlapping) / (len(overlapping) - 1) / len(overlapping))
    assert out["newey_west"]["se"] > 1.3 * iid_se          # positive autocorrelation is not ignored
    assert sig.hac_lag(17, horizon=3) == 2 and sig.hac_lag(5, horizon=10) == 4
    assert sig.block_length(17, horizon=3) == 3 and sig.block_length(4, horizon=5) == 2


def test_paired_difference_uses_common_sessions_only():
    champion = {f"d{k:02d}": 0.01 * ((k * 7) % 5) for k in range(30)}
    challenger = {s: v + 0.02 + 0.001 * ((k * 3) % 4) for k, (s, v) in enumerate(champion.items()) if k % 6}
    challenger["extra"] = 9.0                                # not in champion: ignored
    out = sig.paired(challenger, champion)
    assert out["common_sessions"] == 25 and out["n"] == 25
    assert out["estimate"] == pytest.approx(0.0215, abs=0.001) and out["distinguishable"]
    same = sig.paired(champion, champion)
    assert same["status"] == "DEGENERATE_ZERO_VARIANCE" and not same["distinguishable"]


def test_early_vs_recent_detects_a_break_and_ignores_stable_series():
    broken = noise(40, mean=0.03, sd=0.01, seed=5)[:20] + noise(40, mean=-0.03, sd=0.01, seed=6)[:20]
    out = sig.early_vs_recent(broken, horizon=3)
    assert out["n_early"] == 20 and out["n_recent"] == 20 and out["estimate"] < -0.05 and out["distinguishable"]
    assert out["early_mean"] > 0 > out["recent_mean"]
    stable = sig.early_vs_recent(noise(40, sd=0.01, seed=8), horizon=3)
    assert stable["status"] == "OK" and not stable["distinguishable"]
    odd = sig.early_vs_recent(noise(17))
    assert (odd["n_early"], odd["n_recent"]) == (8, 9)


def _predictions(n_sessions, *, skill):
    rng = random.Random(9)
    items = []
    for d in range(n_sessions):
        for k in range(30):
            ret = rng.gauss(0, 0.03)
            pred = skill * ret + rng.gauss(0, 0.03)
            items.append({"session": f"2026-01-{d + 1:02d}", "ticker": f"T{k}", "features": {"turnover20": 1e9},
                          "labels": {h: {"status": "VALID", "ret": ret} for h in (1, 2, 3)},
                          "forecast": {h: {"status": "OK", "expected_return": pred,
                                           "probabilities": {t: {"p": 0.2} for t in THRESHOLD_KEYS}} for h in (1, 2, 3)}})
    return items


def test_evaluation_reports_significance_and_challenger_comparison():
    series = {"A": {}, CHAMPION: {}}
    skilled = walkforward.evaluate_predictions(_predictions(25, skill=1.0), liquidity_floor=0, series=series["A"])
    random_ = walkforward.evaluate_predictions(_predictions(25, skill=0.0), liquidity_floor=0, series=series[CHAMPION])
    ic = skilled[3]["significance"]["rank_ic"]["mean"]
    assert ic["n"] == 25 and ic["horizon"] == 3 and ic["distinguishable"] and ic["estimate"] > 0.3
    assert ic["estimate"] == pytest.approx(skilled[3]["spearman_mean"], abs=1e-4)
    assert not random_[3]["significance"]["rank_ic"]["mean"]["distinguishable"]
    assert "early_vs_recent" in skilled[3]["significance"]["top10_excess"]
    comparison = walkforward.compare_to_champion(series)
    assert set(comparison) == {"A"} and comparison["A"]["3"]["rank_ic"]["distinguishable"]
    small = walkforward.evaluate_predictions(_predictions(5, skill=1.0), liquidity_floor=0)
    assert small[3]["significance"]["rank_ic"]["mean"]["status"] == "INSUFFICIENT_SAMPLE"


def test_live_fusion_scoring_adds_tests_without_changing_existing_fields(tmp_path):
    rng = random.Random(4)
    rows, sessions = [], [f"2026-02-{d + 1:02d}" for d in range(12)]
    for session in sessions:
        scores = {}
        for k in range(30):
            ret = rng.gauss(0, 0.03)
            rows.append({"session": session, "ticker": f"T{k}", "labels": {1: {"status": "VALID", "ret": ret},
                                                                          3: {"status": "VALID", "ret": ret}}})
            scores[f"T{k}"] = {"liquid": True, "technical_score": 50,
                               "opportunity_by_arm": {"DF0": 50 + 100 * ret, "DF1": rng.random() * 100}}
        path = tmp_path / "fusion" / f"{session}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"session": session, "scores": scores}))
    out = daily.fusion_scoring(tmp_path, {"rows": rows}, market="EGX")
    assert out["arms"]["DF0"]["matured_sessions"] == 12 and out["arms"]["DF0"]["status"] == "INSUFFICIENT_SAMPLE"
    df0 = out["significance"]["DF0"]["3"]["rank_ic"]["mean"]
    assert df0["n"] == 12 and df0["estimate"] == pytest.approx(1.0) and df0["status"] == "DEGENERATE_ZERO_VARIANCE"
    versus = out["significance"]["DF1"]["1"]["vs_technical"]["rank_ic"]
    assert versus["common_sessions"] == 12 and versus["estimate"] < -0.5 and versus["distinguishable"]
    assert out["technical_arm"] == "DF0" and out["significance_method"] == sig.METHOD
