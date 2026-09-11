"""Explicit optional M7 boundaries; no inferred periods or slippage grids."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.strategies.contracts import Contract


class PeriodicReturnSeries(Contract):
    schema_version: Literal['periodic-return-v1']
    series_id: str = Field(min_length=1, pattern=r'\S')
    period_seconds: int = Field(gt=0)
    period_ends: tuple[datetime, ...]
    returns: tuple[Decimal, ...]
    risk_free_return: Decimal
    sortino_target: Decimal
    minimum_samples: int = Field(ge=2)
    annualization_factor: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def chronology(self):
        if len(self.period_ends) != len(self.returns):
            raise ValueError('period/return length mismatch')
        for end in self.period_ends:
            if end.tzinfo != timezone.utc:
                raise ValueError('canonical UTC period ends required')
        for previous, current in zip(self.period_ends, self.period_ends[1:]):
            if current - previous != timedelta(seconds=self.period_seconds):
                raise ValueError('ordered contiguous equal periods required')
        return self


class RiskAdjustedSummary(Contract):
    series: PeriodicReturnSeries | None = None
    sharpe: Decimal | None = None
    sortino: Decimal | None = None
    sharpe_reason: str | None = 'NO_PERIODIC_SERIES'
    sortino_reason: str | None = 'NO_PERIODIC_SERIES'


def risk_adjusted(series):
    # Called inside the analyzer's private precision-34 Decimal context.
    if series is None:
        return RiskAdjustedSummary()
    if len(series.returns) < series.minimum_samples:
        return RiskAdjustedSummary(series=series, sharpe_reason='INSUFFICIENT_SAMPLES',
                                   sortino_reason='INSUFFICIENT_SAMPLES')
    n = Decimal(len(series.returns))
    excess = tuple(r - series.risk_free_return for r in series.returns)
    mean = sum(excess, Decimal(0)) / n
    deviation = (sum(((r - mean) ** 2 for r in excess), Decimal(0)) / (n - 1)).sqrt()
    targeted = tuple(r - series.sortino_target for r in series.returns)
    downside = (sum((min(r, Decimal(0)) ** 2 for r in targeted), Decimal(0)) / n).sqrt()
    scale = series.annualization_factor.sqrt() if series.annualization_factor is not None else Decimal(1)
    return RiskAdjustedSummary(
        series=series,
        sharpe=mean / deviation * scale if deviation else None,
        sortino=sum(targeted, Decimal(0)) / n / downside * scale if downside else None,
        sharpe_reason=None if deviation else 'ZERO_DISPERSION',
        sortino_reason=None if downside else 'ZERO_DOWNSIDE_DEVIATION',
    )
