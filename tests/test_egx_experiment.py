import copy
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import egx_experiment_run as runner
from app.strategies import egx_experiment as exp
from app.strategies.egx_experiment_config import ACTIVE_CONFIG, ARMS, CONFIGS
from app.strategies.egx_ranking import Bar, classify
from app.ui.experiment import attach, cell, explanation, render_live, render_performance, render_system

CFG = CONFIGS[ACTIVE_CONFIG]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "egx_cpci_2026-10-01.json").read_text())


def egx_days(n, end=date(2026, 10, 1)):
    days, day = [], end
    while len(days) < n:
        if day.weekday() not in (4, 5):  # EGX: Friday/Saturday closed
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def bars_from(closes, volume=100_000, end=date(2026, 10, 1), highs=None):
    out = []
    for i, (day, close) in enumerate(zip(egx_days(len(closes), end), closes)):
        c = Decimal(str(close))
        h = Decimal(str(highs[i])) if highs else c * Decimal("1.01")
        out.append(Bar(day.isoformat(), c, max(h, c), c * Decimal("0.99"), c, Decimal(volume)))
    return out


def candidate(**extra):
    base = {"candidate_id": "c1", "ticker": "AAA", "session": "2026-10-01", "classification": "STRONG_CANDIDATE",
            "score": 80, "entry_zone": ["99.50", "100.50"], "stop": "97.00", "target_1": "105.25",
            "target_2": "109.00", "freshness": "CURRENT", "artifact_id": "a1"}
    base.update(extra)
    return base


# --- liquidity --------------------------------------------------------------------------------------------

def test_turnover_and_relative_volume_are_computed_from_bars():
    bars = bars_from([10] * 20 + [11], volume=1000)
    bars[-1] = Bar(bars[-1].date, Decimal(11), Decimal(11), Decimal(11), Decimal(11), Decimal(3000))
    liq = exp.liquidity_evidence(bars)
    assert liq["avg_volume_20"] == "1000.00" and liq["relative_volume"] == "3.00"
    assert liq["turnover_egp"] == "33000.00" and liq["avg_turnover_20_egp"] == "11150.00"


def test_liquidity_gate_is_position_aware_and_unknown_without_evidence():
    sizing = exp.position_size(Decimal("100.5"), Decimal("97"), CFG)
    assert sizing["quantity"] == 1428 and sizing["position_value_egp"] == "143514.00" and sizing["capped_by"] == "RISK"
    deep = exp.liquidity_evidence(bars_from([100] * 30, volume=1_000_000))
    thin = exp.liquidity_evidence(bars_from([100] * 30, volume=20_000))    # 2M EGP/day: position is 7.2%
    assert exp.liquidity_gate(deep, sizing, CFG)["result"] == "PASS"
    failed = exp.liquidity_gate(thin, sizing, CFG)
    assert failed["result"] == "FAIL" and failed["reason"].startswith("LIQUIDITY_FAIL: position")
    floor = exp.liquidity_gate(exp.liquidity_evidence(bars_from([1] * 30, volume=500_000)), sizing, CFG)
    assert floor["reason"] == "LIQUIDITY_FAIL: average turnover below floor"
    unknown = exp.liquidity_gate(exp.liquidity_evidence(bars_from([100] * 10)), sizing, CFG)
    assert unknown["result"] == "UNKNOWN" and unknown["reason"].startswith("NOT_READY")
    missing = bars_from([100] * 30)
    missing[-3] = Bar(missing[-3].date, *[Decimal(100)] * 4, None)
    assert exp.liquidity_evidence(missing)["status"] == "UNKNOWN"


def test_sizing_caps_and_invalid_stop():
    tight = exp.position_size(Decimal("10"), Decimal("9.99"), CFG)
    assert tight["capped_by"] == "CAPITAL_CAP" and tight["position_value_egp"] == "200000.00"
    assert exp.position_size(Decimal("10"), Decimal("10"), CFG)["status"] == "INVALID"


# --- resistance -------------------------------------------------------------------------------------------

def test_pivots_need_confirmation_and_resistance_room_is_in_r():
    highs = [100] * 40 + [110, 104, 103, 102] + [100] * 20 + [101]
    bars = bars_from([100] * len(highs), highs=highs)
    assert [h for _, h in exp.pivot_highs(bars[:-1], half_width=3)] == [Decimal(110)]
    gate = exp.resistance_gate(bars, Decimal("100.5"), Decimal("97"), CFG)
    assert gate["nearest_resistance"] == "110" and gate["room_r"] == "2.71" and gate["result"] == "PASS"
    near = exp.resistance_gate(bars, Decimal("105"), Decimal("101"), CFG)
    assert near["result"] == "FAIL" and near["room_r"] == "1.25" and near["reason"].startswith("RESISTANCE_RR_FAIL")
    # A spike only two sessions before the decision is not yet a confirmed pivot (no lookahead).
    fresh = bars_from([100] * 60, highs=[100] * 57 + [130, 100, 100])
    assert exp.resistance_gate(fresh, Decimal("100.5"), Decimal("97"), CFG)["nearest_resistance"] is None
    open_sky = exp.resistance_gate(bars_from([100] * 60), Decimal("150"), Decimal("140"), CFG)
    assert open_sky["result"] == "PASS" and open_sky["room_r"] is None
    assert exp.resistance_gate(bars_from([100] * 5), Decimal("100.5"), Decimal("97"), CFG)["result"] == "UNKNOWN"


