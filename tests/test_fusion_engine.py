"""Decision Fusion (EGX-DECISION-FUSION-v1, US-DECISION-FUSION-v1): scores, gates, confidence, isolation, validation."""
import json
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.learning import dataset, fusion, fusion_eval, fusion_live
from app.learning.dataset import Series
from app.strategies.egx_ranking import Bar, features as v1_features, score as v1_score
from app.us import benchmarks


def days(n, start=date(2025, 1, 6), weekdays=(0, 1, 2, 3, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() in weekdays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def universe(n_symbols=40, n_days=230, seed=3, weekdays=(0, 1, 2, 3, 4), sector_count=4):
    rng = random.Random(seed)
    dates = days(n_days, weekdays=weekdays)
    out = []
    for k in range(n_symbols):
        price, bars = 20.0 + k, []
        for d in dates:
            open_ = price * (1 + rng.gauss(0, 0.01))
            price = max(1.0, open_ * (1 + rng.gauss(0.0005, 0.02)))
            bars.append((d, open_, max(open_, price) * 1.01, min(open_, price) * 0.99, price,
                         float(rng.randint(400_000, 900_000))))
        out.append(Series(f"T{k:02d}", bars, isin=f"ISIN{k:02d}", sector=f"S{k % sector_count}"))
    return out


# --- technical parity and normalization -------------------------------------------------------------------------------

def test_technical_component_reproduces_egx_rank_v1_score():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "egx_cpci_2026-10-01.json").read_text())
    bars = [Bar(b["date"], *(Decimal(b[k]) for k in ("open", "high", "low", "close", "volume"))) for b in fixture["bars"]]
    expected = v1_score(v1_features(bars))["total"]
    series = Series("CPCI", [(b.date, float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume))
                             for b in bars])
    feats = dataset.symbol_features(series)[bars[-1].date]
    assert fusion.technical_score(lambda name: feats.get(name), "EGX") == expected == 82.18


def test_percentiles_are_bounded_tie_aware_and_keep_missing():
    out = fusion.percentiles([3.0, None, 1.0, 3.0, float("nan")])
    assert out[1] is None and out[4] is None and out[2] == 0.0 and out[0] == out[3] == 75.0
    assert all(0 <= v <= 100 for v in out if v is not None)
    assert fusion.percentiles([5.0]) == [50.0]


def test_missing_evidence_renormalizes_instead_of_scoring_zero():
    weights = fusion.arm_weights("DF4")
    full, share_full = fusion.opportunity({c: 80.0 for c in fusion.COMPONENTS}, weights)
    partial, share = fusion.opportunity({"technical": 80.0, "quant": 80.0, "sector": 80.0, "forecast": 80.0,
                                         "catalyst": None, "regime": None}, weights)
    assert full == partial == 80.0 and share_full == 1.0 and share == 0.85
    assert fusion.opportunity({c: None for c in fusion.COMPONENTS}, weights) == (None, 0.0)


# --- confidence, evidence quality, explanation ------------------------------------------------------------------------------

def test_high_score_can_have_low_or_insufficient_confidence():
    metrics_ok = {"status": "OK", "spearman_mean": 0.01}
    assert fusion.confidence(available_share=1.0, arm_metrics=metrics_ok, forecast_support=5000, disagreement=5,
                             stale=False) == "LOW"
    assert fusion.confidence(available_share=1.0, arm_metrics=None, forecast_support=5000, disagreement=5,
                             stale=False) == "INSUFFICIENT_SAMPLE"
    strong = {"status": "OK", "spearman_mean": 0.08}
    assert fusion.confidence(available_share=0.9, arm_metrics=strong, forecast_support=5000, disagreement=5,
                             stale=False) == "HIGH"
    assert fusion.confidence(available_share=0.9, arm_metrics=strong, forecast_support=5000, disagreement=60,
                             stale=False) == "MEDIUM"
    assert fusion.confidence(available_share=1.0, arm_metrics=strong, forecast_support=5000, disagreement=1,
                             stale=True) == "LOW"


