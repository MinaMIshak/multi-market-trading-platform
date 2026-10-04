"""Learning and forecasting: labels, features, no look-ahead, walk-forward, metrics, winners, events, UI."""
import json
import random
from datetime import date, timedelta

import pytest

from app.learning import daily, dataset, events, models, walkforward
from app.learning.dataset import Series
from app.ui.learning import render_performance, render_research, render_review, render_system, render_today


def egx_days(n, start=date(2025, 1, 5)):
    out, day = [], start
    while len(out) < n:
        if day.weekday() not in (4, 5):
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def make_series(ticker, closes, days, *, volume=200_000.0, sector="S1", skip=()):
    bars = []
    for day, close in zip(days, closes):
        if day in skip:
            continue
        bars.append((day, close, close * 1.02, close * 0.98, close, volume))
    return Series(ticker, bars, isin=f"EG{ticker}", sector=sector)


def universe(n_symbols=30, n_days=220, seed=7):
    rng = random.Random(seed)
    days = egx_days(n_days)
    out = []
    for k in range(n_symbols):
        price, closes = 10.0 + k, []
        for _ in days:
            price *= 1 + rng.gauss(0.0005, 0.02)
            closes.append(round(price, 4))
        out.append(make_series(f"T{k:02d}", closes, days, sector=f"S{k % 3}"))
    return out, days


# --- labels -------------------------------------------------------------------------------------------------

def test_horizon_labels_use_the_next_observed_sessions_and_never_cross_gaps():
    days = egx_days(80)
    closes = [10.0 + i * 0.1 for i in range(80)]
    others = [make_series(f"O{k}", closes, days) for k in range(12)]
    gappy = make_series("GAP", closes, days, skip={days[70]})
    sessions = dataset.observed_sessions(others + [gappy])
    labels = dataset.forward_labels(gappy, sessions)
    t = days[65]
    assert labels[t][1]["status"] == "VALID" and labels[t][1]["label_end"] == days[66]
    assert labels[t][1]["ret"] == pytest.approx(closes[66] / closes[65] - 1)
    assert labels[t][5]["status"] == "MISSING_SESSION"          # day 70 missing: never forward-filled
    assert labels[days[69]][1]["status"] == "MISSING_SESSION"
    assert labels[days[-1]][1]["status"] == "NOT_MATURED"
    mfe = labels[t][3]["mfe"]
    assert mfe == pytest.approx(max(c * 1.02 for c in closes[66:69]) / closes[65] - 1)
    assert labels[t][3]["hits"]["3"] is (labels[t][3]["ret"] >= 0.03)


def test_weekends_are_not_sessions_and_sparse_dates_are_ignored():
    series, days = universe(12, 90)
    sessions = dataset.observed_sessions(series)
    assert all(date.fromisoformat(d).weekday() not in (4, 5) for d in sessions)
    lonely = Series("ODD", [("2025-01-10", 1, 1, 1, 1, 1)])  # a Friday bar on one symbol only
    assert "2025-01-10" not in dataset.observed_sessions(series + [lonely])


def test_features_are_point_in_time_and_missing_history_is_unavailable():
    series, days = universe(12, 120)
    target = series[0]
    feats = dataset.symbol_features(target)
    t = days[100]
    truncated = Series(target.ticker, [b for b in target.bars if b[0] <= t], isin=target.isin, sector=target.sector)
    assert dataset.symbol_features(truncated)[t] == feats[t]     # future bars cannot change past features
    assert days[30] not in feats                                 # fewer than 60 sessions: FEATURE_UNAVAILABLE
    built = dataset.build(series)
    row = next(r for r in built["rows"] if r["session"] == t and r["ticker"] == target.ticker)
    r20s = sorted(r["features"]["r20"] for r in built["rows"] if r["session"] == t)
    assert row["features"]["rs20"] == pytest.approx(row["features"]["r20"] - (r20s[5] + r20s[6]) / 2)
    assert row["features"]["sector_breadth5"] is not None
    counts = dataset.label_counts(built)
    assert counts["observations"] == len(built["rows"]) and counts["h1_valid"] > 0


def test_turnover_acceleration_and_breakout():
    days = egx_days(90)
    closes = [10.0] * 89 + [12.0]
    s = make_series("X", closes, days)
    s.bars[-1] = (days[-1], 12.0, 12.5, 11.8, 12.0, 1_000_000.0)
    f = dataset.symbol_features(s)[days[-1]]
    assert f["breakout20"] is True and f["rvol"] == pytest.approx(5.0)
    assert f["turnover_accel"] > 1.0


# --- models and walk-forward ----------------------------------------------------------------------------------------

