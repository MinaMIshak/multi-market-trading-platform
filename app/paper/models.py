"""Explicit M6 contracts retaining canonical M4 bars and M5 admission models."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.data.intraday import IntradayBar
from app.domain.models import RiskDecision, TradePlan
from app.strategies.contracts import Contract, aware


class PaperExecutionConfig(Contract):
    config_version: Literal['paper-execution-v1']
    entry_slippage_bps: Decimal = Field(ge=0)
    stop_slippage_bps: Decimal = Field(ge=0)
    target_slippage_bps: Decimal = Field(ge=0)
    scheduled_exit_slippage_bps: Decimal = Field(ge=0)
    cost_bps_per_side: Decimal = Field(ge=0)
    fixed_cost_per_side: Decimal = Field(ge=0)
    max_volume_participation_pct: Decimal | None = Field(gt=0, le=1)
    profit_target: Literal['TARGET_1']
    time_exit_at: datetime | None
    session_end_at: datetime | None

    @field_validator('time_exit_at', 'session_end_at')
    @classmethod
    def timestamps(cls, value):
        return aware(value) if value is not None else None


class PaperSimulationInput(Contract):
    schema_version: Literal['paper-simulation-v1']
    trade_plan: TradePlan
    risk_decision: RiskDecision
    bars: tuple[IntradayBar, ...]
    admission_time: datetime
    config: PaperExecutionConfig
    # TradePlan does not carry these identities. Caller must bind its intended
    # execution stream explicitly; they are not inferred from the first bar.
    session_id: str = Field(min_length=1, pattern=r'\S')
    market_date: date
    source_id: str = Field(min_length=1, pattern=r'\S')
    provenance_id: str = Field(min_length=1, pattern=r'\S')

    @field_validator('trade_plan', 'risk_decision', 'config', mode='before')
    @classmethod
    def canonical_objects(cls, value, info):
        kind = {'trade_plan': TradePlan, 'risk_decision': RiskDecision,
                'config': PaperExecutionConfig}[info.field_name]
        if not isinstance(value, kind):
            raise ValueError(f'canonical {kind.__name__} required')
        return kind.model_validate(value.model_dump(), strict=True)

    @field_validator('bars', mode='before')
    @classmethod
    def canonical_bars(cls, value):
        if not isinstance(value, tuple) or any(not isinstance(b, IntradayBar) for b in value):
            raise ValueError('tuple of validated IntradayBar required')
        return tuple(IntradayBar.model_validate(b.model_dump()) for b in value)

    @model_validator(mode='after')
    def consistency(self):
        p, r = self.trade_plan, self.risk_decision
        if not aware(p.created_at) <= aware(self.admission_time) < aware(p.valid_until):
            raise ValueError('admission outside plan validity')
        if r.trade_plan_id != p.trade_plan_id:
            raise ValueError('trade_plan_id mismatch')
        if p.direction.value != 'LONG':
            raise ValueError('unsupported SHORT paper execution')
        if not p.stop_price < p.entry_low <= p.entry_high < p.target_1:
            raise ValueError('invalid execution geometry')
        for obj in (p, r):
            for value in obj.model_dump().values():
                if isinstance(value, Decimal) and not value.is_finite():
                    raise ValueError('non-finite boundary value')
        if not p.symbol.strip() or not r.policy_version.strip() or not r.policy_identity.strip():
            raise ValueError('missing identity')
        expected = (p.symbol, self.session_id, self.market_date, self.source_id, self.provenance_id)
        if self.bars and self.bars[0].sequence != 1:
            raise ValueError('invalid event chronology: opening sequence must start at 1')
        previous = None
        for bar in self.bars:
            if (bar.symbol, bar.session_id, bar.market_date, bar.source_id, bar.provenance_id) != expected:
                raise ValueError('symbol/session/source/provenance mismatch')
            if not bar.is_final or bar.session_phase != 'CONTINUOUS':
                raise ValueError('final CONTINUOUS bars required')
            if previous and (bar.sequence != previous.sequence + 1
                             or bar.interval_start != previous.interval_end
                             or bar.available_at < previous.available_at):
                raise ValueError('invalid event chronology')
            previous = bar
        return self


class PaperFill(Contract):
    side: Literal['BUY', 'SELL']
    quantity: int = Field(gt=0)
    raw_price: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    bar_sequence: int
    interval_start: datetime
    interval_end: datetime
    known_at: datetime
    at_open: bool


class PaperPosition(Contract):
    mode: Literal['PAPER'] = 'PAPER'
    trade_plan_id: UUID
    quantity: int
    entry: PaperFill
    exit: PaperFill | None
    state: Literal['OPEN', 'CLOSED']


class PaperTradeMetrics(Contract):
    entry_notional: Decimal
    exit_notional: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    r_multiple: Decimal
    mae: Decimal
    mfe: Decimal
    mae_r: Decimal
    mfe_r: Decimal


class PaperSimulationResult(Contract):
    schema_version: Literal['paper-result-v1'] = 'paper-result-v1'
    state: Literal['REJECTED', 'INCOMPLETE', 'OPEN', 'COMPLETED', 'NO_FILL']
    outcome: Literal['WIN', 'LOSS', 'TIME_EXIT', 'NO_FILL'] | None = None
    rejection_reason: str | None = None
    exit_reason: Literal['STOP', 'TARGET_1', 'TIME_EXIT', 'SESSION_END'] | None = None
    position: PaperPosition | None = None
    metrics: PaperTradeMetrics | None = None
