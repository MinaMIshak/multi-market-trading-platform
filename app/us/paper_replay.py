"""US7C-B fail-closed adapter from admitted US evidence to shared paper replay."""
from datetime import timedelta
from decimal import Context, localcontext
from zoneinfo import ZoneInfo

from app.data.models import BarGranularity
from app.domain.enums import RiskDecisionType
from app.paper.replay import simulate_paper_replay
from app.paper.replay_models import PaperReplayBar, PaperReplayCalendarDay, PaperReplayInput
from app.performance.replay_models import ReplayPerformanceObservation
from app.us.contracts import USCorporateActionType, USSessionRecord, USSessionState, US_MARKET_TIMEZONE
from app.us.historical_actions import admit_us_corporate_action_history, resolve_us_corporate_actions_on_date
from app.us.historical_identity import admit_us_listing_history
from app.us.historical_intraday import admit_us_intraday_session, USIntradayCoverageMode
from app.us.historical_session import admit_us_session_history, resolve_us_session_on_date
from app.us.research_planning import (
    admit_us_research_risk, build_us_research_trade_plan,
    materialize_legacy_risk_decision, materialize_legacy_trade_plan,
)
from .paper_replay_models import (
    USPaperReplayRequest, USPaperReplayResult, USReplayBarProvenance,
    USReplayProvenance, canonical, exact_equal, exact_utc, semantic_hash,
)


_WIDTH_SECONDS = {BarGranularity.M1: 60, BarGranularity.M5: 300}


def derive_us_replay_session_id(session: USSessionRecord) -> str:
    session = canonical(session, USSessionRecord)
    if session.state == USSessionState.CLOSED:
        raise ValueError("CLOSED date has no replay session_id")
    return semantic_hash({
        "schema_version": "us-replay-session-id-v1",
        "market": session.market, "calendar_mic": session.calendar_mic,
        "market_date": session.market_date.isoformat(),
        "timezone_name": session.timezone_name, "state": session.state.value,
        "opens_at_utc": session.opens_at_utc.isoformat(),
        "closes_at_utc": session.closes_at_utc.isoformat(),
    })


def _same(rebuilt, supplied, label):
    # Compare complete contents: admitted-session identity alone omits its
    # separately stored flattened bars and cannot detect dataclass corruption.
    if not exact_equal(rebuilt, supplied):
        raise ValueError(f"noncanonical {label}; reconstruction mismatch")
    return rebuilt


