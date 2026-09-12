"""M7.1 arithmetic and evidence-boundary fixtures, not historical evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, ROUND_UP, getcontext, localcontext
from uuid import UUID

import pytest

from app.domain.enums import MarketRegimeType, RiskDecisionType
from app.paper.replay import simulate_paper_replay
from app.paper.replay_models import PaperReplayInput, PaperReplayResult
from app.performance.models import PerformanceConfig, PerformanceReport
from app.performance.replay import analyze_replay_performance
from app.performance.replay_models import (
    ReplayPerformanceAnalysisInput, ReplayPerformanceObservation,
    ReplaySlippageScenario,
)
from test_m6_1_paper_replay import (
    OPEN1, config, plan, replay_request, risk, trading_day,
)

D = Decimal


def observation(kind="WIN", *, trade_id=1, opened=OPEN1, cfg=None,
                regime=MarketRegimeType.RISK_ON):
    next_open = opened + timedelta(days=1)
    p = plan(admission=opened, trade_plan_id=UUID(int=trade_id),
             valid_until=next_open + timedelta(minutes=3))
    quiet = (("13", "14", "13", "13"),) * 3
    first_rows = (("10", "11", "10", "10"),
                  ("10", "11", "9.5", "10"), ("10", "11", "9.5", "10"))
    if kind in ("INCOMPLETE", "NO_FILL"):
        first_rows = quiet
    first = trading_day(market_date=opened.date(), opened_at=opened,
                        session_id="s1", rows=first_rows)
    second_rows = {
        "WIN": (("10", "11.5", "9.7", "11"), ("11", "12", "10", "12"), ("12", "12", "11", "12")),
        "LOSS": (("10", "11", "9.7", "10"), ("10", "11", "9", "9"), ("9", "10", "9", "9")),
        "FLAT": first_rows,
        "NO_FILL": quiet,
    }.get(kind, first_rows)
    second = trading_day(market_date=next_open.date(), opened_at=next_open,
                         session_id="s2", rows=second_rows)
    days = (first,) if kind in ("OPEN", "INCOMPLETE", "REJECTED") else (first, second)
    if cfg is None:
        cfg = config(time_exit_at=next_open) if kind == "FLAT" else config()
    r = risk(p, decision=RiskDecisionType.BLOCK) if kind == "REJECTED" else risk(p)
    inp = replay_request(days, admission=opened, p=p, cfg=cfg, r=r)
    return ReplayPerformanceObservation(
        replay_input=inp, replay_result=simulate_paper_replay(inp),
        strategy_id="fixture-swing", strategy_version="v1", market_regime=regime,
    )


def request(rows=(), **changes):
    return ReplayPerformanceAnalysisInput(
        config=PerformanceConfig(config_version="performance-v1", starting_equity=D(100)),
        observations=tuple(rows), **changes,
    )


def analyze(rows=(), **changes):
    return analyze_replay_performance(request(rows, **changes))


def revise(row, **changes):
    inp = row.replay_input.model_copy(update=changes)
    return ReplayPerformanceObservation(
        replay_input=inp, replay_result=simulate_paper_replay(inp),
        strategy_id=row.strategy_id, strategy_version=row.strategy_version,
        market_regime=row.market_regime,
    )


def scenario(rows, name="stress"):
    return ReplaySlippageScenario(scenario_id=name, observations=tuple(rows))


def divide(a, b):
    with localcontext(Context(prec=34)):
        return D(a) / D(b)


def test_cross_session_completed_economics_and_identity():
    row = observation()
    result = row.replay_result
    assert result.position.entry.session_id == "s1"
    assert result.position.exit.session_id == "s2"
    report = analyze((row,))
    assert type(report.performance) is PerformanceReport
    summary = report.performance.summary
    assert summary.completed_count == summary.win_count == 1
    assert summary.total_gross_pnl == summary.total_net_pnl == D(20)
    assert summary.total_costs == 0
    assert summary.net_expectancy == D(20)
    assert summary.r_expectancy == D(1)
    assert summary.average_mae == D(".5")
    assert summary.average_mfe == D(2)
    assert summary.average_mae_r == D(".25")
    assert summary.average_mfe_r == D(1)
    assert row.strategy_id == "fixture-swing"
    assert row.strategy_version == "v1"
    assert report.performance.regimes[0].market_regime == row.market_regime
    assert report.performance.slippage_sensitivity == ()
    assert report.performance.baseline_scenario_id is None


def test_cross_month_realization_uses_exit_market_date_not_first_session():
    jan = datetime(2024, 1, 31, 14, 30, tzinfo=timezone.utc)
    row = observation(opened=jan)
    assert row.replay_result.position.entry.market_date.month == 1
    assert row.replay_result.position.exit.market_date.month == 2
    report = analyze((row,)).performance
    assert [month.month for month in report.months] == ["2024-02"]
    assert report.months[0].summary.total_net_pnl == D(20)
    assert report.monthly_consistency.evaluated_months == 1
    assert report.monthly_consistency.positive_months == 1


def test_month_uses_exit_local_date_even_when_evidence_known_next_month():
    row = observation(opened=datetime(2024, 1, 30, 14, 30, tzinfo=timezone.utc))
    inp = row.replay_input
    from test_m6_1_paper_replay import closed_day
    known = datetime(2024, 2, 1, 16, tzinfo=timezone.utc)
    days = tuple(day.model_copy(update={
        "bars": tuple(bar.model_copy(update={"available_at_utc": known}) for bar in day.bars),
    }) for day in inp.calendar_days) + (closed_day(known.date()),)
    row = revise(row, calendar_days=days, path_complete_through_at=known)
    assert row.replay_result.position.exit.known_at_utc.month == 2
    assert [m.month for m in analyze((row,)).performance.months] == ["2024-01"]


def test_costs_profit_factor_expectancy_and_drawdown_preserve_m7_economics():
    rows = (observation(), observation("LOSS", trade_id=2), observation("FLAT", trade_id=3))
    report = analyze(rows).performance
    s = report.summary
    assert (s.win_count, s.loss_count, s.breakeven_count) == (1, 1, 1)
    assert s.total_net_pnl == D(10)
    assert s.profit_factor == D(2)
    assert s.net_expectancy == divide(10, 3)
    assert s.r_expectancy == divide(".5", 3)
    assert s.average_win == D(20) and s.average_loss == D(-10)
    assert s.average_mae == divide(2, 3)
    assert s.average_mfe == divide(4, 3)
    assert s.average_mae_r == divide(1, 3)
    assert s.average_mfe_r == divide(2, 3)
    assert report.drawdown.ending_equity == D(110)
    assert report.drawdown.max_drawdown_amount == D(10)
    assert report.drawdown.max_drawdown_pct == divide(10, 120)
    costly = analyze((observation(cfg=config(cost_bps_per_side=D(10), fixed_cost_per_side=D(1))),)).performance.summary
    assert costly.total_gross_pnl == D(20)
    assert costly.total_costs == D("2.22")
    assert costly.total_net_pnl == D("17.78")


@pytest.mark.parametrize("state", ["OPEN", "INCOMPLETE", "NO_FILL", "REJECTED"])
def test_noncompleted_states_never_realize(state):
    row = observation(state)
    assert row.replay_result.state == state
    report = analyze((row,)).performance
    assert report.summary.total_observations == 1
    assert getattr(report.summary, state.lower() + "_count") == 1
    assert report.summary.completed_count == 0
    assert report.summary.total_net_pnl == report.summary.total_costs == 0
    assert report.summary.net_expectancy is report.summary.r_expectancy is None
    assert report.summary.average_mae is report.summary.average_mfe is None
    assert report.months == ()
    assert report.drawdown.ending_equity == report.drawdown.starting_equity == 100
    assert report.drawdown.max_drawdown_amount == 0


def test_mixed_noncompleted_states_do_not_dilute_realized_expectancy():
    rows = tuple(observation(kind, trade_id=i + 1) for i, kind in enumerate(
        ("WIN", "OPEN", "INCOMPLETE", "NO_FILL", "REJECTED")))
    summary = analyze(rows).performance.summary
    assert summary.total_observations == 5 and summary.completed_count == 1
    assert summary.net_expectancy == 20 and summary.economic_win_rate == 1


def test_empty_and_loss_only_profit_factor():
    empty = analyze().performance
    assert empty.summary.total_net_pnl == 0
    assert empty.summary.profit_factor is None
    assert empty.months == empty.regimes == ()
    assert analyze((observation("LOSS"),)).performance.summary.profit_factor == 0


def test_observation_and_analyzer_recompute_through_replay_function(monkeypatch):
    import app.performance.replay_models as contracts

    row = observation()
    calls = []
    def checked(inp):
        calls.append(inp)
        return simulate_paper_replay(inp)
    monkeypatch.setattr(contracts, "simulate_paper_replay", checked)
    checked_row = ReplayPerformanceObservation(**{
        name: getattr(row, name) for name in ReplayPerformanceObservation.model_fields})
    assert calls == [row.replay_input]
    inp = request((checked_row,))
    calls.clear()
    analyze_replay_performance(inp)
    # Pydantic can run an after-validator again when nesting a rebuilt model.
    # The contract is fresh recomputation of the same canonical input, not a
    # particular number of internal validation passes.
    assert calls and all(inp == row.replay_input for inp in calls)


@pytest.mark.parametrize("mutation", ["metrics", "entry", "exit", "state", "provenance"])
def test_structurally_valid_result_mismatch_is_rejected(mutation):
    row = observation()
    result = row.replay_result
    if mutation == "metrics":
        result = result.model_copy(update={"metrics": result.metrics.model_copy(update={"net_pnl": D(21)})})
    elif mutation in ("entry", "exit", "provenance"):
        name = "entry" if mutation == "entry" else "exit"
        fill = getattr(result.position, name)
        change = {"source_id": "another-source"} if mutation == "provenance" else {"price": fill.price + D(".1")}
        result = result.model_copy(update={"position": result.position.model_copy(update={name: fill.model_copy(update=change)})})
    else:
        result = PaperReplayResult(state="INCOMPLETE")
    with pytest.raises(ValueError, match="input/result mismatch"):
        ReplayPerformanceObservation(replay_input=row.replay_input, replay_result=result,
                                     strategy_id=row.strategy_id, strategy_version=row.strategy_version,
                                     market_regime=row.market_regime)


@pytest.mark.parametrize("location", [
    "input", "result", "day", "bar", "plan", "risk", "config", "position", "entry", "metrics",
])
def test_nested_dictionary_substitutions_fail_closed_at_analyzer(location):
    row = observation()
    inp, result = row.replay_input, row.replay_result
    if location in ("input", "result"):
        field = "replay_" + location
        row = row.model_copy(update={field: getattr(row, field).model_dump(mode="python")})
    elif location in ("day", "bar"):
        first, *rest = inp.calendar_days
        if location == "day":
            first = first.model_dump(mode="python")
        else:
            first = first.model_copy(update={"bars": (first.bars[0].model_dump(mode="python"), *first.bars[1:])})
        row = row.model_copy(update={"replay_input": inp.model_copy(update={"calendar_days": (first, *rest)})})
    elif location in ("plan", "risk", "config"):
        field = {"plan": "trade_plan", "risk": "risk_decision", "config": "config"}[location]
        row = row.model_copy(update={"replay_input": inp.model_copy(update={field: getattr(inp, field).model_dump(mode="python")})})
    else:
        if location == "entry":
            result = result.model_copy(update={"position": result.position.model_copy(update={"entry": result.position.entry.model_dump(mode="python")})})
        else:
            result = result.model_copy(update={location: getattr(result, location).model_dump(mode="python")})
        row = row.model_copy(update={"replay_result": result})
    corrupted = request().model_copy(update={"observations": (row,)})
    with pytest.raises(ValueError):
        analyze_replay_performance(corrupted)


def test_model_construct_and_mutable_domain_corruption_revalidated():
    row = observation()
    forged = ReplayPerformanceAnalysisInput.model_construct(
        config=PerformanceConfig(config_version="performance-v1", starting_equity=D(100)),
        observations=(row.model_copy(update={"replay_result": PaperReplayResult(state="INCOMPLETE")}),),
    )
    with pytest.raises(ValueError, match="mismatch"):
        analyze_replay_performance(forged)
    inp = request((row,))
    inp.observations[0].replay_input.trade_plan.__dict__["target_1"] = D(13)
    with pytest.raises(ValueError, match="mismatch"):
        analyze_replay_performance(inp)


def test_duplicate_trade_plan_id_rejected_even_for_noncompleted():
    row = observation("OPEN")
    with pytest.raises(ValueError, match="duplicate trade_plan_id"):
        analyze((row, row))


def test_exact_slippage_sensitivity_preserves_evidence_and_baseline_deltas():
    rows = (observation(), observation("LOSS", trade_id=2), observation("OPEN", trade_id=3))
    before = tuple(row.model_dump(mode="python") for row in rows)
    stressed = tuple(revise(row, config=row.replay_input.config.model_copy(update={
        "target_slippage_bps": D(100), "stop_slippage_bps": D(100),
    })) for row in rows)
    report = analyze(rows, slippage_scenarios=(scenario(stressed), scenario(rows, "base")), baseline_scenario_id="base")
    base, stress = report.slippage_sensitivity
    assert base.total_net_pnl == 10 and stress.total_net_pnl == D("7.9")
    assert stress.completed_count == 2
    assert stress.net_expectancy == D("3.95")
    assert stress.profit_factor == divide("18.8", "10.9")
    assert stress.max_drawdown_amount == D("10.9")
    assert stress.max_drawdown_pct == divide("10.9", "118.8")
    assert stress.delta_total_net_pnl == D("-2.1")
    assert stress.delta_net_expectancy == D("-1.05")
    assert report.baseline_scenario_id == "base"
    assert stress.scenario.observations == stressed
    assert tuple(row.model_dump(mode="python") for row in rows) == before


@pytest.mark.parametrize("field", [
    "entry_slippage_bps", "stop_slippage_bps", "target_slippage_bps", "scheduled_exit_slippage_bps",
])
def test_each_explicit_slippage_field_is_allowed(field):
    row = observation("FLAT" if field == "scheduled_exit_slippage_bps" else "WIN")
    changed = revise(row, config=row.replay_input.config.model_copy(update={field: D(10)}))
    report = analyze((row,), slippage_scenarios=(scenario((changed,)),))
    assert report.slippage_sensitivity[0].scenario.observations == (changed,)


@pytest.mark.parametrize("field,value", [
    ("cost_bps_per_side", D(1)), ("fixed_cost_per_side", D(1)),
    ("max_volume_participation_pct", D(".5")),
    ("time_exit_at", OPEN1 + timedelta(days=1)),
    ("session_end_at", OPEN1 + timedelta(days=1)),
])
def test_scenarios_cannot_change_non_slippage_configuration(field, value):
    row = observation()
    changed = revise(row, config=row.replay_input.config.model_copy(update={field: value}))
    with pytest.raises(ValueError, match="unauthorized scenario config"):
        analyze((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize("field", [
    "trade_plan", "risk_decision", "admission_time", "instrument_id", "venue_id",
    "calendar_days", "bars", "source_id", "provenance_id", "path_complete_through_at",
    "market_timezone_name", "available_at_utc",
])
def test_scenarios_cannot_change_valid_underlying_replay_path(field):
    row = observation()
    inp = row.replay_input
    changes = {}
    if field == "trade_plan":
        changes[field] = inp.trade_plan.model_copy(update={"target_1": D(13)})
    elif field == "risk_decision":
        changes[field] = inp.risk_decision.model_copy(update={"quantity": 9})
    elif field == "admission_time":
        changes[field] = inp.admission_time + timedelta(seconds=1)
    elif field == "path_complete_through_at":
        changes[field] = inp.path_complete_through_at + timedelta(minutes=1)
    elif field == "market_timezone_name":
        changes[field] = "America/Detroit"
    elif field == "calendar_days":
        changes = {"calendar_days": inp.calendar_days[:1],
                   "path_complete_through_at": inp.calendar_days[0].closes_at_utc}
    else:
        bar_change = {"close": D("10.1")} if field == "bars" else {field: "changed"}
        if field == "available_at_utc":
            bar_change = {field: inp.calendar_days[0].bars[0].available_at_utc + timedelta(seconds=1)}
        if field in ("instrument_id", "venue_id"):
            changes[field] = "changed"
            days = tuple(day.model_copy(update={"bars": tuple(bar.model_copy(update=bar_change) for bar in day.bars)}) for day in inp.calendar_days)
        else:
            first, *rest = inp.calendar_days
            days = (first.model_copy(update={"bars": (first.bars[0].model_copy(update=bar_change), *first.bars[1:])}), *rest)
        changes["calendar_days"] = days
    changed = revise(row, **changes)  # Valid, independently recomputed evidence.
    with pytest.raises(ValueError, match="underlying replay input mismatch"):
        analyze((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize("field,value", [
    ("strategy_id", "other"), ("strategy_version", "v2"),
    ("market_regime", MarketRegimeType.RISK_OFF),
])
def test_scenarios_cannot_change_observation_identity(field, value):
    row = observation()
    changed = row.model_copy(update={field: value})
    with pytest.raises(ValueError, match="scenario identity mismatch"):
        analyze((row,), slippage_scenarios=(scenario((changed,)),))


@pytest.mark.parametrize("case", ["duplicate_scenario", "missing_trade", "extra_trade", "duplicate_trade", "missing_baseline"])
def test_scenario_set_integrity(case):
    row = observation()
    kwargs = {}
    scenarios = (scenario((row,)),)
    if case == "duplicate_scenario":
        scenarios *= 2
    elif case == "missing_trade":
        scenarios = (scenario(()),)
    elif case == "extra_trade":
        scenarios = (scenario((row, observation(trade_id=2))),)
    elif case == "duplicate_trade":
        scenarios = (scenario((row, row)),)
    else:
        kwargs["baseline_scenario_id"] = "missing"
    with pytest.raises(ValueError):
        analyze((row,), slippage_scenarios=scenarios, **kwargs)


def test_scenario_model_copy_cannot_bypass_path_binding():
    row = observation()
    inp = request((row,))
    changed = revise(row, admission_time=row.replay_input.admission_time + timedelta(seconds=1))
    corrupted = inp.model_copy(update={"slippage_scenarios": (scenario((changed,)),)})
    with pytest.raises(ValueError, match="underlying"):
        analyze_replay_performance(corrupted)


def test_hostile_decimal_context_does_not_change_analysis_or_sensitivity():
    from test_m7_extensions import series

    rows = (observation(cfg=config(target_slippage_bps=D("12.3456"))), observation("LOSS", trade_id=2))
    altered = tuple(revise(row, config=row.replay_input.config.model_copy(update={"target_slippage_bps": D("78.1234")})) for row in rows)
    kwargs = dict(slippage_scenarios=(scenario(rows, "base"), scenario(altered)),
                  baseline_scenario_id="base", periodic_returns=series())
    expected = analyze(rows, **kwargs)
    hostile = Context(prec=3, rounding=ROUND_UP, Emin=-2, Emax=2)
    hostile.traps[Inexact] = True
    with localcontext(hostile):
        assert analyze(rows, **kwargs) == expected
        assert getcontext().prec == 3
        assert getcontext().rounding == ROUND_UP
        assert getcontext().traps[Inexact]
    assert expected.performance.risk_adjusted.sharpe is not None


def timed(row, *, known, exit_first=False):
    inp = row.replay_input
    first, second = inp.calendar_days
    if exit_first:
        bars = list(second.bars)
        # Move the complete target/stop observation to the first interval,
        # keeping all canonical session timing and coverage intact.
        prices = {name: getattr(bars[1], name) for name in ("open", "high", "low", "close")}
        bars[0] = bars[0].model_copy(update=prices)
        second = second.model_copy(update={"bars": tuple(bars)})
    days = tuple(day.model_copy(update={"bars": tuple(bar.model_copy(update={"available_at_utc": known}) for bar in day.bars)}) for day in (first, second))
    return revise(row, calendar_days=days, path_complete_through_at=known)


@pytest.mark.parametrize("ordering", ["known_at", "interval_start", "trade_plan_id"])
def test_realized_drawdown_uses_all_exit_ordering_keys(ordering):
    known = OPEN1 + timedelta(days=1, hours=1)
    if ordering == "known_at":
        # Win's interval and ID are later, but it becomes known first.
        win = timed(observation(trade_id=2), known=known)
        loss = timed(observation("LOSS", trade_id=1), known=known + timedelta(seconds=1), exit_first=True)
    elif ordering == "interval_start":
        # Equal known_at: earlier win interval outranks its later ID.
        win = timed(observation(trade_id=2), known=known, exit_first=True)
        loss = timed(observation("LOSS", trade_id=1), known=known)
    else:
        win = timed(observation(trade_id=1), known=known)
        loss = timed(observation("LOSS", trade_id=2), known=known)
    a = analyze((loss, win)).performance
    b = analyze((win, loss)).performance
    assert a == b
    assert a.drawdown.peak_equity == 120
    assert a.drawdown.max_drawdown_amount == 10
    assert a.drawdown.max_drawdown_pct == divide(10, 120)


def test_legacy_observations_and_inputs_are_not_accepted_by_replay_path():
    from test_m7_performance import observation as legacy_observation
    from app.performance.models import PerformanceAnalysisInput

    row = legacy_observation("WIN")
    with pytest.raises(ValueError, match="ReplayPerformanceObservation"):
        request((row,))
    legacy = PerformanceAnalysisInput(schema_version="performance-analysis-v1", config=request().config, observations=(row,))
    with pytest.raises(TypeError, match="ReplayPerformanceAnalysisInput"):
        analyze_replay_performance(legacy)


def test_legacy_m7_still_recomputes_only_simulate_paper(monkeypatch):
    from test_m7_performance import observation as legacy_observation
    from app.performance import analyze_performance, PerformanceAnalysisInput
    import app.paper.simulator as legacy
    import app.performance.replay_models as replay_contracts

    row = legacy_observation("WIN")
    original = legacy.simulate_paper
    calls = []
    def checked(inp):
        calls.append(inp)
        return original(inp)
    def forbidden(inp):
        pytest.fail("legacy M7 must not recompute through M6.1")
    monkeypatch.setattr(legacy, "simulate_paper", checked)
    monkeypatch.setattr(replay_contracts, "simulate_paper_replay", forbidden)
    report = analyze_performance(PerformanceAnalysisInput(
        schema_version="performance-analysis-v1", config=request().config, observations=(row,),
    ))
    assert calls and all(call == row.paper_input for call in calls)
    assert report.summary.total_net_pnl == 20


@pytest.mark.parametrize("state", ["OPEN", "INCOMPLETE", "NO_FILL", "REJECTED"])
def test_noncompleted_sensitivity_has_no_realized_values_or_expectancy_delta(state):
    row = observation(state)
    changed = revise(row, config=row.replay_input.config.model_copy(update={"entry_slippage_bps": D(10)}))
    result = analyze((row,), slippage_scenarios=(scenario((row,), "base"), scenario((changed,))),
                     baseline_scenario_id="base")
    for sensitivity in result.slippage_sensitivity:
        assert sensitivity.completed_count == 0
        assert sensitivity.total_net_pnl == sensitivity.delta_total_net_pnl == 0
        assert sensitivity.net_expectancy is sensitivity.delta_net_expectancy is None
        assert sensitivity.max_drawdown_amount == 0


@pytest.mark.parametrize("field,value", [
    ("strategy_id", " strategy"), ("strategy_version", "v1 "),
    ("market_regime", "RISK_ON"), ("strategy_id", ""),
])
def test_corrupted_identity_is_rejected_without_normalization(field, value):
    row = observation().model_copy(update={field: value})
    with pytest.raises(ValueError):
        analyze_replay_performance(request().model_copy(update={"observations": (row,)}))


@pytest.mark.parametrize("location", ["metrics_decimal", "bar_decimal", "bar_chronology", "risk_quantity"])
def test_nested_invalid_model_copies_fail_closed(location):
    row = observation()
    inp, result = row.replay_input, row.replay_result
    if location == "metrics_decimal":
        row = row.model_copy(update={"replay_result": result.model_copy(update={
            "metrics": result.metrics.model_copy(update={"net_pnl": 20.0}),
        })})
    elif location == "risk_quantity":
        row = row.model_copy(update={"replay_input": inp.model_copy(update={
            "risk_decision": inp.risk_decision.model_copy(update={"quantity": True}),
        })})
    else:
        day = inp.calendar_days[0]
        change = {"open": 10.0} if location == "bar_decimal" else {"interval_start_utc": day.opens_at_utc + timedelta(seconds=1)}
        changed_day = day.model_copy(update={"bars": (day.bars[0].model_copy(update=change), *day.bars[1:])})
        row = row.model_copy(update={"replay_input": inp.model_copy(update={"calendar_days": (changed_day, *inp.calendar_days[1:])})})
    with pytest.raises(ValueError):
        analyze_replay_performance(request().model_copy(update={"observations": (row,)}))


@pytest.mark.parametrize("field", ["observations", "slippage_scenarios", "config", "periodic_returns"])
def test_analysis_contract_dictionary_and_list_substitutions_fail_closed(field):
    from test_m7_extensions import series

    row = observation()
    inp = request((row,))
    values = {
        "observations": [row],
        "slippage_scenarios": (scenario((row,)).model_dump(mode="python"),),
        "config": inp.config.model_dump(mode="python"),
        "periodic_returns": series().model_dump(mode="python"),
    }
    with pytest.raises(ValueError):
        analyze_replay_performance(inp.model_copy(update={field: values[field]}))


def test_corrupt_scenario_result_is_recomputed_instead_of_trusted():
    row = observation()
    wrong = row.model_copy(update={"replay_result": PaperReplayResult(state="INCOMPLETE")})
    forged = scenario((row,)).model_copy(update={"observations": (wrong,)})
    with pytest.raises(ValueError, match="input/result mismatch"):
        analyze_replay_performance(request((row,)).model_copy(update={"slippage_scenarios": (forged,)}))


def test_monthly_and_regime_consistency_preserve_realized_and_nonrealized_counts():
    jan = datetime(2024, 1, 30, 14, 30, tzinfo=timezone.utc)
    rows = (
        observation(opened=jan),
        observation("LOSS", trade_id=2, opened=jan + timedelta(days=1), regime=MarketRegimeType.RISK_OFF),
        observation("FLAT", trade_id=3, opened=datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)),
        observation("OPEN", trade_id=4, regime=MarketRegimeType.RISK_OFF),
    )
    report = analyze(rows).performance
    assert [(m.month, m.summary.total_net_pnl) for m in report.months] == [
        ("2024-01", D(20)), ("2024-02", D(-10)), ("2024-03", D(0)),
    ]
    assert report.monthly_consistency.evaluated_months == 3
    assert report.monthly_consistency.positive_months == 1
    assert report.monthly_consistency.negative_months == 1
    assert report.monthly_consistency.flat_months == 1
    off = next(r.summary for r in report.regimes if r.market_regime == MarketRegimeType.RISK_OFF)
    assert off.total_observations == 2 and off.completed_count == off.open_count == 1
    assert off.total_net_pnl == -10 and off.net_expectancy == -10


def test_partial_replay_result_cannot_be_reused_for_extended_path():
    full = observation()
    day = full.replay_input.calendar_days[0]
    partial = revise(full, calendar_days=(day,), path_complete_through_at=day.closes_at_utc)
    assert partial.replay_result.state == "OPEN"
    forged = partial.model_copy(update={"replay_input": full.replay_input})
    with pytest.raises(ValueError, match="input/result mismatch"):
        analyze_replay_performance(request().model_copy(update={"observations": (forged,)}))
