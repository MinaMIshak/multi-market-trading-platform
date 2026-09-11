"""Offline daily research engines; no next-open reads or model training."""
from datetime import date, datetime
from math import isfinite
from typing import Literal

from pydantic import Field, model_validator

from app.data.point_in_time import PointInTimeDailyRepository
from app.strategies.contracts import Contract, StrategyConfig, aware, candidate


class SwingConfig(StrategyConfig):
    minimum_history: int = Field(ge=2)
    fast_ema_window: int = Field(gt=0)
    slow_ema_window: int = Field(gt=0)
    breakout_lookback: int | None = Field(gt=0)

    @model_validator(mode='after')
    def check(self):
        if not self.fast_ema_window < self.slow_ema_window <= self.minimum_history:
            raise ValueError('EMA windows/history inconsistent')
        if self.breakout_lookback is not None and self.minimum_history <= self.breakout_lookback:
            raise ValueError('breakout requires prior history')
        return self


def ema(values, window):
    value = sum(values[:window]) / window
    alpha = 2 / (window + 1)
    for close in values[window:]:
        value += alpha * (close - value)
    return value


class SwingSeriesEvaluation(Contract):
    """Pure daily swing-math result; no market repository or execution semantics."""

    state: Literal['WATCH', 'NO_CONFIRMATION']
    fast_ema: float
    slow_ema: float
    breakout_reference: float | None = None


def evaluate_swing_series(
    *,
    closes: tuple[float, ...],
    highs: tuple[float, ...],
    config: SwingConfig,
) -> SwingSeriesEvaluation:
    """Evaluate identical Swing math over already-admitted daily research values."""

    if not isinstance(config, SwingConfig):
        raise ValueError('explicit SwingConfig required')

    config = SwingConfig.model_validate(
        {
            name: getattr(config, name)
            for name in SwingConfig.model_fields
        },
        strict=True,
    )

    if type(closes) is not tuple or type(highs) is not tuple:
        raise ValueError('canonical swing series tuples required')

    if len(closes) != len(highs):
        raise ValueError('swing close/high length mismatch')

    if len(closes) < config.minimum_history:
        raise ValueError('validated minimum history required')

    for name, values in (
        ('closes', closes),
        ('highs', highs),
    ):
        if any(
            type(value) is not float
            or not isfinite(value)
            or value <= 0
            for value in values
        ):
            raise ValueError(
                f'positive finite float {name} required'
            )

    fast = ema(
        closes,
        config.fast_ema_window,
    )
    slow = ema(
        closes,
        config.slow_ema_window,
    )

    breakout = (
        None
        if config.breakout_lookback is None
        else max(
            highs[
                -config.breakout_lookback - 1:
                -1
            ]
        )
    )

    qualifies = (
        closes[-1] > fast > slow
        and (
            breakout is None
            or closes[-1] > breakout
        )
    )

    return SwingSeriesEvaluation(
        state=(
            'WATCH'
            if qualifies
            else 'NO_CONFIRMATION'
        ),
        fast_ema=fast,
        slow_ema=slow,
        breakout_reference=breakout,
    )


class SwingEngine:
    def __init__(self, repository, config):
        if not isinstance(repository, PointInTimeDailyRepository):
            raise ValueError('M3 PointInTimeDailyRepository required')
        if not isinstance(config, SwingConfig):
            raise ValueError('explicit SwingConfig required')
        self.repository, self.config = repository, config

    def evaluate(self, *, raw_path, signal_date, decision_time):
        aware(decision_time)
        data = self.repository.load(raw_path=raw_path, universe_date=signal_date,
                                    expected_market_date=signal_date, as_of=decision_time)
        c = self.config
        if data.dq_status != 'VALIDATED' or len(data.rows) < c.minimum_history:
            raise ValueError('validated minimum history required')

        evaluation = evaluate_swing_series(
            closes=tuple(
                float(b.close)
                for b in data.split_adjusted
            ),
            highs=tuple(
                float(b.high)
                for b in data.split_adjusted
            ),
            config=c,
        )

        qualifies = evaluation.state == 'WATCH'

        return candidate('SWING', c, data.rows[-1].canonical_symbol, decision_time,
                         decision_time, evaluation.state,
                         float(data.rows[-1].close) if qualifies else None,
                         strategy_version='1', timing='NEXT_ELIGIBLE_SESSION', after_market_date=signal_date,
                         reference_kind='D_CLOSE_PLANNING_REFERENCE_ONLY',
                         source_audit_id=data.audit_id, provenance_ids=data.provenance_ids,
                         fast_ema=evaluation.fast_ema, slow_ema=evaluation.slow_ema,
                         breakout_reference=evaluation.breakout_reference)