# --- entry / gap / chase / expiry -------------------------------------------------------------------------------

def decision_stub():
    return {"entry_zone": ["99.50", "100.50"], "stop": "97.00", "session": "2026-10-01"}


@pytest.mark.parametrize("open_,low,state", [
    ("100.00", "99.00", "ENTRY_VALID"), ("99.00", "98.00", "BELOW_ENTRY"), ("96.50", "96.00", "INVALIDATED"),
    ("101.00", "100.40", "ENTRY_VALID"), ("101.00", "100.80", "SETUP_EXPIRED"), ("102.00", "100.00", "CHASE_BLOCKED")])
def test_entry_states_on_the_entry_session(open_, low, state):
    bar = Bar("2026-10-04", Decimal(open_), Decimal("103"), Decimal(low), Decimal("101"), Decimal(1000))
    result = exp.entry_state(decision_stub(), [bar], CFG)
    assert result["state"] == state and result["expiry_session"] == "2026-10-04"
    if state == "CHASE_BLOCKED":
        assert Decimal(result["gap_vs_zone_top_pct"]) > CFG["max_chase_pct_above_zone"]


def test_waiting_when_no_entry_session_yet():
    assert exp.entry_state(decision_stub(), [], CFG)["state"] == "WAITING_FOR_ENTRY"


# --- decisions: stale data, no lookahead, attribution ----------------------------------------------------------------

def rising(n=80, end=date(2026, 10, 1)):
    return bars_from([90 + i * 0.15 for i in range(n)], volume=500_000, end=end)


def test_decision_uses_only_bars_up_to_the_session():
    bars = rising() + bars_from([500] * 5, end=date(2026, 10, 8))
    d1 = exp.decide(candidate(), bars, CFG, config_version=ACTIVE_CONFIG, strategy="S")
    d2 = exp.decide(candidate(), rising(), CFG, config_version=ACTIVE_CONFIG, strategy="S")
    assert {k: v for k, v in d1.items()} == d2 and d1["decision_bars"] == 80


def test_stale_data_blocks_challengers_but_never_v1():
    old = bars_from([100] * 60, end=date(2026, 9, 28))
    decision = exp.decide(candidate(), old, CFG, config_version=ACTIVE_CONFIG, strategy="S")
    assert decision["stale"].startswith("STALE_DATA")
    assert exp.eligibility(decision, "V2A") == ("BLOCKED", "STALE_DATA")
    assert exp.eligibility(decision, "V1") == ("ELIGIBLE", "PASS")
    flagged = exp.decide(candidate(freshness="STALE"), rising(), CFG, config_version=ACTIVE_CONFIG, strategy="S")
    assert exp.eligibility(flagged, "V2B")[1] == "STALE_DATA"


def make_world():
    """Three V1 candidates on the same session with weekend-crossing entries (Thu session, Sun entry)."""
    history = rising()
    later = [Bar("2026-10-04", Decimal("104"), Decimal("115"), Decimal("101.9"), Decimal("114"), Decimal(500_000))]
    later += [Bar((date(2026, 10, 5) + timedelta(days=i)).isoformat(), Decimal("114"), Decimal("120"),
                  Decimal("113"), Decimal("119"), Decimal(500_000)) for i in range(3)]
    close = history[-1].close
    zone = [str((close * Decimal("0.995")).quantize(Decimal("0.01"))), str((close * Decimal("1.005")).quantize(Decimal("0.01")))]
    c_ok = candidate(candidate_id="ok", ticker="OK", entry_zone=zone, stop=str(close - 3),
                     target_1=str(close + Decimal("4.5")), target_2=str(close + 9))
    c_thin = candidate(candidate_id="thin", ticker="THIN", entry_zone=zone, stop=str(close - 3),
                       target_1=str(close + Decimal("4.5")), target_2=str(close + 9), score=70)
    candidates = [c_ok, c_thin]
    bars = {"OK": history, "THIN": bars_from([float(b.close) for b in history], volume=5_000)}
    decisions = [exp.decide(c, bars[c["ticker"]], CFG, config_version=ACTIVE_CONFIG, strategy="EGX-EXP-v1")
                 for c in candidates]
    later_by = {"OK": history + later, "THIN": bars["THIN"] + later}
    return candidates, decisions, later_by