def _revalidate(request):
    request = canonical(request, USPaperReplayRequest)
    if request.research_built_at < request.evidence_cutoff_at:
        raise ValueError("research build precedes execution evidence cutoff")
    if request.risk_admission.risk_decision_at > request.evidence_cutoff_at:
        raise ValueError("risk admission is after execution evidence cutoff")
    plan = build_us_research_trade_plan(request.signal, request.terms)
    _same(plan, request.plan, "US7A plan")
    admission = admit_us_research_risk(
        plan, policy=request.policy, snapshot=request.risk_snapshot,
        decision_at=request.risk_admission.risk_decision_at,
        trade_state=request.risk_admission.trade_state,
    )
    _same(admission, request.risk_admission, "US7A risk admission")
    # Regime is bound by the original snapshot identity and recomputation above.
    # The original signal is canonical upstream US6 evidence. Never call US6
    # evaluation using the later execution listing/session/action histories.
    listing = request.listing_history
    _same(admit_us_listing_history(
        listing.facts, decision_at=listing.decision_at,
        research_built_at=listing.research_built_at,
    ), listing, "listing history")
    sessions = request.session_history
    _same(admit_us_session_history(
        sessions.facts, calendar_mic=sessions.calendar_mic,
        coverage_start=sessions.coverage_start, coverage_end=sessions.coverage_end,
        decision_at=sessions.decision_at, research_built_at=sessions.research_built_at,
    ), sessions, "session history")
    actions = request.action_history
    _same(admit_us_corporate_action_history(
        actions.facts, instrument_id=actions.instrument_id,
        coverage_start=actions.coverage_start, coverage_end=actions.coverage_end,
        decision_at=actions.decision_at, research_built_at=actions.research_built_at,
    ), actions, "action history")
    for history in (listing, sessions, actions):
        if history.decision_at != request.evidence_cutoff_at:
            raise ValueError("execution history cutoff mismatch")
        if history.research_built_at > request.research_built_at:
            raise ValueError("history built after replay research build")
    if actions.instrument_id != plan.instrument_id or sessions.calendar_mic != plan.listing_mic:
        raise ValueError("stable instrument/MIC history mismatch")

    admission_date = admission.risk_decision_at.astimezone(ZoneInfo(US_MARKET_TIMEZONE)).date()
    if plan.signal_date > admission_date:
        raise ValueError("signal date follows admission local date")
    previous_date = None
    for checkpoint in request.checkpoints:
        if checkpoint.market_date < admission_date or (previous_date is not None and checkpoint.market_date <= previous_date):
            raise ValueError("checkpoints must be ordered unique local dates at/after admission")
        if not admission.risk_decision_at <= checkpoint.path_complete_through_at <= request.evidence_cutoff_at:
            raise ValueError("checkpoint outside admission/evidence bounds")
        resolve_us_session_on_date(sessions, market_date=checkpoint.market_date)
        previous_date = checkpoint.market_date
    if not sessions.coverage_start <= admission_date <= request.checkpoints[-1].market_date <= sessions.coverage_end:
        raise ValueError("session history must cover every local date in replay path")
    if not actions.coverage_start <= plan.signal_date <= request.checkpoints[-1].market_date <= actions.coverage_end:
        raise ValueError("action coverage must cover signal bridge and requested path")

    # Verify the planning symbol against exact historical identity. This is an
    # execution compatibility check, never a strategy-universe reconstruction.
    matches = tuple(f.listing for f in listing.facts if f.listing.effective_date == plan.signal_date)
    if len(matches) != 1 or (matches[0].instrument_id, matches[0].canonical_symbol, matches[0].listing_mic) != (
        plan.instrument_id, plan.canonical_symbol, plan.listing_mic,
    ):
        raise ValueError("exact signal-date listing binding unavailable/mismatched")

    previous_date = None
    for row in request.intraday_sessions:
        if not admission_date <= row.market_date <= request.checkpoints[-1].market_date or (previous_date is not None and row.market_date <= previous_date):
            raise ValueError("intraday sessions must already be ordered, unique, and inside checkpoints")
        previous_date = row.market_date
        if row.evidence_cutoff_at != request.evidence_cutoff_at or row.research_built_at > request.research_built_at:
            raise ValueError("intraday cutoff/build mismatch")
        rebuilt = admit_us_intraday_session(
            row.facts, instrument_id=row.instrument_id, calendar_mic=row.calendar_mic,
            market_date=row.market_date, granularity=row.granularity,
            coverage_mode=row.coverage_mode, listing_history=listing,
            session_history=sessions, evidence_cutoff_at=row.evidence_cutoff_at,
            research_built_at=row.research_built_at,
        )
        _same(rebuilt, row, "intraday session including flattened bars")
    for at in (request.execution_config.time_exit_at, request.execution_config.session_end_at):
        if at is not None:
            exact_utc(at)
    return request


