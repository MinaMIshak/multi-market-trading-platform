"""Deterministic OHLC replay. No clock, I/O, storage, random IDs or order adapter.

Entry-bar stop touches win pessimistically. For non-open entries a high alone
cannot prove a post-entry target: only close >= target proves that crossing.
Excursions are sampled price distances from slipped entry, excluding fees:
full extrema on surviving subsequent bars; entry-bar high only for open entries.
Terminal bars contribute only the existing-position open and raw/slipped exit,
never their later high/low/close. Thus excursions are a censored OHLC convention,
not a reconstruction of tick extrema. MAE is nonnegative adverse magnitude.
"""
from decimal import Context, Decimal, localcontext

from app.domain.enums import RiskDecisionType
from .models import (PaperFill, PaperPosition, PaperSimulationInput,
                     PaperSimulationResult, PaperTradeMetrics)

D = Decimal


def simulate_paper(request: PaperSimulationInput) -> PaperSimulationResult:
    if not isinstance(request, PaperSimulationInput):
        raise TypeError('PaperSimulationInput required')
    # Revalidate even model_copy/model_construct and mutated nested domain objects.
    request = PaperSimulationInput(**{name: getattr(request, name)
                                     for name in PaperSimulationInput.model_fields})
    with localcontext(Context(prec=34)):
        return _simulate(request)


def _simulate(request):
    p, r, c = request.trade_plan, request.risk_decision, request.config
    if r.decision == RiskDecisionType.BLOCK:
        return PaperSimulationResult(state='REJECTED', rejection_reason='M5_BLOCK')
    if r.decision not in (RiskDecisionType.APPROVE, RiskDecisionType.REDUCE) or r.quantity <= 0 or r.approved_risk <= 0:
        raise ValueError('positive M5 admission required')
    boundaries = [(at, reason) for at, reason in
                  ((c.time_exit_at, 'TIME_EXIT'), (c.session_end_at, 'SESSION_END')) if at is not None]
    boundary = min(boundaries, key=lambda item: item[0]) if boundaries else None
    entry = None
    lowest = highest = None

    def price(raw, bps, buy=False):
        value = raw * (1 + bps / 10000 if buy else 1 - bps / 10000)
        if not value.is_finite() or value <= 0:
            raise ValueError('invalid slipped execution price')
        return value

    def fill(bar, raw, bps, side, at_open):
        return PaperFill(side=side, quantity=r.quantity, raw_price=raw,
                         price=price(raw, bps, side == 'BUY'), bar_sequence=bar.sequence,
                         interval_start=bar.interval_start, interval_end=bar.interval_end,
                         known_at=bar.available_at, at_open=at_open)

    for bar in request.bars:
        o, h, l, close = (D(str(v)) for v in (bar.open, bar.high, bar.low, bar.close))
        just_entered = False
        if entry is None:
            if bar.interval_start < request.admission_time or bar.interval_end > p.valid_until:
                continue
            # A due explicit exit boundary prevents initiating a new position.
            if boundary and bar.interval_start >= boundary[0]:
                continue
            if l > p.entry_high or h < p.entry_low:
                continue
            if c.max_volume_participation_pct is not None:
                capacity = int(D(str(bar.volume)) * c.max_volume_participation_pct)
                if r.quantity > capacity:
                    continue
            raw = max(p.entry_low, min(o, p.entry_high))
            entry = fill(bar, raw, c.entry_slippage_bps, 'BUY', p.entry_low <= o <= p.entry_high)
            if not p.stop_price < entry.price < p.target_1:
                raise ValueError('slipped entry outside stop/target geometry')
            lowest = highest = entry.price
            just_entered = True

        raw_exit = reason = None
        at_open = False
        if not just_entered:
            lowest, highest = min(lowest, o), max(highest, min(o, p.target_1))
            if o <= p.stop_price:
                raw_exit, reason, at_open = o, 'STOP', True
            elif o >= p.target_1:
                raw_exit, reason, at_open = p.target_1, 'TARGET_1', True
            elif boundary and bar.interval_start >= boundary[0]:
                raw_exit, reason, at_open = o, boundary[1], True
        if reason is None:
            if l <= p.stop_price:
                raw_exit, reason = p.stop_price, 'STOP'
            elif h >= p.target_1 and (not just_entered or entry.at_open or close >= p.target_1):
                raw_exit, reason = p.target_1, 'TARGET_1'
        if reason is not None:
            bps = (c.stop_slippage_bps if reason == 'STOP' else
                   c.target_slippage_bps if reason == 'TARGET_1' else c.scheduled_exit_slippage_bps)
            exit_fill = fill(bar, raw_exit, bps, 'SELL', at_open)
            lowest = min(lowest, raw_exit, exit_fill.price)
            highest = max(highest, raw_exit, exit_fill.price)
            en, ex = r.quantity * entry.price, r.quantity * exit_fill.price
            ec = en * c.cost_bps_per_side / 10000 + c.fixed_cost_per_side
            xc = ex * c.cost_bps_per_side / 10000 + c.fixed_cost_per_side
            gross = ex - en
            net = gross - ec - xc
            mae, mfe = entry.price - lowest, highest - entry.price
            return PaperSimulationResult(
                state='COMPLETED', outcome='LOSS' if reason == 'STOP' else 'WIN' if reason == 'TARGET_1' else 'TIME_EXIT',
                exit_reason=reason,
                position=PaperPosition(trade_plan_id=p.trade_plan_id, quantity=r.quantity,
                                       entry=entry, exit=exit_fill, state='CLOSED'),
                metrics=PaperTradeMetrics(entry_notional=en, exit_notional=ex, entry_cost=ec,
                                         exit_cost=xc, gross_pnl=gross, net_pnl=net,
                                         r_multiple=net / r.approved_risk, mae=mae, mfe=mfe,
                                         mae_r=mae * r.quantity / r.approved_risk,
                                         mfe_r=mfe * r.quantity / r.approved_risk))
        lowest = min(lowest, l)
        highest = max(highest, h if not just_entered or entry.at_open else close)
    if entry is not None:
        return PaperSimulationResult(state='OPEN', position=PaperPosition(
            trade_plan_id=p.trade_plan_id, quantity=r.quantity, entry=entry, exit=None, state='OPEN'))
    if request.bars and request.bars[-1].interval_end >= p.valid_until:
        return PaperSimulationResult(state='NO_FILL', outcome='NO_FILL')
    return PaperSimulationResult(state='INCOMPLETE')
