"""Offline continuous-session input contract. No exchange hours inferred."""
from datetime import date, datetime

from pydantic import Field, model_validator

from app.strategies.contracts import Contract, aware


class IntradayBar(Contract):
    symbol: str = Field(min_length=1)
    interval_start: datetime
    interval_end: datetime
    available_at: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    traded_value: float | None = Field(ge=0)
    session_id: str = Field(min_length=1)
    market_date: date
    sequence: int = Field(ge=1)
    session_phase: str
    is_final: bool
    source_id: str = Field(min_length=1)
    provenance_id: str = Field(min_length=1)

    @model_validator(mode='after')
    def check(self):
        if not aware(self.interval_start) < aware(self.interval_end) <= aware(self.available_at):
            raise ValueError('invalid interval/availability')
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('invalid OHLC')
        if self.traded_value is not None and ((self.volume == 0) != (self.traded_value == 0)):
            raise ValueError('inconsistent volume/traded_value')
        return self


def available_bars(bars, decision_time, *, require_vwap=False):
    aware(decision_time)
    if any(not isinstance(b, IntradayBar) for b in bars):
        raise ValueError('validated IntradayBar required')
    rows = tuple(b for b in bars if b.available_at <= decision_time and b.is_final)
    if not rows:
        raise ValueError('no finalized available bars')
    first = rows[0]
    identity = lambda b: (b.symbol, b.session_id, b.market_date, b.source_id, b.provenance_id)
    for i, bar in enumerate(rows):
        if bar.session_phase != 'CONTINUOUS':
            raise ValueError('continuous phase required')
        if identity(bar) != identity(first):
            raise ValueError('source/session identity mismatch')
        if bar.sequence != i + 1:
            raise ValueError('missing/duplicate/unordered sequence or opening origin')
        if i and rows[i - 1].interval_end != bar.interval_start:
            raise ValueError('gap/overlap in intervals')
        if require_vwap and bar.traded_value is None:
            raise ValueError('VWAP requires traded_value')
    return rows


def cumulative_vwap(rows):
    volume = value = 0.0
    result = []
    for bar in rows:
        if bar.traded_value is None:
            raise ValueError('VWAP requires traded_value')
        volume += bar.volume
        value += bar.traded_value
        result.append(value / volume if volume else None)
    return tuple(result)
