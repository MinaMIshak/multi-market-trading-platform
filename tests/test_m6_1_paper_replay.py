"""M6.1 multi-session replay engineering fixtures only."""
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from uuid import UUID

import pytest

from app.domain.enums import RiskDecisionType, SignalDirection
from app.domain.models import RiskDecision, TradePlan
from app.paper.models import PaperExecutionConfig
from app.paper.replay import simulate_paper_replay
from app.paper.replay_models import (
    PaperReplayBar,
    PaperReplayCalendarDay,
    PaperReplayInput,
)


D = Decimal

TZ = "America/New_York"

INSTRUMENT = "fixture-instrument"
VENUE = "XNAS"
SYMBOL = "SWDY"

DAY1 = date(2024, 7, 8)
DAY2 = date(2024, 7, 9)

OPEN1 = datetime(
    2024, 7, 8, 13, 30,
    tzinfo=timezone.utc,
)
OPEN2 = datetime(
    2024, 7, 9, 13, 30,
    tzinfo=timezone.utc,
)


def plan(
    *,
    admission=OPEN1,
    valid_until=None,
    **changes,
):
    if valid_until is None:
        valid_until = (
            admission
            + timedelta(days=1, minutes=3)
        )

    values = dict(
        trade_plan_id=UUID(int=1),
        signal_id=UUID(int=2),
        symbol=SYMBOL,
        direction=SignalDirection.LONG,
        entry_low=D("10"),
        entry_high=D("11"),
        entry_reference=D("10"),
        stop_price=D("9"),
        target_1=D("12"),
        target_2=None,
        target_3=None,
        valid_until=valid_until,
        created_at=(
            admission
            - timedelta(minutes=1)
        ),
    )
    values.update(changes)
    return TradePlan(**values)


def risk(
    p,
    *,
    decision=RiskDecisionType.APPROVE,
    quantity=10,
    approved_risk=D("20"),
):
    if decision == RiskDecisionType.BLOCK:
        quantity = 0
        approved_risk = D(0)

    return RiskDecision(
        policy_version="fixture-v1",
        policy_identity="fixture-policy",
        quantity_caps={},
        risk_decision_id=UUID(int=3),
        trade_plan_id=p.trade_plan_id,
        decision=decision,
        account_equity=D("100000"),
        risk_budget=D("100"),
        approved_risk=approved_risk,
        quantity=quantity,
        max_position_value=D("10000"),
        portfolio_exposure_pct=D("0.10"),
        daily_realized_r=D(0),
        reasons=[],
        blockers=[],
    )


def config(**changes):
    values = dict(
        config_version="paper-execution-v1",
        entry_slippage_bps=D(0),
        stop_slippage_bps=D(0),
        target_slippage_bps=D(0),
        scheduled_exit_slippage_bps=D(0),
        cost_bps_per_side=D(0),
        fixed_cost_per_side=D(0),
        max_volume_participation_pct=None,
        profit_target="TARGET_1",
        time_exit_at=None,
        session_end_at=None,
    )
    values.update(changes)
    return PaperExecutionConfig(**values)


def bar(
    *,
    start,
    sequence,
    session_id,
    market_date,
    o="13",
    h="14",
    l="13",
    c="13",
    available_at=None,
    source_id="source-a",
    provenance_id="prov-a",
    instrument_id=INSTRUMENT,
    venue_id=VENUE,
    symbol=SYMBOL,
):
    end = start + timedelta(minutes=1)

    return PaperReplayBar(
        instrument_id=instrument_id,
        venue_id=venue_id,
        symbol=symbol,
        session_id=session_id,
        market_date=market_date,
        session_sequence=sequence,
        interval_start_utc=start,
        interval_end_utc=end,
        available_at_utc=(
            end
            if available_at is None
            else available_at
        ),
        open=D(o),
        high=D(h),
        low=D(l),
        close=D(c),
        volume=D("1000"),
        traded_value=D("10000"),
        source_id=source_id,
        provenance_id=provenance_id,
    )


