"""Deterministic LONG research patterns over available continuous bars."""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from app.data.intraday import available_bars, cumulative_vwap
from app.strategies.contracts import Contract, StrategyConfig, aware, candidate


class PriorBias(Contract):
    """Offline contextual observation, subject to the same decision cutoff."""
    direction: Literal['UP', 'DOWN', 'NONE']
    available_at: datetime
    provenance_id: str = Field(min_length=1)

    @model_validator(mode='after')
    def check(self):
        aware(self.available_at)
        return self


class First15Config(StrategyConfig):
    opening_window_minutes: int = Field(gt=0)
    bullish_close_location: float = Field(ge=0, le=1)
    minimum_return: float | None = Field(ge=0)
    invalidate_below_open: bool
    invalidation_return_floor: float | None


class ORBConfig(StrategyConfig):
    opening_bars: int = Field(gt=0)
    breakout_buffer_bps: float = Field(ge=0)
    minimum_relative_volume: float | None = Field(gt=0)
    stop_at_range_low: bool


class VWAPConfig(StrategyConfig):
    minimum_extension_bps: float = Field(gt=0)
    pullback_tolerance_bps: float = Field(ge=0)
    reclaim_buffer_bps: float = Field(ge=0)
    pattern_lookback_bars: int = Field(ge=3)
    stop_lookback_bars: int | None = Field(gt=0)


class MomentumConfig(StrategyConfig):
    momentum_lookback_bars: int = Field(gt=0)
    minimum_return: float = Field(gt=0)
    minimum_relative_volume: float | None = Field(gt=0)
    maximum_vwap_extension_bps: float | None = Field(ge=0)
    stop_lookback_bars: int | None = Field(gt=0)


class IntradayEngine:
    config_type = None
    strategy_id = None

    def __init__(self, config):
        if not isinstance(config, self.config_type):
            raise ValueError('explicit typed config required')
        self.config = config

    def output(self, rows, at, state='NO_CONFIRMATION', entry=None, stop=None, evidence_cutoff=None, **evidence):
        if entry is not None and stop is not None and stop >= entry:
            # LONG risk is undefined unless the stop is strictly below entry.
            state, entry, stop = 'NO_CONFIRMATION', None, None
            evidence['risk_geometry'] = 'STOP_NOT_BELOW_ENTRY'
        return candidate(self.strategy_id, self.config, rows[0].symbol, at,
                         max([b.available_at for b in rows] + ([evidence_cutoff] if evidence_cutoff else [])), state, entry, stop,
                         strategy_version='1', source_id=rows[0].source_id, provenance_id=rows[0].provenance_id,
                         session_id=rows[0].session_id, market_date=rows[0].market_date,
                         sequences=[b.sequence for b in rows], **evidence)


class First15Engine(IntradayEngine):
    config_type = First15Config
    strategy_id = 'FIRST15'

    def evaluate(self, bars, *, decision_time, prior_bias: PriorBias):
        if not isinstance(prior_bias, PriorBias):
            raise ValueError('explicit versioned prior bias required')
        if prior_bias.available_at > aware(decision_time):
            raise ValueError('future prior bias')
        rows = available_bars(bars, decision_time)
        end = rows[0].interval_start + timedelta(minutes=self.config.opening_window_minutes)
        window = tuple(b for b in rows if b.interval_end <= end)
        if not window or window[-1].interval_end != end:
            return self.output(rows, decision_time, reason='incomplete exact opening coverage',
                               evidence_cutoff=prior_bias.available_at, prior_bias=prior_bias.model_dump())
        high, low = max(b.high for b in window), min(b.low for b in window)
        ret = window[-1].close / window[0].open - 1
        location = (window[-1].close - low) / (high - low) if high > low else None
        c = self.config
        invalid = ((c.invalidate_below_open and ret < 0) or
                   (c.invalidation_return_floor is not None and ret < c.invalidation_return_floor))
        state = 'INVALIDATED' if invalid else 'NO_CONFIRMATION'
        if (not invalid and location is not None and
                location >= c.bullish_close_location and
                (c.minimum_return is None or ret >= c.minimum_return)):
            state = {'UP': 'CONTINUATION_CONFIRMED', 'DOWN': 'REVERSAL_CONFIRMED',
                     'NONE': 'NO_CONFIRMATION'}[prior_bias.direction]
        return self.output(window, decision_time, state, return_value=ret,
                           close_location=location, prior_bias=prior_bias.model_dump(),
                           evidence_cutoff=prior_bias.available_at)


