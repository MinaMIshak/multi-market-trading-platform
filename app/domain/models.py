from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    CandidateSource,
    ExitReason,
    First15State,
    MarketRegimeType,
    MarketSessionStatus,
    OutcomeStatus,
    RiskDecisionType,
    SignalDirection,
    SignalStatus,
    TradeState,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
    )


class MarketSession(DomainModel):
    market_date: date
    status: MarketSessionStatus
    timezone: str = "Africa/Cairo"
    opened_at: datetime | None = None
    first15_closed_at: datetime | None = None
    closed_at: datetime | None = None
    data_verified_at: datetime | None = None

    @field_validator(
        "opened_at",
        "first15_closed_at",
        "closed_at",
        "data_verified_at",
    )
    @classmethod
    def require_timezone_aware_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class MarketRegime(DomainModel):
    market_date: date
    regime: MarketRegimeType
    confidence: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    egx_return_1d: Decimal | None = None
    egx_return_5d: Decimal | None = None
    breadth_up_pct: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    breadth_down_pct: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    median_return: Decimal | None = None
    reasons: list[str] = Field(default_factory=list)


class Candidate(DomainModel):
    candidate_id: UUID = Field(default_factory=uuid4)
    symbol: str = Field(min_length=1, max_length=32)
    market_date: date
    source: CandidateSource
    score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
    )
    probability: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    expected_r: Decimal | None = None
    stop_probability: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    first15_state: First15State | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class Signal(DomainModel):
    signal_id: UUID = Field(default_factory=uuid4)
    candidate_id: UUID | None = None
    symbol: str = Field(min_length=1, max_length=32)
    strategy: CandidateSource
    direction: SignalDirection = SignalDirection.LONG
    status: SignalStatus
    created_at: datetime
    expires_at: datetime | None = None
    trigger_price: Decimal | None = Field(default=None, gt=0)
    confidence: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    reasons: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone_aware_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_expiry(self) -> Signal:
        if (
            self.expires_at is not None
            and self.expires_at <= self.created_at
        ):
            raise ValueError(
                "expires_at must be after created_at"
            )
        return self


