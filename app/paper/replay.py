"""Deterministic additive M6.1 multi-session OHLC replay.

Legacy simulate_paper() is deliberately untouched.

Session boundaries do not close Swing positions.  TradePlan.valid_until is
an entry deadline, not an automatic liquidation timestamp.  Explicit
PaperExecutionConfig time/session boundaries retain their legacy scheduled
exit semantics.
"""
from decimal import Context, Decimal, localcontext

from app.domain.enums import RiskDecisionType
from app.paper.models import PaperTradeMetrics

from .replay_models import (
    PaperReplayFill,
    PaperReplayInput,
    PaperReplayPosition,
    PaperReplayResult,
)


D = Decimal


def simulate_paper_replay(
    request: PaperReplayInput,
) -> PaperReplayResult:
    if type(request) is not PaperReplayInput:
        raise TypeError(
            "PaperReplayInput required"
        )

    # Revalidate model_copy/model_construct and every nested replay object.
    request = PaperReplayInput(
        **{
            name: getattr(request, name)
            for name in PaperReplayInput.model_fields
        }
    )

    with localcontext(Context(prec=34)):
        return _simulate_replay(request)


def _simulate_replay(
    request: PaperReplayInput,
) -> PaperReplayResult:
    p = request.trade_plan
    r = request.risk_decision
    c = request.config

    if r.decision == RiskDecisionType.BLOCK:
        return PaperReplayResult(
            state="REJECTED",
            rejection_reason="M5_BLOCK",
        )

    if (
        r.decision
        not in (
            RiskDecisionType.APPROVE,
            RiskDecisionType.REDUCE,
        )
        or r.quantity <= 0
        or r.approved_risk <= 0
    ):
        raise ValueError(
            "positive M5 admission required"
        )

    boundaries = [
        (at, reason)
        for at, reason in (
            (
                c.time_exit_at,
                "TIME_EXIT",
            ),
            (
                c.session_end_at,
                "SESSION_END",
            ),
        )
        if at is not None
    ]

    boundary = (
        min(
            boundaries,
            key=lambda item: item[0],
        )
        if boundaries
        else None
    )

    entry = None
    lowest = None
    highest = None

    def price(
        raw: Decimal,
        bps: Decimal,
        *,
        buy: bool,
    ) -> Decimal:
        value = raw * (
            D(1) + bps / D(10000)
            if buy
            else D(1) - bps / D(10000)
        )

        if (
            not value.is_finite()
            or value <= 0
        ):
            raise ValueError(
                "invalid slipped execution price"
            )

        return value

    def fill(
        bar,
        raw: Decimal,
        bps: Decimal,
        side: str,
        *,
        at_open: bool,
    ) -> PaperReplayFill:
        return PaperReplayFill(
            side=side,
            quantity=r.quantity,
            raw_price=raw,
            price=price(
                raw,
                bps,
                buy=side == "BUY",
            ),
            instrument_id=bar.instrument_id,
            venue_id=bar.venue_id,
            symbol=bar.symbol,
            session_id=bar.session_id,
            market_date=bar.market_date,
            bar_sequence=bar.session_sequence,
            interval_start_utc=bar.interval_start_utc,
            interval_end_utc=bar.interval_end_utc,
            known_at_utc=bar.available_at_utc,
            source_id=bar.source_id,
            provenance_id=bar.provenance_id,
            at_open=at_open,
        )

    for day in request.calendar_days:
        if day.state == "CLOSED":
            continue

        for bar in day.bars:
            o = bar.open
            h = bar.high
            l = bar.low
            close = bar.close

            just_entered = False

            if entry is None:
                if (
                    bar.interval_start_utc
                    < request.admission_time
                    or bar.interval_end_utc
                    > p.valid_until
                ):
                    continue

                # Preserve legacy M6 semantics: once an explicit scheduled
                # exit boundary is due, no new position may be initiated.
                if (
                    boundary is not None
                    and bar.interval_start_utc
                    >= boundary[0]
                ):
                    continue

                if (
                    l > p.entry_high
                    or h < p.entry_low
                ):
                    continue

                if (
                    c.max_volume_participation_pct
                    is not None
                ):
                    capacity = int(
                        bar.volume
                        * c.max_volume_participation_pct
                    )
                    if r.quantity > capacity:
                        continue

                raw = max(
                    p.entry_low,
                    min(
                        o,
                        p.entry_high,
                    ),
                )

                entry = fill(
                    bar,
                    raw,
                    c.entry_slippage_bps,
                    "BUY",
                    at_open=(
                        p.entry_low
                        <= o
                        <= p.entry_high
                    ),
                )

                if not (
                    p.stop_price
                    < entry.price
                    < p.target_1
                ):
                    raise ValueError(
                        "slipped entry outside stop/target geometry"
                    )

                lowest = entry.price
                highest = entry.price
                just_entered = True

            raw_exit = None
            reason = None
            at_open = False

            if not just_entered:
                lowest = min(
                    lowest,
                    o,
                )
                highest = max(
                    highest,
                    min(
                        o,
                        p.target_1,
                    ),
                )

                # Preserve legacy ordering:
                # open-gap stop/target precedes a scheduled boundary.
                if o <= p.stop_price:
                    raw_exit = o
                    reason = "STOP"
                    at_open = True
                elif o >= p.target_1:
                    raw_exit = p.target_1
                    reason = "TARGET_1"
                    at_open = True
                elif (
                    boundary is not None
                    and bar.interval_start_utc
                    >= boundary[0]
                ):
                    raw_exit = o
                    reason = boundary[1]
                    at_open = True

            if reason is None:
                if l <= p.stop_price:
                    raw_exit = p.stop_price
                    reason = "STOP"
                elif (
                    h >= p.target_1
                    and (
                        not just_entered
                        or entry.at_open
                        or close >= p.target_1
                    )
                ):
                    raw_exit = p.target_1
                    reason = "TARGET_1"

            if reason is not None:
                if reason == "STOP":
                    bps = c.stop_slippage_bps
                elif reason == "TARGET_1":
                    bps = c.target_slippage_bps
                else:
                    bps = c.scheduled_exit_slippage_bps

                exit_fill = fill(
                    bar,
                    raw_exit,
                    bps,
                    "SELL",
                    at_open=at_open,
                )

                lowest = min(
                    lowest,
                    raw_exit,
                    exit_fill.price,
                )
                highest = max(
                    highest,
                    raw_exit,
                    exit_fill.price,
                )

                entry_notional = (
                    r.quantity
                    * entry.price
                )
                exit_notional = (
                    r.quantity
                    * exit_fill.price
                )

                entry_cost = (
                    entry_notional
                    * c.cost_bps_per_side
                    / D(10000)
                    + c.fixed_cost_per_side
                )
                exit_cost = (
                    exit_notional
                    * c.cost_bps_per_side
                    / D(10000)
                    + c.fixed_cost_per_side
                )

                gross = (
                    exit_notional
                    - entry_notional
                )
                net = (
                    gross
                    - entry_cost
                    - exit_cost
                )

                mae = (
                    entry.price
                    - lowest
                )
                mfe = (
                    highest
                    - entry.price
                )

                outcome = (
                    "LOSS"
                    if reason == "STOP"
                    else "WIN"
                    if reason == "TARGET_1"
                    else "TIME_EXIT"
                )

                return PaperReplayResult(
                    state="COMPLETED",
                    outcome=outcome,
                    exit_reason=reason,
                    position=PaperReplayPosition(
                        trade_plan_id=p.trade_plan_id,
                        quantity=r.quantity,
                        entry=entry,
                        exit=exit_fill,
                        state="CLOSED",
                    ),
                    metrics=PaperTradeMetrics(
                        entry_notional=entry_notional,
                        exit_notional=exit_notional,
                        entry_cost=entry_cost,
                        exit_cost=exit_cost,
                        gross_pnl=gross,
                        net_pnl=net,
                        r_multiple=(
                            net
                            / r.approved_risk
                        ),
                        mae=mae,
                        mfe=mfe,
                        mae_r=(
                            mae
                            * r.quantity
                            / r.approved_risk
                        ),
                        mfe_r=(
                            mfe
                            * r.quantity
                            / r.approved_risk
                        ),
                    ),
                )

            lowest = min(
                lowest,
                l,
            )
            highest = max(
                highest,
                (
                    h
                    if (
                        not just_entered
                        or entry.at_open
                    )
                    else close
                ),
            )

    if entry is not None:
        return PaperReplayResult(
            state="OPEN",
            position=PaperReplayPosition(
                trade_plan_id=p.trade_plan_id,
                quantity=r.quantity,
                entry=entry,
                exit=None,
                state="OPEN",
            ),
        )

    if (
        request.path_complete_through_at
        >= p.valid_until
    ):
        return PaperReplayResult(
            state="NO_FILL",
            outcome="NO_FILL",
        )

    return PaperReplayResult(
        state="INCOMPLETE",
    )


__all__ = [
    "simulate_paper_replay",
]