class OpeningRangeBreakoutEngine(IntradayEngine):
    config_type = ORBConfig
    strategy_id = 'ORB'

    def evaluate(self, bars, *, decision_time):
        rows = available_bars(bars, decision_time)
        c = self.config
        if len(rows) <= c.opening_bars:
            return self.output(rows, decision_time, reason='later bar required')
        opening = rows[:c.opening_bars]
        high, low = max(b.high for b in opening), min(b.low for b in opening)
        level = high * (1 + c.breakout_buffer_bps / 10000)
        mean_volume = sum(b.volume for b in rows[:-1]) / (len(rows) - 1)
        rv = rows[-1].volume / mean_volume if mean_volume else None
        qualifies = rows[-1].close > level and (c.minimum_relative_volume is None or
                    (rv is not None and rv >= c.minimum_relative_volume))
        return self.output(rows, decision_time, 'READY' if qualifies else 'NO_CONFIRMATION',
                           level if qualifies else None,
                           low if qualifies and c.stop_at_range_low else None,
                           range_high=high, range_low=low, breakout_level=level,
                           qualifying_sequence=rows[-1].sequence if qualifies else None,
                           relative_volume=rv)


class VWAPPullbackEngine(IntradayEngine):
    config_type = VWAPConfig
    strategy_id = 'VWAP_PULLBACK'

    def evaluate(self, bars, *, decision_time):
        rows = available_bars(bars, decision_time, require_vwap=True)
        values = cumulative_vwap(rows)
        c = self.config
        start = max(0, len(rows) - c.pattern_lookback_bars)
        extension = pullback = None
        for i in range(start, len(rows) - 1):
            v = values[i]
            if v is None or v <= 0:
                continue
            if extension is not None and i > extension and (
                    rows[i].low <= v * (1 + c.pullback_tolerance_bps / 10000) and
                    rows[i].high >= v * (1 - c.pullback_tolerance_bps / 10000)):
                pullback = i
                break
            if rows[i].close >= v * (1 + c.minimum_extension_bps / 10000):
                extension = i
        level = values[-1] * (1 + c.reclaim_buffer_bps / 10000) if values[-1] else None
        enough_stop = c.stop_lookback_bars is None or len(rows) >= c.stop_lookback_bars
        qualifies = pullback is not None and level is not None and rows[-1].close > level and enough_stop
        stop = min(b.low for b in rows[-c.stop_lookback_bars:]) if qualifies and c.stop_lookback_bars else None
        return self.output(rows, decision_time, 'READY' if qualifies else 'NO_CONFIRMATION',
                           level if qualifies else None, stop, vwap=values[-1],
                           extension_sequence=rows[extension].sequence if extension is not None else None,
                           pullback_sequence=rows[pullback].sequence if pullback is not None else None,
                           reclaim_sequence=rows[-1].sequence if qualifies else None)


class MomentumContinuationEngine(IntradayEngine):
    config_type = MomentumConfig
    strategy_id = 'MOMENTUM_CONTINUATION'

    def evaluate(self, bars, *, decision_time):
        c = self.config
        rows = available_bars(bars, decision_time,
                              require_vwap=c.maximum_vwap_extension_bps is not None)
        n = c.momentum_lookback_bars
        if len(rows) <= n or (c.stop_lookback_bars and len(rows) < c.stop_lookback_bars):
            return self.output(rows, decision_time, reason='insufficient history')
        ret = rows[-1].close / rows[-n-1].close - 1
        avg = sum(b.volume for b in rows[-n-1:-1]) / n
        rv = rows[-1].volume / avg if avg else None
        vwap = cumulative_vwap(rows)[-1] if c.maximum_vwap_extension_bps is not None else None
        qualifies = (ret >= c.minimum_return and
                     (c.minimum_relative_volume is None or
                      (rv is not None and rv >= c.minimum_relative_volume)) and
                     (c.maximum_vwap_extension_bps is None or
                      (vwap is not None and vwap > 0 and
                       rows[-1].close <= vwap * (1 + c.maximum_vwap_extension_bps / 10000))))
        stop = min(b.low for b in rows[-c.stop_lookback_bars:]) if qualifies and c.stop_lookback_bars else None
        return self.output(rows, decision_time, 'READY' if qualifies else 'NO_CONFIRMATION',
                           rows[-1].close if qualifies else None, stop,
                           return_value=ret, relative_volume=rv, vwap=vwap,
                           reference_sequence=rows[-n-1].sequence)