class TradePlan(DomainModel):
    trade_plan_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID
    symbol: str = Field(min_length=1, max_length=32)
    direction: SignalDirection = SignalDirection.LONG

    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    entry_reference: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)

    target_1: Decimal = Field(gt=0)
    target_2: Decimal | None = Field(default=None, gt=0)
    target_3: Decimal | None = Field(default=None, gt=0)

    valid_until: datetime
    created_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("valid_until", "created_at")
    @classmethod
    def require_timezone_aware_datetime(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_price_structure(self) -> TradePlan:
        if self.entry_low > self.entry_high:
            raise ValueError(
                "entry_low cannot be above entry_high"
            )

        if not (
            self.entry_low
            <= self.entry_reference
            <= self.entry_high
        ):
            raise ValueError(
                "entry_reference must be inside entry zone"
            )

        if self.valid_until <= self.created_at:
            raise ValueError(
                "valid_until must be after created_at"
            )

        targets = [
            target
            for target in (
                self.target_1,
                self.target_2,
                self.target_3,
            )
            if target is not None
        ]

        if self.direction == SignalDirection.LONG:
            if self.stop_price >= self.entry_reference:
                raise ValueError(
                    "long stop must be below entry_reference"
                )

            previous = self.entry_reference
            for target in targets:
                if target <= previous:
                    raise ValueError(
                        "long targets must increase above entry"
                    )
                previous = target

        if self.direction == SignalDirection.SHORT:
            if self.stop_price <= self.entry_reference:
                raise ValueError(
                    "short stop must be above entry_reference"
                )

            previous = self.entry_reference
            for target in targets:
                if target >= previous:
                    raise ValueError(
                        "short targets must decrease below entry"
                    )
                previous = target

        return self

    @property
    def risk_per_share(self) -> Decimal:
        return abs(
            self.entry_reference - self.stop_price
        )

    def reward_r(self, target: Decimal) -> Decimal:
        return (
            abs(target - self.entry_reference)
            / self.risk_per_share
        )


class RiskDecision(DomainModel):
    risk_decision_id: UUID = Field(default_factory=uuid4)
    trade_plan_id: UUID
    decision: RiskDecisionType

    account_equity: Decimal = Field(gt=0)
    risk_budget: Decimal = Field(ge=0)
    approved_risk: Decimal = Field(ge=0)

    quantity: int = Field(ge=0)
    max_position_value: Decimal = Field(ge=0)

    portfolio_exposure_pct: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    daily_realized_r: Decimal = Decimal("0")

    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_risk_decision(self) -> RiskDecision:
        if self.approved_risk > self.risk_budget:
            raise ValueError(
                "approved_risk cannot exceed risk_budget"
            )

        if self.decision == RiskDecisionType.BLOCK:
            if self.quantity != 0:
                raise ValueError(
                    "blocked trade must have quantity=0"
                )
            if self.approved_risk != 0:
                raise ValueError(
                    "blocked trade must have approved_risk=0"
                )

        if (
            self.decision == RiskDecisionType.APPROVE
            and self.quantity <= 0
        ):
            raise ValueError(
                "approved trade requires positive quantity"
            )

        return self


class Position(DomainModel):
    position_id: UUID = Field(default_factory=uuid4)
    trade_plan_id: UUID
    symbol: str = Field(min_length=1, max_length=32)
    direction: SignalDirection

    state: TradeState = TradeState.IN_POSITION

    quantity: int = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)

    opened_at: datetime
    closed_at: datetime | None = None
    exit_price: Decimal | None = Field(default=None, gt=0)

    mfe_r: Decimal = Decimal("0")
    mae_r: Decimal = Decimal("0")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("opened_at", "closed_at")
    @classmethod
    def require_timezone_aware_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_position(self) -> Position:
        if (
            self.closed_at is not None
            and self.closed_at <= self.opened_at
        ):
            raise ValueError(
                "closed_at must be after opened_at"
            )

        if (
            self.closed_at is None
            and self.exit_price is not None
        ):
            raise ValueError(
                "exit_price requires closed_at"
            )

        if (
            self.closed_at is not None
            and self.exit_price is None
        ):
            raise ValueError(
                "closed position requires exit_price"
            )

        return self


class TradeOutcome(DomainModel):
    outcome_id: UUID = Field(default_factory=uuid4)
    position_id: UUID | None = None
    trade_plan_id: UUID

    symbol: str = Field(min_length=1, max_length=32)
    status: OutcomeStatus

    filled: bool
    entry_price: Decimal | None = Field(default=None, gt=0)
    exit_price: Decimal | None = Field(default=None, gt=0)

    gross_pnl: Decimal = Decimal("0")
    costs: Decimal = Field(default=Decimal("0"), ge=0)
    net_pnl: Decimal = Decimal("0")
    realized_r: Decimal | None = None

    mae_r: Decimal | None = None
    mfe_r: Decimal | None = None

    exit_reason: ExitReason | None = None

    signal_time: datetime
    fill_time: datetime | None = None
    exit_time: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator(
        "signal_time",
        "fill_time",
        "exit_time",
    )
    @classmethod
    def require_timezone_aware_datetime(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> TradeOutcome:
        if not self.filled:
            if self.status != OutcomeStatus.NO_FILL:
                raise ValueError(
                    "unfilled trade must have NO_FILL status"
                )
            if self.entry_price is not None:
                raise ValueError(
                    "unfilled trade cannot have entry_price"
                )
            if self.fill_time is not None:
                raise ValueError(
                    "unfilled trade cannot have fill_time"
                )

        if self.filled:
            if self.entry_price is None:
                raise ValueError(
                    "filled trade requires entry_price"
                )
            if self.fill_time is None:
                raise ValueError(
                    "filled trade requires fill_time"
                )

        if (
            self.exit_time is not None
            and self.fill_time is not None
            and self.exit_time <= self.fill_time
        ):
            raise ValueError(
                "exit_time must be after fill_time"
            )

        return self