def test_evidence_quality_and_explanation_never_invent_components():
    scores = {"technical": 90.0, "quant": None, "sector": 72.0, "forecast": 50.0, "catalyst": None, "regime": 10.0}
    quality = fusion.evidence_quality(scores, fresh=False, sector_known=True, forecast_status="OK",
                                      calibration_status="INSUFFICIENT_SAMPLE")
    assert quality["quant_edge"] == "INSUFFICIENT_SAMPLE" and quality["catalyst"] == "UNKNOWN"
    assert quality["price_freshness"] == "STALE"
    assert fusion.explain(scores) == {"technical": "+++", "sector": "++", "forecast": "0", "regime": "−"}


# --- hard gates outside the score ----------------------------------------------------------------------------------------------

def test_failed_gate_blocks_even_a_94_opportunity_score():
    assert fusion.final_action(opportunity_score=94, eligibility="BLOCKED", technical_class="STRONG_CANDIDATE",
                               gate_action=None) == "BLOCKED"
    assert fusion.final_action(opportunity_score=94, eligibility="UNKNOWN", technical_class="CANDIDATE",
                               gate_action=None) == "BLOCKED"
    assert fusion.final_action(opportunity_score=10, eligibility="ELIGIBLE", technical_class="CANDIDATE",
                               gate_action=None) == "PAPER_ENTRY"
    assert fusion.final_action(opportunity_score=99, eligibility="ELIGIBLE", technical_class="WATCHLIST",
                               gate_action=None) == "WATCH"


def test_us_gates_and_eligibility():
    features = {"turnover20": 50e6, "vol20": 0.02, "gap1": 0.01, "intraday1": 0.0}
    gates = fusion.us_gates(record={"risk_pct": "4.0"}, features=features, earnings="NO_NEAR_EARNINGS",
                            freshness="CURRENT")
    assert fusion.us_eligibility({k: v for k, v in gates.items() if k != "sector_concentration"})[0] == "ELIGIBLE"
    risky = fusion.us_gates(record={"risk_pct": "4.0"}, features={**features, "gap1": 0.07, "intraday1": 0.01},
                            earnings="EARNINGS_IN_0_1_DAYS", freshness="STALE")
    status, failed = fusion.us_eligibility(risky)
    assert status == "BLOCKED" and {"earnings_event_risk", "gap_chase", "stale_data"} <= set(failed)
    assert risky["spread"] == "NOT_MEASURABLE" and risky["portfolio_risk"] == "NOT_EVALUATED"


def test_us_sector_concentration_cap():
    symbols = {f"T{k}": {"ticker": f"T{k}", "sector": "Tech", "opportunity": 90 - k,
                         "gates": {"stale_data": "PASS", "min_liquidity": "PASS", "earnings_event_risk": "PASS",
                                   "volatility_limit": "PASS", "gap_chase": "PASS", "stop_feasibility": "PASS",
                                   "spread": "NOT_MEASURABLE", "portfolio_risk": "NOT_EVALUATED",
                                   "sector_concentration": "PENDING"}} for k in range(5)}
    records = {t: {"classification": "CANDIDATE"} for t in symbols}
    fusion_live._apply_us_actions(symbols, records)
    actions = [symbols[f"T{k}"]["final_action"] for k in range(5)]
    assert actions == ["PAPER_ENTRY"] * 3 + ["BLOCKED"] * 2
    assert symbols["T4"]["gate_failures"] == ["sector_concentration"]


# --- redundancy control and versioning -------------------------------------------------------------------------------------

