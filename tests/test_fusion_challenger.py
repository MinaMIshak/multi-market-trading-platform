"""Synthetic correctness fixtures only; these are not evidence of market edge."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.learning import fusion, fusion_eval, fusion_run
from app.learning.fusion_challenger import DirectionalQuantEdge, configuration, increasing_means
from test_fusion_engine import dataset, universe


def sample():
    # Positive rank IC with a deliberately inverted middle pair of quintiles.
    x = list(range(40))
    ds = SimpleNamespace(f={"x": x, "vol20": x, "atr_pct": x},
                         ret={3: [float([0, 3, 1, 4, 5][i // 8]) for i in x]},
                         row_session=[0] * 40, end={3: [3] * 40})
    ds.valid = lambda h, i, cutoff: ds.end[h][i] < cutoff
    return ds


def test_monotonic_fit_preserves_positive_gate_direction_and_legacy_table():
    ds = sample()
    groups = {"factor": (("x", 1),)}
    legacy = fusion.QuantEdge(groups).fit(ds, {0: list(range(40))}, [0], 4)
    fixed = DirectionalQuantEdge(groups).fit(ds, {0: list(range(40))}, [0], 4)
    assert legacy.edges["factor"] == [0, 3, 1, 4, 5]
    assert fixed.ic["factor"] > 0
    assert fixed.edges["factor"] == [0, 2, 2, 4, 5]
    scores = fixed.scores(ds, list(range(40)))
    assert scores == sorted(scores)
    assert legacy.edges["factor"] == [0, 3, 1, 4, 5]
    assert increasing_means([[0, 1], [9, 3], [1, 1]]) == [0, 2.5, 2.5]
    assert increasing_means([[0, 0], [1, 1]]) is None


def test_risk_direction_and_missing_features_fail_closed():
    ds = sample()
    ds.ret[3] = [-r for r in ds.ret[3]]
    model = DirectionalQuantEdge({"risk": fusion.US_QUANT_GROUPS["risk"]}).fit(ds, {0: list(range(40))}, [0], 4)
    scores = model.scores(ds, list(range(40)))
    assert model.ic["risk"] > 0
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[-1]
    ds.f["atr_pct"][0] = float("nan")
    assert model.scores(ds, list(range(40)))[0] is None
    ds = sample()  # high volatility rewarded by labels: negative signed IC disables risk
    model.fit(ds, {0: list(range(40))}, [0], 4)
    assert model.edges["risk"] is None
    assert model.scores(ds, list(range(40))) == [None] * 40


def test_fit_ignores_future_sessions_unmatured_labels_and_nonfinite_labels():
    ds = sample()
    groups = {"factor": (("x", 1),)}
    expected = DirectionalQuantEdge(groups).fit(ds, {0: list(range(40))}, [0], 4)
    ds.f["x"] += list(range(40))
    ds.ret[3] += [-999.] * 40
    ds.row_session += [4] * 40
    ds.end[3] += [7] * 40
    actual = DirectionalQuantEdge(groups).fit(ds, {0: list(range(80)), 4: list(range(40, 80))}, [0, 4], 4)
    assert actual.edges == expected.edges and actual.ic == expected.ic
    ds.end[3] = [4] * 80
    actual.fit(ds, {0: list(range(80))}, [0], 4)
    assert actual.edges["factor"] is None
    ds.end[3] = [3] * 80
    ds.ret[3] = [float("inf")] * 80
    actual.fit(ds, {0: list(range(80))}, [0], 4)
    assert actual.edges["factor"] is None


@pytest.mark.parametrize("market", ["EGX", "US"])
def test_separate_walkforward_retains_baseline_and_never_registers_live_arms(market):
    original = deepcopy(fusion.MARKETS)
    ds = dataset.build(universe(n_days=180))
    legacy = fusion_eval.run(ds, market=market, min_train=120)
    result = fusion_eval.run(ds, market=market, min_train=120, challenger=True)
    prefix = "US-" if market == "US" else ""
    assert result["config"] == prefix + "DF-CFG-2"
    assert set(result["results"]) == {prefix + "DF0", prefix + "DF-Q2"}
    assert result["results"][prefix + "DF0"] == legacy["results"][prefix + "DF0"]
    assert result["quant_training_folds"] and result["promotion"] == "NOT_AUTHORIZED"
    assert "fundamentals" not in configuration(market)["weights"]
    assert fusion.MARKETS == original
    assert fusion_eval.run(ds, market=market, min_train=120) == legacy


def test_cli_challenger_cannot_overwrite_live_cache(tmp_path, monkeypatch):
    ds = SimpleNamespace(sessions=["2026-01-01", "2026-02-01"])
    monkeypatch.setattr(fusion_run.sources, "load_egx", lambda *a: [])
    monkeypatch.setattr(fusion_run, "build", lambda s: ds)
    monkeypatch.setattr(fusion_run, "label_counts", lambda s: {})
    seen = []
    def evaluate(ds, **kwargs):
        seen.append(kwargs)
        return {"evaluated_rows": 0, "results": {}}
    monkeypatch.setattr(fusion_run.fusion_eval, "run", evaluate)
    monkeypatch.setattr(fusion_run.fusion_eval, "factor_research", lambda ds: {})
    live = tmp_path / "egx" / "fusion_walkforward.json"
    live.parent.mkdir()
    live.write_text('preserved')
    assert fusion_run.main(["--state", str(tmp_path), "--market", "EGX", "--quant-challenger"]) == 0
    assert live.read_text() == 'preserved'
    assert (live.parent / 'fusion_walkforward_cfg2.json').exists()
    assert seen[0]["challenger"] is True