def test_arms_share_evidence_v1_is_isolated_and_attribution_explains_blocks():
    candidates, decisions, later_by = make_world()
    frozen = copy.deepcopy(candidates)
    rows = runner.evaluate(candidates, decisions, later_by, CFG)
    assert candidates == frozen   # challengers never modify V1 records
    v1 = rows["V1"]["thin"]
    assert v1["attribution"] == "PASS" and v1["status"] != "BLOCKED"
    assert rows["V2A"]["thin"]["attribution"] == "LIQUIDITY_FAIL" and rows["V2A"]["thin"]["status"] == "BLOCKED"
    assert rows["V2C"]["ok"]["status"] == "CHASE_BLOCKED"          # Sunday open gapped > 1% above the zone
    assert rows["V2B"]["ok"]["status"] == rows["V1"]["ok"]["status"]  # V2B uses V1 execution rules
    for arm in ARMS:
        assert all(r["strategy"] == "EGX-EXP-v1" and r["config_version"] == ACTIVE_CONFIG for r in rows[arm].values())
    attr = runner.attribution(rows)
    assert attr["V2A"]["codes_all_candidates"]["LIQUIDITY_FAIL"] == 1


def test_portfolio_risk_cap_blocks_excess_simultaneous_entries():
    cfg = {**CFG, "max_open_risk_pct": Decimal("0.5")}
    history = rising()
    close = history[-1].close
    zone = [str(close - Decimal("0.2")), str(close + Decimal("0.2"))]
    entry = [Bar("2026-10-04", close, close + 1, close - Decimal("0.1"), close, Decimal(500_000))]
    candidates = [candidate(candidate_id=f"c{i}", ticker=f"T{i}", entry_zone=zone, stop=str(close - 3),
                            target_1=str(close + 50), target_2=str(close + 90), score=90 - i) for i in range(2)]
    decisions = [exp.decide(c, history, cfg, config_version=ACTIVE_CONFIG, strategy="S") for c in candidates]
    rows = runner.evaluate(candidates, decisions, {c["ticker"]: history + entry for c in candidates}, cfg)
    assert rows["V2D"]["c0"]["attribution"] == "PASS" and rows["V2D"]["c0"]["quantity"] > 0
    assert rows["V2D"]["c1"]["attribution"] == "PORTFOLIO_RISK_CAP"
    assert rows["V2C"]["c1"]["attribution"] == "PASS"


# --- performance aggregation --------------------------------------------------------------------------------------------

def trade(r, status="CLOSED_T2", exit_date="2026-10-10", events=("ENTRY_FILLED", "PARTIAL_T1", "EXIT_T2")):
    return {"status": status, "net_r": r, "net_r_stressed": r - 0.1, "mae_r": "-0.5", "mfe_r": "2.0",
            "sessions_held": 3, "exit_date": exit_date, "ticker": f"T{r}", "events": [{"event": e} for e in events]}


def test_performance_metrics_and_sample_labels():
    trades = [trade(2.0), trade(-1.0, "CLOSED_STOP", "2026-10-11", ("ENTRY_FILLED", "EXIT_STOP")),
              trade(1.0, exit_date="2026-10-12"), {"status": "PENDING_ENTRY", "events": []}]
    perf = exp.performance(trades, eligible=4, blocked=1)
    assert perf["closed"] == 3 and perf["status"] == "INSUFFICIENT_SAMPLE" and perf["expectancy_r"] == 0.667
    assert perf["profit_factor"] == 3.0 and perf["max_drawdown_r"] == -1.0 and perf["cumulative_r"] == 2.0
    assert perf["stop_hit_rate"] == 0.333 and perf["t2_hit_rate"] == 0.667 and perf["median_r"] == 1.0
    assert perf["expectancy_ci95_approx"] == "NOT_COMPUTED_BELOW_20" and perf["expectancy_r_double_costs"] == 0.567
    assert "NOT_ELIGIBLE" in perf["promotion"]
    assert [exp.checkpoint(n) for n in (19, 20, 50, 100)] == [
        "INSUFFICIENT_SAMPLE", "EARLY_CHECKPOINT_20", "CHECKPOINT_50", "CHECKPOINT_100"]
    many = exp.performance([trade(1.0 if i % 2 else -0.5, exit_date=f"2026-11-{i % 28 + 1:02d}") for i in range(20)],
                           eligible=20, blocked=0)
    assert isinstance(many["expectancy_ci95_approx"], list)
    assert exp.performance([], eligible=0, blocked=0)["status"] == "INSUFFICIENT_SAMPLE"