def test_learned_weights_are_nonnegative_capped_and_share_correlated_signal():
    rng = random.Random(1)
    rows = []
    for _ in range(2000):
        signal = rng.uniform(0, 100)
        rows.append({"scores": {"technical": signal, "quant": signal + rng.gauss(0, 1), "sector": rng.uniform(0, 100),
                                "forecast": signal + rng.gauss(0, 1), "catalyst": None, "regime": rng.uniform(0, 100)},
                     "ret3": (signal - 50) / 1000 + rng.gauss(0, 0.01)})
    weights = fusion.learn_weights(rows)
    assert weights and all(0 < w <= fusion.DF5_CAP + 1e-9 for w in weights.values())
    assert abs(sum(weights.values()) - 1) < 1e-3 and "catalyst" not in weights
    assert fusion.learn_weights(rows[:100]) is None
    assert fusion.cap_weights({"a": 0.9, "b": 0.0, "c": 0.1, "d": 0.0}, 0.35) == {"a": 0.5, "c": 0.5}
    capped = fusion.cap_weights({"a": 0.7, "b": 0.2, "c": 0.05, "d": 0.05}, 0.35)
    assert max(capped.values()) <= 0.35 and abs(sum(capped.values()) - 1) < 1e-3


def test_configs_are_versioned_and_separate_per_market():
    assert (fusion.CONFIG, fusion.US_CONFIG) == ("DF-CFG-1", "US-DF-CFG-1")
    assert fusion.BASE_WEIGHTS == {"technical": 0.25, "quant": 0.25, "sector": 0.20, "forecast": 0.15,
                                   "catalyst": 0.08, "regime": 0.07}
    assert fusion.US_BASE_WEIGHTS["fundamentals"] == 0.20 and list(fusion.US_ARMS)[-1] == "US-DF6"
    assert fusion.arm_weights("US-DF2", market="US") == {"technical": 0.2, "quant": 0.2, "sector": 0.15}
    assert set(fusion.MARKETS["US"]["quant_groups"]) >= {"reversal", "gap"} and "gap" not in fusion.QUANT_GROUPS


# --- US earnings, gaps, benchmarks ---------------------------------------------------------------------------------------------

def stamp(day, hour=21):
    return datetime(*map(int, day.split("-")), hour, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("next_release,last_release,expected", [
    (stamp("2026-10-05"), None, "EARNINGS_IN_0_1_DAYS"),       # next NYSE session (Fri -> Mon)
    (stamp("2026-10-08"), None, "EARNINGS_IN_2_5_DAYS"),
    (stamp("2026-10-30"), None, "NO_NEAR_EARNINGS"),
    (stamp("2026-11-30"), stamp("2026-10-01"), "POST_EARNINGS_DAY_1"),
    (stamp("2026-11-30"), stamp("2026-09-29"), "POST_EARNINGS_DAY_2_3"),
    (None, None, "UNKNOWN"),
])
def test_earnings_states_follow_the_nyse_calendar(next_release, last_release, expected):
    assert fusion.earnings_state(session="2026-10-02", next_release=next_release, last_release=last_release) == expected


@pytest.mark.parametrize("gap,intraday,label", [(0.05, 0.01, "GAP_UP_CONTINUATION"), (0.05, -0.02, "GAP_UP_FADE"),
                                                (-0.04, 0.01, "GAP_DOWN_REVERSAL"),
                                                (-0.04, -0.01, "GAP_DOWN_CONTINUATION"), (0.005, 0.0, "NO_GAP"),
                                                (None, 0.0, "UNKNOWN")])
def test_gap_classes(gap, intraday, label):
    assert fusion.gap_class(gap, intraday) == label


def test_benchmark_forward_returns_are_session_aligned():
    sessions = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]
    closes = {"2026-10-01": 100.0, "2026-10-02": 101.0, "2026-10-05": 103.0, "2026-10-06": 104.0}
    out = benchmarks.forward_returns(sessions, closes, horizon=3)
    assert out == {0: pytest.approx(0.04)} and 1 not in out


def test_fundamentals_need_three_sourced_fields():
    records = [{"eps_surprise_pct": 10, "revenue_surprise_pct": 5, "revenue_growth_ttm": 20, "net_margin": 30, "roe": 15},
               {"eps_surprise_pct": -5, "revenue_surprise_pct": -2, "revenue_growth_ttm": 1, "net_margin": 5, "roe": 3},
               {"eps_surprise_pct": 2}]
    scores = fusion.fundamentals_scores(records)
    assert scores[0] > scores[1] and scores[2] is None