def _run(request):
    p, r = request.plan, request.risk_admission
    days, checkpoints, session_fact_ids, intraday_ids, proof_ids, lineage = [], [], [], [], [], []
    checked_dates, checked_ids = [], []

    def result(*, code=None, action_ids=(), inp=None, replay=None):
        return USPaperReplayResult(
            status="INCOMPATIBLE" if code else "REPLAYED", rejection_code=code,
            rejection_action_ids=action_ids, replay_input=inp, replay_result=replay,
            provenance=USReplayProvenance(
                request_id=request.identity, consumed_checkpoints=tuple(checkpoints),
                session_fact_ids=tuple(session_fact_ids), intraday_session_ids=tuple(intraday_ids),
                full_session_proof_ids=tuple(proof_ids), bar_lineage=tuple(lineage),
                checked_action_dates=tuple(checked_dates), checked_action_ids=tuple(checked_ids),
            ),
        )

    if not p.created_at <= r.risk_decision_at < p.valid_until:
        return result(code="ADMISSION_OUTSIDE_PLAN_VALIDITY")
    if not p.stop_price < p.entry_low <= p.entry_high < p.target_1:
        return result(code="UNSUPPORTED_ENTRY_GEOMETRY")

    def gate(day):
        if day in checked_dates or r.decision == RiskDecisionType.BLOCK:
            return ()
        actions = resolve_us_corporate_actions_on_date(request.action_history, market_date=day)
        checked_dates.append(day)
        checked_ids.extend(action.event_id for action in actions)
        # CASH_DIVIDEND is price-only: no quantity/price/cash transform.
        return tuple(action.event_id for action in actions
                     if action.action_type != USCorporateActionType.CASH_DIVIDEND)

    admission_date = r.risk_decision_at.astimezone(ZoneInfo(US_MARKET_TIMEZONE)).date()
    bridge_day = p.signal_date
    while bridge_day <= admission_date:
        blocked = gate(bridge_day)
        if blocked:
            return result(code="UNSUPPORTED_CORPORATE_ACTION", action_ids=blocked)
        bridge_day += timedelta(days=1)

    by_date = {row.market_date: row for row in request.intraday_sessions}
    checkpoints_by_date = {cp.market_date: cp for cp in request.checkpoints}
    inp = replay = None
    # Checkpoint dates may be sparse, but calendar dates may never be missing.
    # This permits later-known bars without pretending an earlier prefix was
    # already knowable. An intervening unsupported action still fails closed.
    for fact in request.session_history.facts:
        market_date = fact.session.market_date
        if not admission_date <= market_date <= request.checkpoints[-1].market_date:
            continue
        blocked = gate(market_date)
        if blocked:
            return result(code="UNSUPPORTED_CORPORATE_ACTION", action_ids=blocked)
        session = resolve_us_session_on_date(request.session_history, market_date=market_date)
        session_fact_ids.append(fact.identity)
        if session.state == USSessionState.CLOSED:
            if market_date in by_date:
                raise ValueError("CLOSED date cannot contain intraday truth")
            day = PaperReplayCalendarDay(market_date=market_date, state="CLOSED")
        else:
            if market_date not in by_date:
                raise ValueError("missing complete intraday session for reached trading date")
            original = by_date[market_date]
            # This certifies FULL_SESSION even for an explicitly supplied
            # full-span BOUNDED_WINDOW. Partial windows cannot pass this gate.
            full = admit_us_intraday_session(
                original.facts, instrument_id=original.instrument_id,
                calendar_mic=original.calendar_mic, market_date=original.market_date,
                granularity=original.granularity, coverage_mode=USIntradayCoverageMode.FULL_SESSION,
                listing_history=request.listing_history, session_history=request.session_history,
                evidence_cutoff_at=original.evidence_cutoff_at, research_built_at=original.research_built_at,
            )
            if (full.instrument_id, full.canonical_symbol, full.calendar_mic) != (
                p.instrument_id, p.canonical_symbol, p.listing_mic,
            ):
                raise ValueError("historical instrument/symbol/MIC does not match plan")
            width = _WIDTH_SECONDS[full.granularity]
            entry_boundaries = (r.risk_decision_at,)
            if replay is None or replay.state == "INCOMPLETE":
                entry_boundaries += (p.valid_until,)
            if r.decision == RiskDecisionType.BLOCK:
                entry_boundaries = ()
            for at in entry_boundaries:
                if session.opens_at_utc < at < session.closes_at_utc:
                    if (at - session.opens_at_utc) % timedelta(seconds=width):
                        return result(code="ENTRY_BOUNDARY_BISECTS_BAR")
            session_id = derive_us_replay_session_id(session)
            bars = []
            for segment_fact in full.facts:
                for bar in segment_fact.bars:
                    bars.append(PaperReplayBar(
                        instrument_id=str(bar.instrument_id), venue_id=bar.calendar_mic,
                        symbol=bar.canonical_symbol, session_id=session_id,
                        market_date=bar.market_date, session_sequence=bar.session_sequence,
                        interval_start_utc=bar.interval_start_utc, interval_end_utc=bar.interval_end_utc,
                        available_at_utc=bar.available_at_utc, open=bar.open, high=bar.high,
                        low=bar.low, close=bar.close, volume=bar.volume, traded_value=bar.traded_value,
                        source_id=bar.source_sha256, provenance_id=bar.provenance_id,
                    ))
                    lineage.append(USReplayBarProvenance(
                        session_id=session_id, session_sequence=bar.session_sequence,
                        source_id=bar.source_sha256, provenance_id=bar.provenance_id,
                        source_provider=bar.source_provider, provider_symbol=bar.provider_symbol,
                        source_instrument_key=bar.source_instrument_key, source_row_number=bar.source_row_number,
                        segment_fact_id=segment_fact.identity, evidence_package_id=segment_fact.evidence_package.identity,
                    ))
            day = PaperReplayCalendarDay(
                market_date=session.market_date, state="TRADING", session_id=session_id,
                opens_at_utc=session.opens_at_utc, closes_at_utc=session.closes_at_utc,
                granularity_seconds=width, bars=tuple(bars),
            )
            intraday_ids.append(original.identity)
            proof_ids.append(full.identity)
        days.append(day)
        if market_date not in checkpoints_by_date:
            continue
        checkpoint = checkpoints_by_date[market_date]
        checkpoints.append(checkpoint)
        inp = PaperReplayInput(
            trade_plan=materialize_legacy_trade_plan(p), risk_decision=materialize_legacy_risk_decision(r),
            config=request.execution_config, instrument_id=str(p.instrument_id), venue_id=p.listing_mic,
            market_timezone_name=session.timezone_name, admission_time=r.risk_decision_at,
            calendar_days=tuple(days), path_complete_through_at=checkpoint.path_complete_through_at,
        )
        replay = simulate_paper_replay(inp)
        if replay.state in ("COMPLETED", "NO_FILL", "REJECTED"):
            break
    return result(inp=inp, replay=replay)