class PreSurgeConfig(StrategyConfig):
    scorer_contract: Literal['LEGACY_V5_PARITY_SEED']
    risk_penalty: float = Field(ge=0)


class ScorerRow(Contract):
    symbol: str = Field(min_length=1)
    signal_date: date
    available_at: datetime
    source_date: date
    source_id: str = Field(min_length=1)
    provenance_id: str = Field(min_length=1)
    scorer_contract: Literal['LEGACY_V5_PARITY_SEED']
    model_version: str = Field(min_length=1)
    trained_through: date
    model_p_top10: float = Field(ge=0, le=1)
    model_p_close8: float = Field(ge=0, le=1)
    model_p_stop5: float = Field(ge=0, le=1)
    model_expected_close: float
    avg_turnover: float = Field(ge=0)
    today_return: float

    @model_validator(mode='after')
    def check(self):
        aware(self.available_at)
        if self.trained_through >= self.signal_date:
            raise ValueError('training leakage')
        if self.source_date != self.signal_date:
            raise ValueError('stale source identity')
        if self.signal_date > self.available_at.date():
            raise ValueError('future signal date')
        return self


def percentile(values, value):
    """Ascending average-tie rank / cohort size (pandas rank pct parity)."""
    below = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    return (below + (equal + 1) / 2) / len(values)


class PreSurgeV7Engine:
    def __init__(self, config):
        if not isinstance(config, PreSurgeConfig):
            raise ValueError('explicit PreSurgeConfig required')
        self.config = config

    def evaluate(self, rows, *, decision_time):
        aware(decision_time)
        if any(not isinstance(r, ScorerRow) for r in rows):
            raise ValueError('versioned ScorerRow required')
        available = tuple(r for r in rows if r.available_at <= decision_time)
        output = []
        for day in sorted({r.signal_date for r in available}):
            cohort = [r for r in available if r.signal_date == day]
            identities = {(r.source_id, r.provenance_id, r.model_version, r.trained_through,
                           r.scorer_contract) for r in cohort}
            if len(identities) != 1 or len({r.symbol for r in cohort}) != len(cohort):
                raise ValueError('ambiguous scorer source/cohort')
            cohort = [r for r in cohort if r.today_return < 0.05]
            fields = ('model_p_top10', 'model_p_close8', 'model_expected_close',
                      'avg_turnover', 'model_p_stop5')
            ranked = []
            for row in cohort:
                ranks = {f: percentile([getattr(r, f) for r in cohort], getattr(row, f)) for f in fields}
                score = (ranks[fields[0]] + .25 * ranks[fields[1]] + .15 * ranks[fields[2]]
                         + .05 * ranks[fields[3]] - self.config.risk_penalty * ranks[fields[4]])
                ranked.append((row, score, ranks))
            ranked.sort(key=lambda x: (-x[1], -x[0].model_p_top10, -x[0].avg_turnover, x[0].symbol))
            for row, score, ranks in ranked:
                output.append(candidate('PRE_SURGE', self.config, row.symbol, decision_time,
                                        max(r.available_at for r in cohort), 'WATCH',
                                        strategy_version='7', scorer=row.model_dump(), score=score, ranks=ranks,
                                        purpose='LEGACY_RESEARCH_OUTCOME_RANKING'))
        return tuple(output)