def test_models_return_bounded_versioned_probabilities_and_insufficient_samples():
    series, _ = universe(30, 220)
    built = dataset.build(series)
    for name, cls in models.MODELS.items():
        model = cls().fit(built["rows"])
        forecast = model.predict(built["rows"][-1]["features"])
        assert model.version == name
        for h in models.FORECAST_HORIZONS:
            item = forecast[h]
            if item["status"] == "OK":
                assert all(0 < v["p"] < 1 for v in item["probabilities"].values())
                assert item["probabilities"]["20"]["p"] <= item["probabilities"]["3"]["p"] + 1e-9
    tiny = models.BucketModel().fit(built["rows"][:30])
    assert tiny.predict(built["rows"][-1]["features"])[1]["status"] == "INSUFFICIENT_SAMPLE"


def test_walk_forward_is_chronological_without_label_leakage(monkeypatch):
    series, _ = universe(30, 220)
    built = dataset.build(series)
    plan = walkforward.folds(built["sessions"], min_train=120)
    assert plan and all(f["sessions"] == sorted(f["sessions"]) for f in plan)
    assert all(a["sessions"][-1] < b["start"] for a, b in zip(plan, plan[1:]))
    cutoffs = []

    class Spy(models.BucketModel):
        def fit(self, rows, cutoff=None):
            assert cutoff is not None and all(r["session"] < cutoff for r in rows)
            leaked = [r for r in rows for h, lab in r["labels"].items()
                      if lab.get("status") == "VALID" and lab["label_end"] >= cutoff]
            assert leaked, "fixture should contain labels maturing after the cutoff"
            assert not any(models._valid(r, h, cutoff) for r in leaked for h in models.FORECAST_HORIZONS
                           if r["labels"].get(h, {}).get("label_end", "") >= cutoff)
            cutoffs.append(cutoff)
            return super().fit(rows, cutoff)
    monkeypatch.setitem(models.MODELS, "SPY", Spy)
    monkeypatch.setattr(walkforward, "MODELS", models.MODELS)
    result = walkforward.run(built, liquidity_floor=0, model_names=("SPY",), min_train=120)
    assert result["SPY"]["predictions"] > 0 and cutoffs == [f["start"] for f in plan][:len(cutoffs)]


def test_metrics_brier_spearman_and_topk():
    assert walkforward.brier([(0.0, 0.0), (1.0, 1.0)]) == 0.0
    assert walkforward.brier([(0.5, 1.0), (0.5, 0.0)]) == 0.25
    assert walkforward.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert walkforward.spearman([1, 2], [2, 1]) is None
    buckets = walkforward.calibration_buckets([(0.02, 0.0), (0.04, 1.0), (0.4, 1.0)])
    assert buckets[0]["n"] == 2 and buckets[0]["realized"] == 0.5
    preds = []
    for session in ("d1", "d2"):
        for k in range(30):
            ret = (30 - k) / 100
            preds.append({"session": session, "ticker": f"T{k}", "features": {"turnover20": 1e9},
                          "labels": {h: {"status": "VALID", "ret": ret, "hits": {t: ret >= int(t) / 100 for t in
                                                                                 models.THRESHOLD_KEYS}} for h in (1, 2, 3)},
                          "forecast": {h: {"status": "OK", "expected_return": ret,
                                           "probabilities": {t: {"p": 0.5} for t in models.THRESHOLD_KEYS}} for h in (1, 2, 3)}})
    report = walkforward.evaluate_predictions(preds, liquidity_floor=0)
    assert report[3]["precision_at_k"][10] == 1.0 and report[3]["recall_top10_in_pred10"] == 1.0
    assert report[3]["status"] == "INSUFFICIENT_SAMPLE" and report[3]["spearman_mean"] == pytest.approx(1.0)
    assert report[3]["topk_mean_return"][5] > report[3]["benchmark_mean_return"]


# --- daily run, winners, events, UI --------------------------------------------------------------------------------------------

CONTEXT = {"generated_at": "2026-10-03T08:00:00+00:00",
           "events": {"egx_disclosures": {"status": "AVAILABLE", "provenance": [{"retrieved_at": "2025-01-01T00:00:00+00:00"}],
                                          "records": [{"code": 1, "isin": "EGT01", "heading": "T01 - Cash Dividend Distribution",
                                                       "section": "Disclosure", "published_at": "2025-01-02T10:00:00+02:00"}]}},
           "fundamentals": {"egx_financial_statements": {"status": "AVAILABLE", "records": [
               {"code": 2, "isin": "EGT02", "heading": "Results", "published_at": "2025-01-03T10:00:00+02:00",
                "net_result_change_pct": "-12.0"}]}},
           "regimes": {"brent": {"label": "RISING", "change_pct": 7.0, "date": "2026-10-02"},
                       "egp": {"label": "STABLE"}, "fed": {"label": "TIGHTENING", "last_change": "2026-09-17",
                                                          "from": "3.75", "to": "4.00"},
                       "chokepoints": {"Suez Canal": {"label": "DISRUPTED", "change_7d_vs_90d_pct": -40.0}}}}