def replay_us_research_trade(request: USPaperReplayRequest) -> USPaperReplayResult:
    """Revalidate US evidence and replay only action-compatible complete prefixes."""
    with localcontext(Context(prec=34)):
        return _run(_revalidate(request))


def build_us_paper_replay_input(request: USPaperReplayRequest) -> PaperReplayInput:
    """Return the exact accepted prefix; never bypass the US compatibility gate."""
    result = replay_us_research_trade(request)
    if result.status != "REPLAYED":
        raise ValueError(f"US replay incompatible: {result.rejection_code}; actions={result.rejection_action_ids}")
    return result.replay_input


def build_us_replay_performance_observation(
    request: USPaperReplayRequest, result: USPaperReplayResult,
) -> ReplayPerformanceObservation:
    """Run independently for every scenario before shared M7.1 admission.

    US INCOMPATIBLE is an explicit exclusion, never a fake shared NO_FILL or
    REJECTED result. A scenario may reach an action that its baseline did not.
    """
    with localcontext(Context(prec=34)):
        request = _revalidate(request)
        result = canonical(result, USPaperReplayResult)
        expected = _run(request)
        _same(expected, result, "US replay result/provenance")
        if result.status != "REPLAYED":
            raise ValueError(f"US replay incompatible: {result.rejection_code}; actions={result.rejection_action_ids}")
        return ReplayPerformanceObservation(
            replay_input=result.replay_input, replay_result=result.replay_result,
            strategy_id=request.plan.strategy_id, strategy_version=request.plan.strategy_version,
            market_regime=request.risk_snapshot.market_regime,
        )
