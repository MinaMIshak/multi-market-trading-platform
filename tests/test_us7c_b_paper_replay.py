"""US7C-B synthetic engineering fixtures only, never historical evidence."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, ROUND_UP, localcontext
from uuid import UUID

import pytest

from app.data.models import BarGranularity
from app.domain.enums import MarketRegimeType, TradeState
from app.paper.replay import simulate_paper_replay
from app.performance.models import PerformanceConfig
from app.performance.replay import analyze_replay_performance
from app.performance.replay_models import ReplayPerformanceAnalysisInput, ReplaySlippageScenario
from app.us.contracts import USCorporateActionCoverage, USCorporateActionEvent, USCorporateActionType, USSessionRecord, USSessionState
from app.us.historical_actions import HistoricalUSCorporateActionFact, US_ACTION_COVERAGE_EVIDENCE_FIELDS, admit_us_corporate_action_history
from app.us.historical_identity import admit_us_listing_history
from app.us.historical_intraday import (
    HistoricalUSIntradaySegmentFact, USHistoricalIntradayBar, USHistoricalIntradaySegment,
    USIntradayCoverageMode, US_INTRADAY_EVIDENCE_FIELDS, admit_us_intraday_session,
)
from app.us.historical_session import HistoricalUSSessionFact, US_SESSION_EVIDENCE_FIELDS, admit_us_session_history
from app.us.paper_replay import (
    build_us_paper_replay_input, build_us_replay_performance_observation,
    derive_us_replay_session_id, replay_us_research_trade,
)
from app.us.paper_replay_models import USPaperReplayRequest, USReplayCheckpoint
from app.us.research_adapter import USSwingResearchResult
from app.us.research_planning import admit_us_research_risk, build_us_research_trade_plan
from test_m6_1_paper_replay import config as execution_config
from test_us3_historical_daily import _listing, _package, BUILD
from test_us7a_planning_risk import policy, snapshot, terms

D = Decimal
INSTRUMENT = UUID(int=71)
DAY = date(2024, 7, 8)
OPEN = datetime(2024, 7, 8, 13, 30, tzinfo=timezone.utc)
QUIET = (("13", "14", "13", "13"),) * 3
ENTRY = (("10", "11", "10", "10"), ("10", "11", "9.5", "10"), ("10", "11", "9.5", "10"))
TARGET = (("10", "11.5", "9.7", "11"), ("11", "12", "10", "12"), ("12", "12", "11", "12"))


def action(kind, day, event_id="event"):
    extra = {}
    if kind == USCorporateActionType.SPLIT:
        extra = dict(new_shares=D(4), old_shares=D(1))
    elif kind == USCorporateActionType.SYMBOL_CHANGE:
        extra = dict(old_symbol="TEST", new_symbol="NEW")
    elif kind == USCorporateActionType.CASH_DIVIDEND:
        extra = dict(cash_amount=D(".25"), cash_currency="USD")
    return USCorporateActionEvent(event_id=event_id, effective_date=day,
                                  action_type=kind, details="engineering fixture", **extra)


def make_request(*, rows=(ENTRY, TARGET), states=None, actions=(), start=OPEN,
                 mode=USIntradayCoverageMode.FULL_SESSION, truncate=False,
                 chunks=False, expiry=None, admission=None, regime=MarketRegimeType.RISK_ON,
                 cfg=None, symbol_by_day=None, delayed=None, signal_date=None,
                 checkpoint_times=None):
    n = len(rows)
    states = states or (USSessionState.REGULAR,) * n
    dates = tuple((start + timedelta(days=i)).date() for i in range(n))
    cutoff = start + timedelta(days=n - 1, hours=8)
    decision = start - timedelta(minutes=1)
    admission = start if admission is None else admission
    signal_date = dates[0] if signal_date is None else signal_date
    symbols = symbol_by_day or ("TEST",) * n
    signal = USSwingResearchResult(
        instrument_id=INSTRUMENT, canonical_symbol="TEST", listing_mic="XNAS",
        signal_date=signal_date, decision_at=decision, source_dataset_id="a" * 64,
        config_id="b" * 64, state="WATCH", planning_close_reference=D(10),
        fast_ema=10.0, slow_ema=9.0, breakout_reference=9.5,
    )
    t = terms(entry_low=D(10), entry_high=D(11), entry_reference=D(10), stop_price=D(9),
              target_1=D(12), valid_until=expiry or (start + timedelta(days=1, minutes=3)))
    p = build_us_research_trade_plan(signal, t)
    pol = policy()
    snap = snapshot(available_at=decision, account_equity=D(1000), cash_balance=D(1000), market_regime=regime)
    risk = admit_us_research_risk(p, policy=pol, snapshot=snap, decision_at=admission, trade_state=TradeState.READY)
    listing_facts = tuple(_listing(day, symbol=symbols[i], instrument_id=INSTRUMENT) for i, day in enumerate(dates))
    if signal_date < dates[0]:
        listing_facts = (_listing(signal_date, instrument_id=INSTRUMENT),) + listing_facts
    listing = admit_us_listing_history(listing_facts, decision_at=cutoff, research_built_at=BUILD)
    session_facts = []
    for i, day in enumerate(dates):
        opened = start + timedelta(days=i)
        count = 2 if states[i] == USSessionState.EARLY_CLOSE else 3
        record = USSessionRecord(market_date=day, calendar_mic="XNAS", state=states[i],
                                 opens_at_utc=None if states[i] == USSessionState.CLOSED else opened,
                                 closes_at_utc=None if states[i] == USSessionState.CLOSED else opened + timedelta(minutes=count))
        session_facts.append(HistoricalUSSessionFact(session=record, evidence_package=_package(
            provider="calendar", category="US_SESSION_CALENDAR", covered_fields=US_SESSION_EVIDENCE_FIELDS,
            available_at=decision, sha_char="a")))
    sessions = admit_us_session_history(tuple(session_facts), calendar_mic="XNAS", coverage_start=dates[0],
                                       coverage_end=dates[-1], decision_at=cutoff, research_built_at=BUILD)
    coverage = USCorporateActionCoverage(instrument_id=INSTRUMENT, coverage_start=signal_date,
                                          coverage_end=dates[-1], actions=tuple(actions))
    action_history = admit_us_corporate_action_history((HistoricalUSCorporateActionFact(
        coverage=coverage, evidence_package=_package(provider="actions", category="US_CORPORATE_ACTIONS",
            covered_fields=US_ACTION_COVERAGE_EVIDENCE_FIELDS, available_at=cutoff, sha_char="b")),),
        instrument_id=INSTRUMENT, coverage_start=signal_date, coverage_end=dates[-1],
        decision_at=cutoff, research_built_at=BUILD)
    intradays, checkpoints = [], []
    for i, day in enumerate(dates):
        opened = start + timedelta(days=i)
        count = 2 if states[i] == USSessionState.EARLY_CLOSE else 3
        end = opened + timedelta(minutes=count)
        checkpoints.append(USReplayCheckpoint(market_date=day, path_complete_through_at=(
            checkpoint_times[i] if checkpoint_times else end)))
        if states[i] == USSessionState.CLOSED:
            continue
        selected = rows[i][:count]
        if truncate:
            selected = selected[:-1]
        groups = ((0, 1), (1, len(selected))) if chunks else ((0, len(selected)),)
        facts = []
        for chunk_index, (lo, hi) in enumerate(groups):
            sha = "cdef0123456789"[i * 2 + chunk_index]
            provenance = f"fixture-{i}-{chunk_index}"
            bars = tuple(USHistoricalIntradayBar(
                instrument_id=INSTRUMENT, market_date=day, calendar_mic="XNAS", canonical_symbol=symbols[i],
                provider_symbol=symbols[i], source_instrument_key="fixture-key", granularity=BarGranularity.M1,
                session_sequence=j + 1, interval_start_utc=opened + timedelta(minutes=j),
                interval_end_utc=opened + timedelta(minutes=j + 1),
                available_at_utc=(delayed(i, j, opened) if delayed else opened + timedelta(minutes=j + 1)),
                open=D(selected[j][0]), high=D(selected[j][1]), low=D(selected[j][2]), close=D(selected[j][3]),
                volume=D("1000.1234567890123456789"), traded_value=D("10000.1234567890123456789"),
                source_provider="intraday", source_row_number=j - lo + 1,
                source_sha256=sha * 64, provenance_id=provenance,
            ) for j in range(lo, hi))
            segment = USHistoricalIntradaySegment(
                instrument_id=INSTRUMENT, market_date=day, calendar_mic="XNAS", canonical_symbol=symbols[i],
                granularity=BarGranularity.M1, coverage_start_at_utc=bars[0].interval_start_utc,
                coverage_end_at_utc=bars[-1].interval_end_utc, first_session_sequence=lo + 1,
                last_session_sequence=hi, source_provider="intraday", source_sha256=sha * 64,
                provenance_id=provenance,
            )
            facts.append(HistoricalUSIntradaySegmentFact(segment=segment, bars=bars, evidence_package=_package(
                provider="intraday", category="US_INTRADAY_BARS", covered_fields=US_INTRADAY_EVIDENCE_FIELDS,
                available_at=end, sha_char=sha)))
        intradays.append(admit_us_intraday_session(tuple(facts), instrument_id=INSTRUMENT, calendar_mic="XNAS",
            market_date=day, granularity=BarGranularity.M1, coverage_mode=mode,
            listing_history=listing, session_history=sessions, evidence_cutoff_at=cutoff, research_built_at=BUILD))
    return USPaperReplayRequest(signal=signal, terms=t, plan=p, policy=pol, risk_snapshot=snap,
        risk_admission=risk, execution_config=cfg or execution_config(), listing_history=listing,
        session_history=sessions, action_history=action_history, intraday_sessions=tuple(intradays),
        checkpoints=tuple(checkpoints), evidence_cutoff_at=cutoff, research_built_at=BUILD)


def perf(req, result=None):
    result = result or replay_us_research_trade(req)
    obs = build_us_replay_performance_observation(req, result)
    return analyze_replay_performance(ReplayPerformanceAnalysisInput(
        config=PerformanceConfig(config_version="performance-v1", starting_equity=D(1000)), observations=(obs,),
    ))


def test_regular_cross_session_completion_and_exact_mapping():
    req = make_request()
    result = replay_us_research_trade(req)
    assert result.status == "REPLAYED"
    replay = result.replay_result
    assert replay.state == "COMPLETED" and replay.outcome == "WIN"
    assert replay.position.entry.market_date == DAY
    assert replay.position.exit.market_date == DAY + timedelta(days=1)
    assert replay.metrics.net_pnl == 20
    assert replay.metrics.mae == D(".5") and replay.metrics.mfe == D(2)
    assert result.replay_input.admission_time == req.risk_admission.risk_decision_at
    assert result.replay_input.trade_plan.trade_plan_id == req.plan.trade_plan_id
    for original, mapped in zip(req.intraday_sessions, result.replay_input.calendar_days):
        for bar, out in zip(original.bars, mapped.bars):
            for name in ("open", "high", "low", "close", "volume", "traded_value", "market_date",
                         "session_sequence", "interval_start_utc", "interval_end_utc", "available_at_utc", "provenance_id"):
                assert getattr(bar, name) == getattr(out, name)
            assert out.instrument_id == str(bar.instrument_id)
            assert out.symbol == bar.canonical_symbol and out.venue_id == bar.calendar_mic
            assert out.source_id == bar.source_sha256
    assert simulate_paper_replay(result.replay_input) == replay
    assert build_us_paper_replay_input(req) == result.replay_input


def test_early_close_is_explicit_and_does_not_liquidate():
    req = make_request(rows=(ENTRY,), states=(USSessionState.EARLY_CLOSE,), expiry=OPEN + timedelta(minutes=2))
    result = replay_us_research_trade(req)
    day = result.replay_input.calendar_days[0]
    assert day.closes_at_utc == OPEN + timedelta(minutes=2)
    assert len(day.bars) == 2 and result.replay_result.state == "OPEN"


def test_closed_date_and_pending_entry_carry_without_inference():
    req = make_request(rows=(QUIET, (), ENTRY), states=(USSessionState.REGULAR, USSessionState.CLOSED, USSessionState.REGULAR),
                       expiry=OPEN + timedelta(days=2, minutes=3))
    result = replay_us_research_trade(req)
    closed = result.replay_input.calendar_days[1]
    assert closed.state == "CLOSED" and closed.bars == () and closed.session_id is None
    assert result.replay_result.state == "OPEN"
    assert result.replay_result.position.entry.market_date == DAY + timedelta(days=2)


@pytest.mark.parametrize("expiry", [OPEN + timedelta(minutes=2), OPEN + timedelta(hours=1)])
def test_no_fill_only_with_coverage_through_intraday_or_after_close_expiry(expiry):
    req = make_request(rows=(QUIET,), expiry=expiry, checkpoint_times=(max(expiry, OPEN + timedelta(minutes=3)),))
    assert replay_us_research_trade(req).replay_result.state == "NO_FILL"


def test_closed_date_expiry_and_incomplete_before_expiry():
    req = make_request(rows=(QUIET, ()), states=(USSessionState.REGULAR, USSessionState.CLOSED))
    assert replay_us_research_trade(req).replay_result.state == "NO_FILL"
    short = req.model_copy(update={"checkpoints": req.checkpoints[:1]})
    assert replay_us_research_trade(short).replay_result.state == "INCOMPLETE"


def test_filled_position_stays_open_after_expiry_until_actual_exit():
    req = make_request(expiry=OPEN + timedelta(minutes=3))
    first = req.model_copy(update={"checkpoints": req.checkpoints[:1], "intraday_sessions": req.intraday_sessions[:1]})
    a, b = replay_us_research_trade(first), replay_us_research_trade(req)
    assert a.replay_result.state == "OPEN" and b.replay_result.state == "COMPLETED"
    assert a.replay_result.position.entry == b.replay_result.position.entry


def test_multi_artifact_provenance_is_per_bar():
    req = make_request(chunks=True)
    result = replay_us_research_trade(req)
    rows = result.provenance.bar_lineage
    assert len(rows) == 6 and rows[0].source_id != rows[1].source_id
    for i, entry in enumerate(rows):
        day, index = divmod(i, 3)
        bar = req.intraday_sessions[day].bars[index]
        assert entry.source_id == bar.source_sha256
        assert entry.provenance_id == bar.provenance_id
        assert entry.source_provider == bar.source_provider
        assert entry.provider_symbol == bar.provider_symbol
        assert entry.source_instrument_key == bar.source_instrument_key
        assert entry.source_row_number == bar.source_row_number
    assert rows[0].session_id == rows[1].session_id != rows[3].session_id


def test_partial_bounded_window_is_not_promoted():
    req = make_request(mode=USIntradayCoverageMode.BOUNDED_WINDOW, truncate=True)
    with pytest.raises(ValueError, match="FULL_SESSION"):
        replay_us_research_trade(req)


def test_exact_full_span_bounded_window_is_explicitly_certified():
    req = make_request(mode=USIntradayCoverageMode.BOUNDED_WINDOW)
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "COMPLETED"
    assert result.provenance.intraday_session_ids != result.provenance.full_session_proof_ids
    assert all(s.coverage_mode == USIntradayCoverageMode.BOUNDED_WINDOW for s in req.intraday_sessions)


@pytest.mark.parametrize("with_closed", [False, True])
def test_cross_session_availability_reversal_fails_closed(with_closed):
    offset = 2 if with_closed else 1
    # A first-day prefix cannot be claimed complete with next-day availability.
    req = make_request(rows=(ENTRY, (), TARGET) if with_closed else (ENTRY, TARGET),
        states=(USSessionState.REGULAR, USSessionState.CLOSED, USSessionState.REGULAR) if with_closed else None,
        delayed=lambda i, j, o: OPEN + timedelta(days=offset, minutes=2) if i == 0 else o + timedelta(minutes=j + 1))
    req = req.model_copy(update={"checkpoints": req.checkpoints[-1:]})
    with pytest.raises(ValueError, match="nonchronological"):
        replay_us_research_trade(req)


def test_equal_availability_preserved_and_deterministic():
    req = make_request(delayed=lambda i, j, o: o + timedelta(minutes=3))
    a = replay_us_research_trade(req)
    assert a == replay_us_research_trade(req)
    assert a.replay_result.position.entry.known_at_utc == OPEN + timedelta(minutes=3)


UNSUPPORTED = tuple(kind for kind in USCorporateActionType if kind != USCorporateActionType.CASH_DIVIDEND)


@pytest.mark.parametrize("kind", UNSUPPORTED)
@pytest.mark.parametrize("pending", [False, True])
def test_unsupported_actions_reject_pending_and_open_paths(kind, pending):
    req = make_request(rows=(QUIET if pending else ENTRY, TARGET), actions=(action(kind, DAY + timedelta(days=1)),))
    result = replay_us_research_trade(req)
    assert result.status == "INCOMPATIBLE" and result.rejection_code == "UNSUPPORTED_CORPORATE_ACTION"
    assert result.rejection_action_ids == ("event",)
    assert result.replay_input is result.replay_result is None
    with pytest.raises(ValueError, match="UNSUPPORTED_CORPORATE_ACTION"):
        build_us_replay_performance_observation(req, result)


def test_action_on_closed_day_while_open_is_rejected():
    req = make_request(rows=(ENTRY, ()), states=(USSessionState.REGULAR, USSessionState.CLOSED),
                       actions=(action(USCorporateActionType.SPLIT, DAY + timedelta(days=1)),))
    assert replay_us_research_trade(req).rejection_code == "UNSUPPORTED_CORPORATE_ACTION"


def test_signal_to_admission_bridge_action_rejected():
    req = make_request(signal_date=DAY - timedelta(days=2),
        actions=(action(USCorporateActionType.SPLIT, DAY - timedelta(days=1)),))
    assert replay_us_research_trade(req).rejection_code == "UNSUPPORTED_CORPORATE_ACTION"


def test_same_day_action_and_exit_is_ambiguous():
    req = make_request(rows=(TARGET,), actions=(action(USCorporateActionType.SPLIT, DAY),))
    assert replay_us_research_trade(req).status == "INCOMPATIBLE"


@pytest.mark.parametrize("kind", UNSUPPORTED)
def test_later_action_after_proven_terminal_result_is_ignored(kind):
    req = make_request(rows=(TARGET, TARGET), actions=(action(kind, DAY + timedelta(days=1)),),
                       symbol_by_day=("TEST", "NEW") if kind == USCorporateActionType.SYMBOL_CHANGE else None)
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "COMPLETED"
    assert len(result.replay_input.calendar_days) == 1
    assert result.provenance.checked_action_ids == ()
    plain = make_request(rows=(TARGET, TARGET))
    assert replay_us_research_trade(plain).replay_result == result.replay_result


def test_cash_dividend_preserves_raw_prices_quantity_and_price_only_pnl():
    req = make_request(actions=(action(USCorporateActionType.CASH_DIVIDEND, DAY + timedelta(days=1)),))
    result = replay_us_research_trade(req)
    assert result.replay_result == replay_us_research_trade(make_request()).replay_result
    assert result.replay_result.metrics.net_pnl == D(20)
    assert result.provenance.dividend_policy == "RAW_PRICE_ONLY_NO_CASH_CREDIT"
    assert result.provenance.checked_action_ids == ("event",)


def test_cutoff_is_not_strategy_decision_or_risk_admission(monkeypatch):
    import app.us.research_adapter as strategy
    monkeypatch.setattr(strategy, "evaluate_us_swing", lambda *a, **k: pytest.fail("future strategy rebuild"))
    req = make_request()
    before = req.signal.model_dump(), req.plan.model_dump(), req.risk_admission.model_dump()
    result = replay_us_research_trade(req)
    assert req.signal.decision_at < result.replay_input.admission_time < req.evidence_cutoff_at
    assert before == (req.signal.model_dump(), req.plan.model_dump(), req.risk_admission.model_dump())


def test_m7_exact_recomputation_regime_and_exit_month():
    req = make_request(start=datetime(2024, 7, 31, 13, 30, tzinfo=timezone.utc))
    result = replay_us_research_trade(req)
    obs = build_us_replay_performance_observation(req, result)
    assert obs.market_regime == req.risk_snapshot.market_regime
    assert obs.strategy_id == req.plan.strategy_id and obs.strategy_version == req.plan.strategy_version
    assert [m.month for m in perf(req, result).performance.months] == ["2024-08"]
    bad = result.model_copy(update={"replay_result": result.replay_result.model_copy(update={
        "metrics": result.replay_result.metrics.model_copy(update={"net_pnl": D(1000)})})})
    with pytest.raises(ValueError, match="reconstruction mismatch"):
        build_us_replay_performance_observation(req, bad)


def test_deterministic_identity_excludes_local_build_clock():
    req = make_request()
    rebuilt = req.model_copy(update={"research_built_at": BUILD + timedelta(days=1)})
    assert replay_us_research_trade(req) == replay_us_research_trade(rebuilt)
    assert req.identity == rebuilt.identity
    day = req.session_history.facts[0].session
    assert derive_us_replay_session_id(day) == derive_us_replay_session_id(day)
    assert derive_us_replay_session_id(day) != derive_us_replay_session_id(req.session_history.facts[1].session)


@pytest.mark.parametrize("field", ["signal", "terms", "plan", "policy", "risk_snapshot", "risk_admission",
                                   "execution_config", "listing_history", "session_history", "action_history", "checkpoints"])
def test_nested_dictionary_substitution_is_rejected(field):
    req = make_request()
    bad = req.model_copy(update={field: {}})
    with pytest.raises(ValueError):
        replay_us_research_trade(bad)


def test_flattened_dataclass_bars_corruption_rejected_even_when_identity_unchanged():
    req = make_request()
    first = req.intraday_sessions[0]
    forged = replace(first, bars=(first.bars[0].model_copy(update={"close": D("10.1")}), *first.bars[1:]))
    assert forged.identity == first.identity
    with pytest.raises(ValueError, match="flattened bars"):
        replay_us_research_trade(req.model_copy(update={"intraday_sessions": (forged, *req.intraday_sessions[1:])}))


@pytest.mark.parametrize("field,value", [("quantity", 11), ("source_plan_id", "e" * 64), ("policy_identity", "e" * 64)])
def test_risk_admission_is_recomputed_not_trusted(field, value):
    req = make_request()
    with pytest.raises(ValueError):
        replay_us_research_trade(req.model_copy(update={"risk_admission": req.risk_admission.model_copy(update={field: value})}))


def test_future_snapshot_rejected_and_signal_terms_binding_recomputed():
    req = make_request()
    with pytest.raises(ValueError, match="future risk snapshot"):
        replay_us_research_trade(req.model_copy(update={"risk_snapshot": req.risk_snapshot.model_copy(update={"available_at": req.evidence_cutoff_at})}))
    with pytest.raises(ValueError, match="US7A plan"):
        replay_us_research_trade(req.model_copy(update={"terms": req.terms.model_copy(update={"target_1": D(13)})}))


@pytest.mark.parametrize("change", ["reverse", "duplicate", "early_cutoff"])
def test_checkpoint_calendar_truth_cannot_be_repaired(change):
    req = make_request(rows=(ENTRY, ENTRY, TARGET))
    cps = req.checkpoints
    changes = {"reverse": cps[::-1], "gap": (cps[0], cps[2]), "duplicate": (cps[0], cps[0]),
               "early_cutoff": (cps[0].model_copy(update={"path_complete_through_at": OPEN - timedelta(seconds=1)}),)}
    with pytest.raises(ValueError):
        replay_us_research_trade(req.model_copy(update={"checkpoints": changes[change]}))


def test_hostile_decimal_context_does_not_change_result_or_performance():
    req = make_request(cfg=execution_config(target_slippage_bps=D("12.34567"), cost_bps_per_side=D("7.654321")))
    expected, expected_perf = replay_us_research_trade(req), perf(req)
    hostile = Context(prec=3, rounding=ROUND_UP, Emax=2, Emin=-2)
    hostile.traps[Inexact] = True
    with localcontext(hostile):
        assert replay_us_research_trade(req) == expected
        assert perf(req) == expected_perf


def test_shared_m7_slippage_binding_and_independent_us_action_gates():
    req = make_request()
    altered = req.model_copy(update={"execution_config": req.execution_config.model_copy(update={"target_slippage_bps": D(100)})})
    base = build_us_replay_performance_observation(req, replay_us_research_trade(req))
    stress = build_us_replay_performance_observation(altered, replay_us_research_trade(altered))
    report = analyze_replay_performance(ReplayPerformanceAnalysisInput(
        config=PerformanceConfig(config_version="performance-v1", starting_equity=D(1000)),
        observations=(base,), slippage_scenarios=(ReplaySlippageScenario(scenario_id="stress", observations=(stress,)),),
    ))
    assert report.slippage_sensitivity[0].total_net_pnl == D("18.8")
    unsafe = make_request(actions=(action(USCorporateActionType.SPLIT, DAY + timedelta(days=1)),))
    unsafe = unsafe.model_copy(update={"execution_config": altered.execution_config})
    result = replay_us_research_trade(unsafe)
    with pytest.raises(ValueError, match="UNSUPPORTED_CORPORATE_ACTION"):
        build_us_replay_performance_observation(unsafe, result)


@pytest.mark.parametrize("which", ["admission", "expiry"])
def test_entry_boundaries_cannot_bisect_bar(which):
    req = make_request(admission=OPEN + timedelta(seconds=30)) if which == "admission" else make_request(expiry=OPEN + timedelta(minutes=2, seconds=30))
    assert replay_us_research_trade(req).rejection_code == "ENTRY_BOUNDARY_BISECTS_BAR"


def test_valid_block_has_no_artifacts_and_expired_block_not_backdated():
    req = make_request(regime=MarketRegimeType.RISK_OFF)
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "REJECTED" and result.replay_result.position is None
    assert perf(req, result).performance.summary.rejected_count == 1
    expired = make_request(expiry=OPEN, admission=OPEN + timedelta(minutes=1))
    result = replay_us_research_trade(expired)
    assert result.rejection_code == "ADMISSION_OUTSIDE_PLAN_VALIDITY"
    assert result.replay_result is None


def test_sparse_checkpoints_still_require_all_explicit_calendar_dates():
    req = make_request(rows=(ENTRY, ENTRY, TARGET))
    sparse = req.model_copy(update={"checkpoints": (req.checkpoints[0], req.checkpoints[2])})
    result = replay_us_research_trade(sparse)
    assert len(result.replay_input.calendar_days) == 3
    assert len(result.provenance.consumed_checkpoints) == 2
    broken = replace(req.session_history, facts=(req.session_history.facts[0], req.session_history.facts[2]))
    with pytest.raises(ValueError, match="missing explicit"):
        replay_us_research_trade(sparse.model_copy(update={"session_history": broken}))


def test_equal_availability_across_sessions_is_supported_with_later_checkpoint():
    known = OPEN + timedelta(days=1, minutes=3)
    req = make_request(delayed=lambda i, j, o: known)
    req = req.model_copy(update={"checkpoints": req.checkpoints[-1:]})
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "COMPLETED"
    assert result.replay_result.position.entry.known_at_utc == known
    assert result.replay_result.position.exit.known_at_utc == known
    assert all(bar.available_at_utc == known for day in result.replay_input.calendar_days for bar in day.bars)


def test_unproven_terminal_prefix_cannot_skip_an_intervening_action():
    known = OPEN + timedelta(days=1, minutes=3)
    req = make_request(rows=(TARGET, TARGET), delayed=lambda i, j, o: known,
                       actions=(action(USCorporateActionType.SPLIT, DAY + timedelta(days=1)),))
    req = req.model_copy(update={"checkpoints": req.checkpoints[-1:]})
    assert replay_us_research_trade(req).rejection_code == "UNSUPPORTED_CORPORATE_ACTION"


def test_expiry_bisection_is_irrelevant_to_already_proven_open_position():
    req = make_request(expiry=OPEN + timedelta(days=1, minutes=1, seconds=30))
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "COMPLETED"
    assert result.replay_result.position.entry.market_date == DAY


def test_no_fill_early_close_and_closed_only_path():
    req = make_request(rows=(QUIET,), states=(USSessionState.EARLY_CLOSE,), expiry=OPEN + timedelta(minutes=2))
    assert replay_us_research_trade(req).replay_result.state == "NO_FILL"
    req = make_request(rows=((),), states=(USSessionState.CLOSED,), expiry=OPEN + timedelta(minutes=3))
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "NO_FILL"
    assert result.replay_input.calendar_days[0].bars == ()
    assert result.provenance.bar_lineage == ()


@pytest.mark.parametrize("state", ["OPEN", "INCOMPLETE", "NO_FILL", "REJECTED"])
def test_shared_performance_noncompleted_states_are_nonrealized(state):
    if state == "REJECTED":
        req = make_request(regime=MarketRegimeType.RISK_OFF)
    else:
        req = make_request(rows=(ENTRY if state == "OPEN" else QUIET,),
                           expiry=OPEN + timedelta(minutes=3) if state == "NO_FILL" else None)
    result = replay_us_research_trade(req)
    assert result.replay_result.state == state
    report = perf(req, result).performance
    assert report.summary.completed_count == 0 and report.summary.total_net_pnl == 0
    assert report.summary.net_expectancy is None and report.months == ()


@pytest.mark.parametrize("field", ["instrument_id", "canonical_symbol", "listing_mic"])
def test_corrupted_plan_identity_cannot_pass_original_signal_binding(field):
    req = make_request()
    value = UUID(int=99) if field == "instrument_id" else "OTHER" if field == "canonical_symbol" else "XNYS"
    with pytest.raises(ValueError, match="US7A plan"):
        replay_us_research_trade(req.model_copy(update={"plan": req.plan.model_copy(update={field: value})}))


def test_unexplained_historical_symbol_change_fails_closed():
    req = make_request(symbol_by_day=("TEST", "NEW"))
    with pytest.raises(ValueError, match="historical instrument/symbol/MIC"):
        replay_us_research_trade(req)


@pytest.mark.parametrize("field", ["listing_history", "session_history", "action_history"])
def test_execution_history_cutoff_must_be_common_and_distinct_from_strategy(field):
    req = make_request()
    history = getattr(req, field)
    changed = replace(history, decision_at=req.evidence_cutoff_at + timedelta(minutes=1))
    with pytest.raises(ValueError, match="cutoff mismatch"):
        replay_us_research_trade(req.model_copy(update={field: changed}))


def test_missing_reached_session_and_duplicate_intraday_session_fail_closed():
    req = make_request()
    with pytest.raises(ValueError, match="missing complete intraday"):
        replay_us_research_trade(req.model_copy(update={"intraday_sessions": req.intraday_sessions[:1]}))
    with pytest.raises(ValueError, match="ordered, unique"):
        replay_us_research_trade(req.model_copy(update={"intraday_sessions": (req.intraday_sessions[0],) * 2}))


def test_action_coverage_hole_and_wrong_stable_identity_fail_closed():
    req = make_request()
    bad = replace(req.action_history, coverage_start=DAY - timedelta(days=1))
    with pytest.raises(ValueError, match="gap"):
        replay_us_research_trade(req.model_copy(update={"action_history": bad}))
    bad = replace(req.action_history, instrument_id=UUID(int=99))
    with pytest.raises(ValueError, match="instrument_id"):
        replay_us_research_trade(req.model_copy(update={"action_history": bad}))


def test_semantic_identity_changes_with_terms_configuration_checkpoint_and_evidence():
    req = make_request()
    original = req.identity
    assert req.model_copy(update={"execution_config": req.execution_config.model_copy(update={"target_slippage_bps": D(1)})}).identity != original
    assert req.model_copy(update={"checkpoints": req.checkpoints[-1:]}).identity != original
    assert req.model_copy(update={"terms": req.terms.model_copy(update={"planning_rule_version": "changed"})}).identity != original
    changed = make_request(chunks=True)
    assert changed.identity != original


def test_model_construct_and_result_provenance_corruption_rejected():
    req = make_request()
    constructed = USPaperReplayRequest.model_construct(**{
        **{name: getattr(req, name) for name in USPaperReplayRequest.model_fields}, "signal": {},
    })
    with pytest.raises(ValueError):
        replay_us_research_trade(constructed)
    result = replay_us_research_trade(req)
    forged = result.model_copy(update={"provenance": result.provenance.model_copy(update={"bar_lineage": ()})})
    with pytest.raises(ValueError, match="reconstruction mismatch"):
        build_us_replay_performance_observation(req, forged)


def test_no_float_conversion_and_m7_observation_preserves_risk_regime():
    req = make_request(regime=MarketRegimeType.NEUTRAL)
    result = replay_us_research_trade(req)
    obs = build_us_replay_performance_observation(req, result)
    assert obs.market_regime == MarketRegimeType.NEUTRAL
    assert all(type(bar.volume) is D and bar.volume.as_tuple() == req.intraday_sessions[i].bars[j].volume.as_tuple()
               for i, day in enumerate(result.replay_input.calendar_days) for j, bar in enumerate(day.bars))


def test_existing_session_identity_rejects_closed_and_is_independent_of_artifact_sources():
    req, multi = make_request(), make_request(chunks=True)
    assert derive_us_replay_session_id(req.session_history.facts[0].session) == derive_us_replay_session_id(multi.session_history.facts[0].session)
    closed = USSessionRecord(market_date=DAY, calendar_mic="XNAS", state=USSessionState.CLOSED)
    with pytest.raises(ValueError, match="no replay session_id"):
        derive_us_replay_session_id(closed)


def test_scheduled_exit_respects_next_open_and_gap_stop_priority():
    stop_rows = (("8", "10", "8", "9"),) * 3
    req = make_request(rows=(ENTRY, stop_rows), cfg=execution_config(session_end_at=OPEN + timedelta(minutes=3)))
    result = replay_us_research_trade(req)
    assert result.replay_result.exit_reason == "STOP"
    assert result.replay_result.position.exit.raw_price == D(8)
    req = make_request(rows=(ENTRY, ENTRY), cfg=execution_config(time_exit_at=OPEN + timedelta(minutes=3)))
    result = replay_us_research_trade(req)
    assert result.replay_result.exit_reason == "TIME_EXIT"
    assert result.replay_result.position.exit.market_date == DAY + timedelta(days=1)


def test_later_builds_of_all_admitted_histories_preserve_semantic_identity():
    req = make_request()
    later = req.model_copy(update={
        "research_built_at": BUILD + timedelta(days=1),
        **{name: replace(getattr(req, name), research_built_at=BUILD + timedelta(days=1))
           for name in ("listing_history", "session_history", "action_history")},
        "intraday_sessions": tuple(replace(row, research_built_at=BUILD + timedelta(days=1)) for row in req.intraday_sessions),
    })
    assert later.identity == req.identity
    assert replay_us_research_trade(later) == replay_us_research_trade(req)


def test_no_later_session_is_needed_after_proven_terminal_exit():
    req = make_request(rows=(TARGET, TARGET), actions=(action(USCorporateActionType.DELISTING, DAY + timedelta(days=1)),))
    req = req.model_copy(update={"intraday_sessions": req.intraday_sessions[:1]})
    result = replay_us_research_trade(req)
    assert result.replay_result.state == "COMPLETED" and len(result.replay_input.calendar_days) == 1


@pytest.mark.parametrize("case", ["float", "bool", "decimal_scale", "unknown_field"])
def test_equal_valued_flattened_corruption_is_still_rejected(case):
    req = make_request()
    first = req.intraday_sessions[0]
    if case == "bool":
        forged = replace(first, first_session_sequence=True)
    else:
        changes = {"open": 10.0} if case == "float" else {"open": D("10.0")} if case == "decimal_scale" else {"undeclared": 1}
        forged = replace(first, bars=(first.bars[0].model_copy(update=changes), *first.bars[1:]))
    boundary = "repair forbidden" if case == "unknown_field" else "flattened bars"
    with pytest.raises(ValueError, match=boundary):
        replay_us_research_trade(req.model_copy(update={"intraday_sessions": (forged, *req.intraday_sessions[1:])}))


def test_equal_valued_result_lineage_corruption_is_rejected():
    req = make_request()
    result = replay_us_research_trade(req)
    rows = result.provenance.bar_lineage
    forged = result.model_copy(update={"provenance": result.provenance.model_copy(update={
        "bar_lineage": (rows[0].model_copy(update={"session_sequence": True}), *rows[1:]),
    })})
    with pytest.raises(ValueError):
        build_us_replay_performance_observation(req, forged)