# --- walk-forward, isolation, no look-ahead ---------------------------------------------------------------------------------

def test_fusion_walk_forward_is_chronological_and_reports_incremental_value():
    ds = dataset.build(universe())
    result = fusion_eval.run(ds, market="US", min_train=120)
    assert result["market"] == "US" and set(result["results"]) == set(fusion.US_ARMS)
    assert result["evaluation_start"] > ds.sessions[119]
    assert set(result["incremental"]) == {f"{b} vs {a}" for a, b in zip(list(fusion.US_ARMS), list(fusion.US_ARMS)[1:])}
    all_metrics = result["results"]["US-DF0"]["all"]
    assert all_metrics["status"] in ("OK", "INSUFFICIENT_SAMPLE")
    assert "upper_turnover_tier" in result["results"]["US-DF0"]
    egx = fusion_eval.run(dataset.build(universe(weekdays=(6, 0, 1, 2, 3))), market="EGX", min_train=120)
    assert set(egx["results"]) == set(fusion.ARMS) and "upper_turnover_tier" not in egx["results"]["DF0"]


def test_quant_edge_ignores_labels_after_cutoff():
    ds = dataset.build(universe())
    by_session = ds.by_session()
    cutoff = 150
    seen = fusion.QuantEdge().fit(ds, by_session, [k for k in by_session if k < cutoff], cutoff)
    poisoned = dataset.build(universe())
    for i in range(poisoned.n):
        if poisoned.end[3][i] >= cutoff:
            poisoned.ret[3][i] = 9.9      # future outcomes changed: must not affect the fit
    again = fusion.QuantEdge().fit(poisoned, poisoned.by_session(), [k for k in by_session if k < cutoff], cutoff)
    assert seen.edges == again.edges and seen.ic == again.ic


def test_markets_are_isolated_datasets_and_state():
    egx = dataset.build(universe(seed=5, weekdays=(6, 0, 1, 2, 3)))
    us = dataset.build(universe(seed=6))
    assert not set(egx.sessions) & {d for d in us.sessions if date.fromisoformat(d).weekday() == 6}
    assert set(egx.tickers) == set(us.tickers)   # same synthetic names, yet separate objects and labels
    assert egx.ret[1] is not us.ret[1]
    assert fusion.MARKETS["EGX"]["weights"] is not fusion.MARKETS["US"]["weights"]


def test_session_metrics_topk():
    scores = list(range(30, 0, -1))
    r1 = [s / 100 for s in scores]
    m = fusion_eval._session_metrics(scores, r1, r1, [-0.01] * 30)
    assert m["recall_top10_in_top10"] == 1.0 and m["precision_at_5"] == 1.0 and m["spearman_r3"] == pytest.approx(1.0)


def test_live_money_stays_disabled():
    assert "LIVE" not in fusion.STRATEGY and fusion.final_action(
        opportunity_score=100, eligibility="BLOCKED", technical_class="STRONG_CANDIDATE", gate_action=None) != "PAPER_ENTRY"


def test_reported_arm_must_beat_technical_out_of_sample():
    def cache(results):
        return {"evaluation": {"results": {arm: {"all": {"status": "OK", **m}} for arm, m in results.items()}}}
    negative = cache({"DF0": {"spearman_mean": -0.03, "top10_mean_r3": 0.0138},
                      "DF5": {"spearman_mean": -0.02, "top10_mean_r3": 0.0098}})
    assert fusion_live.best_arm(negative)[0] == "DF0" and "NO_CHALLENGER" in fusion_live.best_arm(negative)[1]
    us = cache({"US-DF0": {"spearman_mean": -0.0001, "top10_mean_r3": 0.0053},
                "US-DF1": {"spearman_mean": 0.0039, "top10_mean_r3": 0.0070},
                "US-DF6": {"spearman_mean": -0.0017, "top10_mean_r3": 0.0124}})
    assert fusion_live.best_arm(us, "US")[0] == "US-DF1"     # US-DF6 has a higher return but a negative IC
    assert fusion_live.best_arm(None)[0] == "DF0"