def trading_day(
    *,
    market_date,
    opened_at,
    rows,
    session_id,
    sources=None,
):
    rows = tuple(rows)

    if sources is None:
        sources = tuple(
            ("source-a", "prov-a")
            for _ in rows
        )

    bars = tuple(
        bar(
            start=(
                opened_at
                + timedelta(minutes=i)
            ),
            sequence=i + 1,
            session_id=session_id,
            market_date=market_date,
            o=row[0],
            h=row[1],
            l=row[2],
            c=row[3],
            source_id=sources[i][0],
            provenance_id=sources[i][1],
        )
        for i, row in enumerate(rows)
    )

    return PaperReplayCalendarDay(
        market_date=market_date,
        state="TRADING",
        session_id=session_id,
        opens_at_utc=opened_at,
        closes_at_utc=(
            opened_at
            + timedelta(minutes=len(rows))
        ),
        granularity_seconds=60,
        bars=bars,
    )


def closed_day(market_date):
    return PaperReplayCalendarDay(
        market_date=market_date,
        state="CLOSED",
    )


def replay_request(
    days,
    *,
    p=None,
    admission=OPEN1,
    path_complete_through_at=None,
    cfg=None,
    r=None,
):
    days = tuple(days)

    if p is None:
        p = plan(admission=admission)

    if r is None:
        r = risk(p)

    if cfg is None:
        cfg = config()

    if path_complete_through_at is None:
        last = days[-1]
        if last.state == "TRADING":
            path_complete_through_at = last.closes_at_utc
        else:
            path_complete_through_at = datetime(
                last.market_date.year,
                last.market_date.month,
                last.market_date.day,
                16,
                0,
                tzinfo=timezone.utc,
            )

    return PaperReplayInput(
        trade_plan=p,
        risk_decision=r,
        config=cfg,
        instrument_id=INSTRUMENT,
        venue_id=VENUE,
        market_timezone_name=TZ,
        admission_time=admission,
        calendar_days=days,
        path_complete_through_at=path_complete_through_at,
    )


def quiet_day1():
    return trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
        ),
    )


def quiet_day2():
    return trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
        ),
    )


def entry_day1():
    return trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("10", "11", "10", "10"),
            ("10", "11", "9.5", "10"),
            ("10", "11", "9.5", "10"),
        ),
    )


def test_single_session_close_does_not_auto_liquidate():
    p = plan(
        valid_until=(
            OPEN1 + timedelta(minutes=3)
        )
    )

    result = simulate_paper_replay(
        replay_request(
            (entry_day1(),),
            p=p,
            path_complete_through_at=(
                OPEN1 + timedelta(minutes=3)
            ),
        )
    )

    assert result.state == "OPEN"
    assert result.position.entry.session_id == "s1"
    assert result.position.exit is None
    assert result.metrics is None


def test_valid_until_is_entry_deadline_not_position_liquidation():
    p = plan(
        valid_until=(
            OPEN1 + timedelta(minutes=3)
        )
    )

    day2 = trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("8", "10", "8", "9"),
            ("9", "10", "9", "9"),
            ("9", "10", "9", "9"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (
                entry_day1(),
                day2,
            ),
            p=p,
            path_complete_through_at=(
                OPEN2 + timedelta(minutes=3)
            ),
        )
    )

    assert result.state == "COMPLETED"
    assert result.outcome == "LOSS"
    assert result.exit_reason == "STOP"

    assert result.position.entry.session_id == "s1"
    assert result.position.entry.bar_sequence == 1

    assert result.position.exit.session_id == "s2"
    assert result.position.exit.bar_sequence == 1
    assert result.position.exit.raw_price == D("8")


def test_pending_entry_can_fill_on_later_session():
    day2 = trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("10.5", "11", "10", "10.5"),
            ("10.5", "11", "10", "10.5"),
            ("10.5", "11", "10", "10.5"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (
                quiet_day1(),
                day2,
            )
        )
    )

    assert result.state == "OPEN"
    assert result.position.entry.session_id == "s2"
    assert result.position.entry.bar_sequence == 1
    assert result.position.entry.raw_price == D("10.5")


