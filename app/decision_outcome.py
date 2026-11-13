"""Pure per-decision outcome evaluation over session bars (decision-outcome-v1).

Phase G/H groundwork. ``evaluate_decision_outcome`` takes ONE retained
decision-observation-v1 record (bound to its content address), caller-supplied
completed session bars of the same instrument and an explicit, versioned
``OutcomeAssumptions`` (slippage and cost in basis points), and returns a
JSON-ready outcome for the record's immutable plan terms. It never edits the
observation, the decision or the plan.

Why not an existing engine: ``app.paper.replay.simulate_paper_replay`` requires a
positive M5 risk admission (quantity, approved risk) that a TradeDecision does
not have (risk_status NOT_EVALUATED); fabricating one would invent a risk
approval. ``app.paper.system_candidates.simulate`` applies the EGX-RANK-v1
two-target half-exit lifecycle with a fixed hold, not these single-target
terms. The exit ordering below follows the replay engine's conservative rules.

Point in time (fail closed with ValueError): every bar must begin at or after
the detection time, be available by ``path_complete_through_at`` and be in
strictly ascending session order; ``path_complete_through_at`` must lie between
detection and ``evaluated_at``. Bars are the caller's assertion of a complete,
raw (unadjusted) session path; calendar completeness and corporate actions are
NOT verified here and are labelled as such.

Statuses: CLOSED (STOP / TARGET / TIME_EXIT), NO_ENTRY, PENDING_PATH and
UNEVALUABLE with an explicit reason. Results are R-multiples per share using
the planned risk per share; they are hypothetical outcomes from bars, not fills,
and a single outcome is not performance. No IO, store, route, scheduler or
caller. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Literal

from pydantic import Field, field_validator

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _decimal, _time, read_retained_decision_observation
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'decision-outcome-v1'
RULES_VERSION = 'decision-outcome-rules-v1'
MAX_BARS = 400
MAX_BPS = Decimal('1000')
TOUCH = 'LONG_ENTRY_ZONE_TOUCH_V1'
NEXT_SESSION = 'EGX_RANK_V1_NEXT_SESSION_ZONE_V1'
TERM_FIELDS = frozenset((
    'entry_trigger', 'entry_low', 'entry_high', 'entry_reference', 'stop', 'target',
    'valid_until', 'max_holding_sessions', 'flat_by'))
REASONS = {
    'CLOSED': (None,),
    'NO_ENTRY': ('NO_FILL_GAP_DOWN', 'NO_FILL_GAP_UP', 'ENTRY_WINDOW_EXPIRED',
                 'NOT_TOUCHED_BEFORE_EXPIRY'),
    'PENDING_PATH': ('AWAITING_ENTRY_WINDOW', 'POSITION_OPEN'),
    'UNEVALUABLE': ('NO_SESSION_BARS_IN_ENTRY_WINDOW', 'ENTRY_OUTSIDE_STOP_TARGET',
                    'FLAT_BY_NOT_SUPPORTED_BY_SESSION_BARS', 'UNSUPPORTED_ENTRY_TRIGGER'),
}
RULES = {
    'entry_touch': 'FIRST_SESSION_ENDING_BY_VALID_UNTIL_TRADING_IN_ZONE_FILLS_AT_OPEN_CLAMPED_TO_ZONE',
    'entry_next_session': 'FIRST_SESSION_ONLY_OPEN_IN_ZONE_AT_OPEN_ABOVE_AT_ZONE_HIGH_IF_TRADED_BELOW_GAP_DOWN',
    'same_session': 'STOP_BEFORE_TARGET',
    'fill_session_target': 'ONLY_IF_FILLED_AT_OPEN_OR_CLOSE_AT_OR_ABOVE_TARGET',
    'gaps': 'STOP_AT_OPEN_TARGET_CAPPED_AT_TARGET',
    'time_exit': 'CLOSE_OF_MAX_HOLDING_SESSION_COUNTING_FILL_SESSION',
    'holding_after_valid_until': 'VALID_UNTIL_IS_ENTRY_DEADLINE_ONLY',
    'flat_by': 'UNEVALUABLE_WITH_SESSION_BARS',
    'r_unit': 'PLANNED_RISK_PER_SHARE',
    'costs': 'BPS_PER_SIDE_ON_SLIPPED_PRICES',
    'excursion': 'CONSERVATIVE_SESSION_EXTREMES_V1',
}


class OutcomeAssumptions(Contract):
    """Explicit, versioned per-market execution assumptions; declared, not validated."""

    schema_version: Literal['decision-outcome-assumptions-v1'] = 'decision-outcome-assumptions-v1'
    assumptions_version: str = Field(min_length=1, pattern=r'^\S+$')
    market: Literal['EGX', 'US']
    entry_slippage_bps: Decimal = Field(ge=0, le=MAX_BPS)
    exit_slippage_bps: Decimal = Field(ge=0, le=MAX_BPS)
    cost_bps_per_side: Decimal = Field(ge=0, le=MAX_BPS)
    basis: Literal['DECLARED_UNVALIDATED'] = 'DECLARED_UNVALIDATED'

    @field_validator('entry_slippage_bps', 'exit_slippage_bps', 'cost_bps_per_side', mode='before')
    @classmethod
    def exact_decimal(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError('finite exact Decimal required')
        return value


def _bar_ref(bar):
    return {'market_date': bar.market_date.isoformat(), 'session_id': bar.session_id,
            'source_id': bar.source_id, 'provenance_id': bar.provenance_id,
            'available_at': _utc(bar.available_at_utc)}


def _bars(bars, record, detected, complete):
    if type(bars) is not tuple or len(bars) > MAX_BARS or any(
            type(bar) is not PaperReplayBar for bar in bars):
        raise ValueError(f'tuple of at most {MAX_BARS} exact PaperReplayBar required')
    # Revalidate: model_copy/model_construct bypass checks.
    bars = tuple(PaperReplayBar(**{name: getattr(bar, name) for name in PaperReplayBar.model_fields})
                 for bar in bars)
    previous = None
    for bar in bars:
        if (bar.instrument_id, bar.symbol) != (record['instrument_id'], record['symbol']):
            raise ValueError('bar instrument or symbol mismatch')
        if bar.interval_start_utc < detected:
            raise ValueError('bar begins before detection')
        if bar.available_at_utc > complete:
            raise ValueError('bar not available by path_complete_through_at')
        if previous is not None and (
                bar.interval_start_utc < previous.interval_end_utc
                or bar.market_date <= previous.market_date
                or bar.available_at_utc < previous.available_at_utc):
            raise ValueError('bars must be strictly ascending sessions')
        previous = bar
    return bars


def _entry(bars, trigger, low, high, valid_until, complete):
    """(index, raw, at_open) of the fill, or (status, reason, bars_used)."""
    if trigger == NEXT_SESSION:
        if not bars:
            if complete < valid_until:
                return 'PENDING_PATH', 'AWAITING_ENTRY_WINDOW', []
            return 'UNEVALUABLE', 'NO_SESSION_BARS_IN_ENTRY_WINDOW', []
        first = bars[0]
        if first.interval_end_utc > valid_until:
            return 'NO_ENTRY', 'ENTRY_WINDOW_EXPIRED', [first]
        if first.open < low:
            return 'NO_ENTRY', 'NO_FILL_GAP_DOWN', [first]
        if first.open <= high:
            return 0, first.open, True
        if first.low <= high:
            return 0, high, False
        return 'NO_ENTRY', 'NO_FILL_GAP_UP', [first]
    if trigger == TOUCH:
        window = [bar for bar in bars if bar.interval_end_utc <= valid_until]
        for index, bar in enumerate(window):
            if bar.low <= high and bar.high >= low:
                return index, max(low, min(bar.open, high)), low <= bar.open <= high
        if complete < valid_until:
            return 'PENDING_PATH', 'AWAITING_ENTRY_WINDOW', window
        if not window:
            return 'UNEVALUABLE', 'NO_SESSION_BARS_IN_ENTRY_WINDOW', []
        return 'NO_ENTRY', 'NOT_TOUCHED_BEFORE_EXPIRY', window
    return 'UNEVALUABLE', 'UNSUPPORTED_ENTRY_TRIGGER', []


def _path(bars, terms, assumptions, complete):
    trigger, low, high, stop, target, valid_until, flat_by, holding, risk = terms
    found = _entry(bars, trigger, low, high, valid_until, complete)
    if type(found[0]) is str:
        status, reason, used = found
        return status, reason, None, None, None, used
    index, raw, at_open = found
    fill_bar = bars[index]
    entry_price = raw * (1 + assumptions.entry_slippage_bps / Decimal(10000))
    if not (stop < raw and entry_price < target):
        return 'UNEVALUABLE', 'ENTRY_OUTSIDE_STOP_TARGET', None, None, None, [fill_bar]
    entry = {'market_date': fill_bar.market_date.isoformat(), 'session_id': fill_bar.session_id,
             'raw_price': str(raw), 'price': str(entry_price), 'at_open': at_open}
    if flat_by is not None:
        return 'UNEVALUABLE', 'FLAT_BY_NOT_SUPPORTED_BY_SESSION_BARS', entry, None, None, [fill_bar]

    lowest = highest = raw
    used = []
    for count, bar in enumerate(bars[index:], start=1):
        used.append(bar)
        first = count == 1
        reason, exit_raw, exit_open = None, None, False
        if not first:
            if bar.open <= stop:
                reason, exit_raw, exit_open = 'STOP', bar.open, True
            elif bar.open >= target:
                reason, exit_raw, exit_open = 'TARGET', target, True
        if reason is None:
            if bar.low <= stop:
                reason, exit_raw = 'STOP', stop
            elif bar.high >= target and (not first or at_open or bar.close >= target):
                reason, exit_raw = 'TARGET', target
        if reason is None and count >= holding:
            reason, exit_raw = 'TIME_EXIT', bar.close
        # Never understate adverse excursion; never count prices that may
        # precede an intrabar fill or follow an exit as favourable excursion.
        if reason == 'STOP':
            lowest = min(lowest, exit_raw)
            if not first:
                highest = max(highest, bar.open)
        elif reason == 'TARGET':
            highest = max(highest, exit_raw)
            if not exit_open:
                lowest = min(lowest, bar.low)
        else:
            lowest = min(lowest, bar.low)
            highest = max(highest, bar.high if (not first or at_open) else bar.close)
        if reason is None:
            continue
        exit_price = exit_raw * (1 - assumptions.exit_slippage_bps / Decimal(10000))
        cost = (entry_price + exit_price) * assumptions.cost_bps_per_side / Decimal(10000)
        exit_ = {'market_date': bar.market_date.isoformat(), 'session_id': bar.session_id,
                 'reason': reason, 'raw_price': str(exit_raw), 'price': str(exit_price),
                 'at_open': exit_open}
        metrics = {
            'r_unit_per_share': str(risk),
            'fill_risk_per_share': str(raw - stop),
            'gross_r': str((exit_raw - raw) / risk),
            'net_r': str((exit_price - entry_price - cost) / risk),
            'cost_per_share': str(cost),
            'mae_r': str((lowest - raw) / risk),
            'mfe_r': str((highest - raw) / risk),
            'holding_sessions': count,
        }
        return 'CLOSED', None, entry, exit_, metrics, used
    return 'PENDING_PATH', 'POSITION_OPEN', entry, None, None, used


def _clock(value, name):
    if not isinstance(value, datetime):
        raise ValueError(f'timezone-aware {name} required')
    try:
        return aware(value)
    except ValueError as exc:
        raise ValueError(f'timezone-aware {name} required') from exc


def evaluate_decision_outcome(identity, record, bars, *, assumptions,
                              path_complete_through_at, evaluated_at) -> dict:
    """JSON-ready decision-outcome-v1 for one retained observation; pure."""
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    assumptions = OutcomeAssumptions.model_validate(assumptions.model_dump(mode='python'))
    if type(record) is not dict:
        raise ValueError('decision-observation-v1 record dict required')
    try:
        payload = canonical_bytes(record)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('record is not canonical JSON data') from exc
    # Binds the outcome to the exact retained bytes; any edit fails closed.
    record = read_retained_decision_observation(payload, expected_sha256=identity)
    if assumptions.market != record['market']:
        raise ValueError('assumptions market mismatch')

    terms = record['terms']
    if set(terms) != TERM_FIELDS:
        raise ValueError('terms field set mismatch')
    low, high, ref, stop, target = (_decimal(terms[key], key) for key in (
        'entry_low', 'entry_high', 'entry_reference', 'stop', 'target'))
    if not (0 < low <= ref <= high and 0 < stop < ref < target):
        raise ValueError('invalid plan geometry')
    holding = terms['max_holding_sessions']
    if type(holding) is not int or holding < 1:
        raise ValueError('positive max_holding_sessions required')
    risk = _decimal(record['risk_per_share'], 'risk_per_share')
    if risk != ref - stop:
        raise ValueError('risk_per_share does not match terms')
    valid_until = _time(terms['valid_until'], 'valid_until')
    flat_by = None if terms['flat_by'] is None else _time(terms['flat_by'], 'flat_by')
    detected = _time(record['detected_at'], 'detected_at')

    complete = _clock(path_complete_through_at, 'path_complete_through_at')
    evaluated = _clock(evaluated_at, 'evaluated_at')
    if not detected <= complete <= evaluated:
        raise ValueError('path_complete_through_at must lie between detection and evaluated_at')
    bars = _bars(bars, record, detected, complete)

    with localcontext(Context(prec=34)):
        status, reason, entry, exit_, metrics, used = _path(
            bars, (terms['entry_trigger'], low, high, stop, target, valid_until, flat_by,
                   holding, risk), assumptions, complete)
    if reason not in REASONS[status]:
        raise AssertionError('unregistered outcome reason')
    return {
        'contract_version': CONTRACT_VERSION,
        'rules_version': RULES_VERSION,
        'rules': dict(RULES),
        'observation_identity': identity,
        'decision_id': record['decision_id'],
        'market': record['market'],
        'symbol': record['symbol'],
        'instrument_id': record['instrument_id'],
        'strategy_id': record['strategy_id'],
        'strategy_version': record['strategy_version'],
        'config_id': record['config_id'],
        'entry_trigger': terms['entry_trigger'],
        'detected_at': record['detected_at'],
        'path_complete_through_at': _utc(complete),
        'path_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'evaluated_at': _utc(evaluated),
        'evaluated_at_clock': 'CALLER_SUPPLIED_UNVERIFIED',
        'price_basis': 'RAW_UNADJUSTED_CALLER_ASSERTED',
        'corporate_actions': 'NOT_CHECKED',
        'assumptions': assumptions.model_dump(mode='json'),
        'bars_supplied': len(bars),
        'bars_used': [_bar_ref(bar) for bar in used],
        'status': status,
        'reason': reason,
        'entry': entry,
        'exit': exit_,
        'metrics': metrics,
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