# --- CPCI regression / explanation fixture --------------------------------------------------------------------------------

def test_cpci_fixture_reproduces_v1_plan_and_explains_the_gates():
    bars = [Bar(b["date"], *(Decimal(b[k]) for k in ("open", "high", "low", "close", "volume"))) for b in FIXTURE["bars"]]
    v1 = FIXTURE["v1_record"]
    plan = classify(ticker="CPCI", company=None, isin=None, source="s", licensing="l", admitted=True,
                    freshness="CURRENT", session=v1["session"], bars=bars)
    assert plan["entry_zone"] == v1["entry_zone"] and plan["stop"] == v1["stop"]
    assert plan["volume_ratio_20"] == 2.35 and plan["breakout_20"] is True and plan["momentum_20_pct"] == 10.31
    decision = exp.decide(v1, bars, CFG, config_version=ACTIVE_CONFIG, strategy="EGX-EXP-v1")
    assert decision["technical_class"] == "STRONG_CANDIDATE"
    assert decision["liquidity"]["avg_turnover_20_egp"] == "3674835.74"
    assert decision["liquidity_gate"]["result"] == "PASS" and decision["sizing"]["quantity"] == 175
    gate = decision["resistance_gate"]
    assert (gate["nearest_resistance"], gate["resistance_date"], gate["room_r"], gate["result"]) == (
        "644.0", "2026-08-11", "1.47", "FAIL")
    assert exp.eligibility(decision, "V2A") == ("ELIGIBLE", "PASS")
    assert exp.eligibility(decision, "V2B") == ("BLOCKED", "RESISTANCE_RR_FAIL")


# --- config versioning and UI ----------------------------------------------------------------------------------------------

def test_config_version_is_frozen():
    cfg = CONFIGS["EGX-EXP-CFG-1"]
    assert (cfg["effective_date"], cfg["min_room_to_resistance_r"], cfg["max_chase_pct_above_zone"],
            cfg["risk_pct_per_trade"], cfg["max_position_pct_of_avg_turnover"]) == (
        "2026-10-03", Decimal("1.8"), Decimal("1.0"), Decimal("0.5"), Decimal("5"))
    assert set(cfg["rationale"]) >= {"min_room_to_resistance_r", "risk_pct_per_trade", "max_chase_pct_above_zone"}


def test_ui_renders_comparison_lifecycles_system_and_details():
    candidates, decisions, later_by = make_world()
    rows = runner.evaluate(candidates, decisions, later_by, CFG)
    perf = {arm: exp.performance(list(rows[arm].values()), eligible=2, blocked=0) for arm in ARMS}
    report = {"schema": "egx-experiment-report-v1", "strategy": "EGX-EXP-v1", "config_version": ACTIVE_CONFIG,
              "config": {"effective_date": "2026-10-03", "rationale": {"x": "y"}}, "arms": {"V1": "champion"},
              "session": "2026-10-01", "champion": "V1", "promotion_policy": "No auto-promotion.",
              "live_money": False, "rows": {arm: list(rows[arm].values()) for arm in ARMS}, "performance": perf,
              "attribution": runner.attribution(rows), "health": {"status": "HEALTHY", "decisions": 2},
              "current": runner.current_view({"session": "2026-10-01", "symbols": [
                  {"ticker": "OK", "classification": "STRONG_CANDIDATE", "score": 80},
                  {"ticker": "THIN", "classification": "CANDIDATE", "score": 70},
                  {"ticker": "W", "classification": "WATCHLIST"}]}, decisions, rows)}
    assert report["current"]["THIN"]["final_action"] == "WATCH" and report["current"]["OK"]["final_action"] == "CHASE_BLOCKED"
    assert report["current"]["W"] == {"technical_class": "WATCHLIST", "final_action": "WATCH",
                                      "eligibility": "NOT_APPLICABLE"}
    html = render_performance(report)
    assert "Champion vs challenger" in html and "Expectancy (R)" in html and "Gate attribution" in html
    assert "chase blocked" in render_live(report) and "Threshold rationale" in render_system(report)
    fused = attach({"session": "2026-10-01", "symbols": [{"ticker": "THIN", "classification": "CANDIDATE"}]}, report)
    view = fused["symbols"][0]["experiment"]
    assert fused["symbols"][0]["classification"] == "CANDIDATE"   # technical class preserved
    assert "BLOCKED" in cell(view)[0] and "LIQUIDITY_FAIL" in cell(view)[0]
    assert "Liquidity:</b> FAIL" in explanation(view) and "Resistance:" in explanation(view)
    assert attach({"session": "2026-09-30", "symbols": []}, report) == {"session": "2026-09-30", "symbols": []}