def test_no_fill_requires_path_through_valid_until():
    p = plan(
        valid_until=(
            OPEN2 + timedelta(minutes=3)
        )
    )

    incomplete = simulate_paper_replay(
        replay_request(
            (quiet_day1(),),
            p=p,
            path_complete_through_at=(
                OPEN1 + timedelta(minutes=3)
            ),
        )
    )

    assert incomplete.state == "INCOMPLETE"

    complete = simulate_paper_replay(
        replay_request(
            (
                quiet_day1(),
                quiet_day2(),
            ),
            p=p,
            path_complete_through_at=(
                OPEN2 + timedelta(minutes=3)
            ),
        )
    )

    assert complete.state == "NO_FILL"
    assert complete.outcome == "NO_FILL"


def test_explicit_closed_weekend_can_prove_no_fill():
    friday = date(2024, 7, 12)
    saturday = date(2024, 7, 13)
    sunday = date(2024, 7, 14)

    opened = datetime(
        2024, 7, 12, 13, 30,
        tzinfo=timezone.utc,
    )

    admission = opened

    friday_session = trading_day(
        market_date=friday,
        opened_at=opened,
        session_id="fri",
        rows=(
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
        ),
    )

    expiry = datetime(
        2024, 7, 14, 16, 0,
        tzinfo=timezone.utc,
    )

    p = plan(
        admission=admission,
        valid_until=expiry,
    )

    result = simulate_paper_replay(
        replay_request(
            (
                friday_session,
                closed_day(saturday),
                closed_day(sunday),
            ),
            p=p,
            admission=admission,
            path_complete_through_at=expiry,
        )
    )

    assert result.state == "NO_FILL"


def test_explicit_session_end_boundary_exits_at_next_tradable_open():
    boundary = OPEN1 + timedelta(minutes=3)

    day2 = trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("10.5", "11", "10", "10.5"),
            ("10.5", "11", "10", "10.5"),
            ("10.5", "11", "10", "10.5"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (
                entry_day1(),
                day2,
            ),
            cfg=config(
                session_end_at=boundary
            ),
        )
    )

    assert result.state == "COMPLETED"
    assert result.exit_reason == "SESSION_END"
    assert result.outcome == "TIME_EXIT"
    assert result.position.exit.session_id == "s2"
    assert result.position.exit.at_open is True


def test_open_gap_precedes_scheduled_boundary():
    boundary = OPEN1 + timedelta(minutes=3)

    day2 = trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("8", "10", "8", "9"),
            ("9", "10", "9", "9"),
            ("9", "10", "9", "9"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (
                entry_day1(),
                day2,
            ),
            cfg=config(
                session_end_at=boundary
            ),
        )
    )

    assert result.exit_reason == "STOP"
    assert result.outcome == "LOSS"
    assert result.position.exit.raw_price == D("8")


def test_calendar_days_are_not_sorted_or_repaired():
    with pytest.raises(
        ValueError,
        match="begin on admission local date|gap-free",
    ):
        replay_request(
            (
                quiet_day2(),
                quiet_day1(),
            )
        )


def test_missing_calendar_day_is_rejected():
    day3_date = date(2024, 7, 10)
    open3 = datetime(
        2024, 7, 10, 13, 30,
        tzinfo=timezone.utc,
    )

    day3 = trading_day(
        market_date=day3_date,
        opened_at=open3,
        session_id="s3",
        rows=(
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
        ),
    )

    with pytest.raises(
        ValueError,
        match="gap-free",
    ):
        replay_request(
            (
                quiet_day1(),
                day3,
            ),
            path_complete_through_at=(
                open3 + timedelta(minutes=3)
            ),
        )