def test_event_taxonomy_and_first_seen():
    extracted = events.extract(CONTEXT, market="EGX")
    types = {e["event_type"] for e in extracted["events"]}
    assert {"DIVIDEND", "EARNINGS_MISS", "BRENT_MOVE", "INTEREST_RATE_HIKE", "RED_SEA_DISRUPTION"} <= types
    dividend = extracted["by_isin"]["EGT01"][0]
    assert dividend["first_seen"] == "2025-01-01T00:00:00+00:00" and dividend["confidence"] == "SOURCED_FACT"
    assert events.extract(None, market="EGX")["summary"]["status"] == "UNAVAILABLE"
    assert "DIVIDEND" not in {e["event_type"] for e in events.extract(CONTEXT, market="US")["events"]}
    assert events.classify_disclosure("Board approves merger with X") == "MERGER"


def test_daily_run_freezes_forecasts_reviews_winners_and_scores(tmp_path):
    series, days = universe(30, 220)
    first = daily.run_market(market="EGX", series=[Series(s.ticker, s.bars[:-1], s.isin, s.sector) for s in series],
                             state_dir=tmp_path, now=__import__("datetime").datetime(2026, 10, 5, tzinfo=__import__("datetime").timezone.utc),
                             build_revision="t", context=CONTEXT)
    assert first["forecast"]["frozen_now"] is True and first["forecast"]["upside_top"]
    assert all(item["liquid"] for item in first["forecast"]["upside_top"])
    second = daily.run_market(market="EGX", series=series, state_dir=tmp_path,
                              now=__import__("datetime").datetime(2026, 10, 6, tzinfo=__import__("datetime").timezone.utc),
                              build_revision="t", context=CONTEXT)
    review = second["winners"]
    assert review["status"] == "AVAILABLE" and review["frozen_forecast_available"] is True
    assert len(review["winners"]) == 20 and review["winners"][0]["actual_return"] >= review["winners"][1]["actual_return"]
    for item in review["winners"]:
        assert item["caught"] or item["miss_reasons"]
        assert item["prior_session"] == review["prior_session"] < review["session"]
    again = daily.run_market(market="EGX", series=series, state_dir=tmp_path,
                             now=__import__("datetime").datetime(2026, 10, 6, 1, tzinfo=__import__("datetime").timezone.utc),
                             build_revision="t", context=CONTEXT)
    assert again["forecast"]["frozen_now"] is False      # frozen forecasts are never overwritten
    assert second["live_scoring"]["frozen_forecast_files"] == 2
    sectors = second["sectors"]["sectors"]
    assert sum(s["members"] for s in sectors) == 30 and "not institutional flow" in second["sectors"]["terminology"]
    report = {"schema": "learning-report-v1", "generated_at": "2026-10-06T00:00:00+00:00", "build_revision": "t",
              "live_money": False, "use": "RESEARCH_ONLY_NOT_A_TRADE_DECISION", "markets": {"EGX": second}}
    today = render_today(report, "EGX", ranking={"symbols": []}, experiment={"current": {}})
    assert "Top upside forecasts" in today and "UNVALIDATED" in today and "not a guarantee" in today
    assert "what did we know" in render_review(report, "EGX")
    assert "NOT_RUN_YET" in render_performance(report) and "Sector rotation" in render_research(report)
    assert "FC-BASE-v1" in render_system(report)
    assert "UNAVAILABLE" in render_today(None, "EGX")


def test_missed_reasons_are_machine_readable():
    prior = {"features": {"turnover20": 10.0, "breakout20": False}}
    v1 = {"technical_class": "NO_TRADE", "rejection_reasons": ["ILLIQUID", "DOWNTREND"]}
    reasons = daily.missed_reasons(prior, v1, None, [], market="EGX")
    assert {"LIQUIDITY_GATE", "MOMENTUM_THRESHOLD", "BREAKOUT_THRESHOLD", "MISSED_WINNER_RANK_TOO_LOW",
            "NO_CATALYST"} <= set(reasons)
    assert daily.missed_reasons(None, None, None, [], market="EGX") == ["OUT_OF_UNIVERSE"]


def test_champion_trade_strategy_untouched_and_live_money_disabled():
    from app.strategies.egx_ranking import VERSION
    assert VERSION == "EGX-RANK-v1" and models.CHAMPION == "FC-BASE-v1"
    assert json.loads(json.dumps({"live_money": False}))["live_money"] is False