def test_trading_day_requires_opening_sequence_one():
    good = quiet_day1()

    bad_first = good.bars[0].model_copy(
        update={
            "session_sequence": 2,
        }
    )

    corrupted = good.model_copy(
        update={
            "bars": (
                bad_first,
                *good.bars[1:],
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="sequence",
    ):
        replay_request(
            (corrupted,)
        )


def test_truncated_session_is_rejected():
    good = quiet_day1()

    corrupted = good.model_copy(
        update={
            "bars": good.bars[:-1],
        }
    )

    with pytest.raises(
        ValueError,
        match="bar count|session close",
    ):
        replay_request(
            (corrupted,)
        )


def test_source_and_provenance_may_change_inside_complete_session():
    day = trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
            ("13", "14", "13", "13"),
        ),
        sources=(
            ("sha-a", "chunk-a"),
            ("sha-b", "chunk-b"),
            ("sha-b", "chunk-b"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (day,),
            p=plan(
                valid_until=(
                    OPEN1
                    + timedelta(minutes=3)
                )
            ),
        )
    )

    assert result.state == "NO_FILL"


def test_bar_unavailable_by_replay_cutoff_is_rejected():
    good = quiet_day1()

    delayed = good.bars[-1].model_copy(
        update={
            "available_at_utc": (
                good.closes_at_utc
                + timedelta(seconds=1)
            ),
        }
    )

    corrupted = good.model_copy(
        update={
            "bars": (
                *good.bars[:-1],
                delayed,
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="unavailable by",
    ):
        replay_request(
            (corrupted,),
            path_complete_through_at=(
                good.closes_at_utc
            ),
        )


def test_exact_utc_is_required():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        ValueError,
        match="datetime.timezone.utc",
    ):
        bar(
            start=OPEN1.replace(
                tzinfo=zero
            ),
            sequence=1,
            session_id="s1",
            market_date=DAY1,
        )


def test_block_has_no_execution_artifacts():
    p = plan(
        valid_until=(
            OPEN1 + timedelta(minutes=3)
        )
    )

    result = simulate_paper_replay(
        replay_request(
            (quiet_day1(),),
            p=p,
            r=risk(
                p,
                decision=RiskDecisionType.BLOCK,
            ),
        )
    )

    assert result.state == "REJECTED"
    assert result.rejection_reason == "M5_BLOCK"
    assert result.position is None
    assert result.metrics is None
    assert result.outcome is None


def test_nested_dictionary_substitution_fails_closed():
    request = replay_request(
        (quiet_day1(),)
    )

    corrupted = request.model_copy(
        update={
            "calendar_days": (
                request.calendar_days[0].model_dump(
                    mode="python"
                ),
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="canonical PaperReplayCalendarDay",
    ):
        simulate_paper_replay(
            corrupted
        )


def test_decimal_context_does_not_change_result():
    request = replay_request(
        (
            entry_day1(),
            trading_day(
                market_date=DAY2,
                opened_at=OPEN2,
                session_id="s2",
                rows=(
                    ("10", "12", "10", "12"),
                    ("12", "12", "11", "12"),
                    ("12", "12", "11", "12"),
                ),
            ),
        ),
        cfg=config(
            target_slippage_bps=D("12.5"),
            cost_bps_per_side=D("7.5"),
            fixed_cost_per_side=D("1.25"),
        ),
    )

    expected = simulate_paper_replay(
        request
    )

    with localcontext(Context(prec=6)):
        assert simulate_paper_replay(
            request
        ) == expected


def test_future_sessions_do_not_change_terminal_result():
    target_day = trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("10", "11", "10", "10"),
            ("10", "12", "10", "12"),
            ("50", "100", "1", "50"),
        ),
    )

    first = simulate_paper_replay(
        replay_request(
            (target_day,),
            p=plan(
                valid_until=(
                    OPEN2
                    + timedelta(minutes=3)
                )
            ),
        )
    )

    second = simulate_paper_replay(
        replay_request(
            (
                target_day,
                quiet_day2(),
            ),
            p=plan(
                valid_until=(
                    OPEN2
                    + timedelta(minutes=3)
                )
            ),
        )
    )

    assert first == second
    assert first.state == "COMPLETED"
    assert first.exit_reason == "TARGET_1"


def test_stable_instrument_binding_is_fail_closed():
    good = quiet_day1()

    bad_bar = good.bars[0].model_copy(
        update={
            "instrument_id": "other-instrument",
        }
    )

    corrupted = good.model_copy(
        update={
            "bars": (
                bad_bar,
                *good.bars[1:],
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="stable instrument",
    ):
        replay_request(
            (corrupted,)
        )


def test_mae_mfe_accumulate_across_sessions():
    day1 = trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("10", "11", "10", "10"),
            ("10", "11", "9.5", "10"),
            ("10", "11", "9.5", "10"),
        ),
    )

    day2 = trading_day(
        market_date=DAY2,
        opened_at=OPEN2,
        session_id="s2",
        rows=(
            ("10", "11.5", "9.7", "11"),
            ("11", "12", "10", "12"),
            ("12", "12", "11", "12"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (
                day1,
                day2,
            )
        )
    )

    assert result.state == "COMPLETED"
    assert result.exit_reason == "TARGET_1"

    assert result.metrics.mae == D("0.5")
    assert result.metrics.mfe == D("2")
    assert result.metrics.mae_r == D("0.25")
    assert result.metrics.mfe_r == D("1")


def test_entry_bar_stop_ambiguity_preserves_legacy_pessimism():
    day = trading_day(
        market_date=DAY1,
        opened_at=OPEN1,
        session_id="s1",
        rows=(
            ("13", "14", "8", "11"),
            ("11", "11", "10", "11"),
            ("11", "11", "10", "11"),
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (day,),
            p=plan(
                valid_until=(
                    OPEN1
                    + timedelta(minutes=3)
                )
            ),
        )
    )

    assert result.state == "COMPLETED"
    assert result.exit_reason == "STOP"
    assert result.position.entry.raw_price == D("11")
    assert result.position.exit.raw_price == D("9")


def test_delayed_bar_availability_is_preserved_on_fill():
    first = bar(
        start=OPEN1,
        sequence=1,
        session_id="s1",
        market_date=DAY1,
        o="10",
        h="11",
        l="10",
        c="10",
        available_at=(
            OPEN1
            + timedelta(
                minutes=1,
                seconds=10,
            )
        ),
    )

    second = bar(
        start=OPEN1 + timedelta(minutes=1),
        sequence=2,
        session_id="s1",
        market_date=DAY1,
        o="10",
        h="11",
        l="10",
        c="10",
    )

    third = bar(
        start=OPEN1 + timedelta(minutes=2),
        sequence=3,
        session_id="s1",
        market_date=DAY1,
        o="10",
        h="11",
        l="10",
        c="10",
    )

    day = PaperReplayCalendarDay(
        market_date=DAY1,
        state="TRADING",
        session_id="s1",
        opens_at_utc=OPEN1,
        closes_at_utc=(
            OPEN1 + timedelta(minutes=3)
        ),
        granularity_seconds=60,
        bars=(
            first,
            second,
            third,
        ),
    )

    result = simulate_paper_replay(
        replay_request(
            (day,),
            p=plan(
                valid_until=(
                    OPEN1
                    + timedelta(minutes=3)
                )
            ),
        )
    )

    assert (
        result.position.entry.known_at_utc
        == OPEN1
        + timedelta(
            minutes=1,
            seconds=10,
        )
    )


@pytest.mark.parametrize("through_closed_day", [False, True])
def test_cross_session_availability_reversal_fails_closed(through_closed_day):
    first = quiet_day1()
    next_open = OPEN2 + timedelta(days=int(through_closed_day))
    second = trading_day(
        market_date=next_open.date(), opened_at=next_open,
        session_id="s2", rows=(("13", "14", "13", "13"),) * 3,
    )
    delayed = first.bars[-1].model_copy(update={
        "available_at_utc": next_open + timedelta(minutes=2),
    })
    first = first.model_copy(update={"bars": (*first.bars[:-1], delayed)})
    days = (first, closed_day(DAY2), second) if through_closed_day else (first, second)
    with pytest.raises(ValueError, match="availability is nonchronological"):
        replay_request(days)


def test_cross_session_equal_availability_is_preserved_deterministically():
    first = entry_day1()
    second = quiet_day2()
    known_at = second.closes_at_utc
    days = tuple(day.model_copy(update={
        "bars": tuple(b.model_copy(update={"available_at_utc": known_at}) for b in day.bars),
    }) for day in (first, second))
    request = replay_request(days)
    result = simulate_paper_replay(request)
    assert result.state == "COMPLETED"
    assert result.position.entry.known_at_utc == known_at
    assert result.position.exit.known_at_utc == known_at
    assert simulate_paper_replay(request) == result


def test_model_copy_cannot_bypass_global_availability_validation():
    request = replay_request((quiet_day1(), quiet_day2()))
    first, second = request.calendar_days
    delayed = first.bars[-1].model_copy(update={
        "available_at_utc": second.closes_at_utc,
    })
    corrupted = request.model_copy(update={"calendar_days": (
        first.model_copy(update={"bars": (*first.bars[:-1], delayed)}), second,
    )})
    with pytest.raises(ValueError, match="availability is nonchronological"):
        simulate_paper_replay(corrupted)


def test_session_identity_cannot_be_reused_on_another_date():
    second = quiet_day2()
    second = second.model_copy(update={
        "session_id": "s1",
        "bars": tuple(b.model_copy(update={"session_id": "s1"}) for b in second.bars),
    })
    with pytest.raises(ValueError, match="duplicate replay session_id"):
        replay_request((quiet_day1(), second))


@pytest.mark.parametrize("field,value", [
    ("symbol", " swdy "), ("entry_low", 10.0), ("direction", "LONG"),
])
def test_corrupted_domain_plan_is_not_repaired(field, value):
    request = replay_request((quiet_day1(),))
    corrupted = request.model_copy(update={
        "trade_plan": request.trade_plan.model_copy(update={field: value}),
    })
    with pytest.raises(ValueError):
        simulate_paper_replay(corrupted)


def test_nonfinite_unconstrained_risk_field_fails_closed():
    request = replay_request((quiet_day1(),))
    corrupted = request.model_copy(update={
        "risk_decision": request.risk_decision.model_copy(update={"daily_realized_r": D("NaN")}),
    })
    with pytest.raises(ValueError):
        simulate_paper_replay(corrupted)


@pytest.mark.parametrize("field,value", [
    ("volume", 1000.0), ("open", 13.0),
    ("interval_start_utc", OPEN1 + timedelta(seconds=1)),
    ("interval_end_utc", OPEN1 + timedelta(seconds=59)),
    ("session_sequence", True),
])
def test_corrupted_bar_geometry_and_exact_types_fail_closed(field, value):
    day = quiet_day1()
    day = day.model_copy(update={"bars": (
        day.bars[0].model_copy(update={field: value}), *day.bars[1:],
    )})
    with pytest.raises(ValueError):
        replay_request((day,))


def test_scheduled_exit_open_target_precedes_boundary():
    request = replay_request(
        (entry_day1(), quiet_day2()), cfg=config(time_exit_at=OPEN2),
    )
    result = simulate_paper_replay(request)
    assert result.exit_reason == "TARGET_1"
    assert result.position.exit.raw_price == D("12")
    assert result.position.exit.at_open


def test_decimal_traps_rounding_and_exponent_limits_do_not_leak():
    from decimal import Inexact, ROUND_UP

    request = replay_request((entry_day1(), quiet_day2()), cfg=config(
        target_slippage_bps=D("12.34567"), cost_bps_per_side=D("7.654321"),
    ))
    expected = simulate_paper_replay(request)
    hostile = Context(prec=3, rounding=ROUND_UP, Emax=2, Emin=-2)
    hostile.traps[Inexact] = True
    with localcontext(hostile):
        assert simulate_paper_replay(request) == expected
