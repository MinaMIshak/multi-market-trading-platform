from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RiskPolicy(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    # 1% of total equity per trade before other caps.
    risk_per_trade_pct: Decimal = Field(
        default=Decimal("0.01"),
        gt=Decimal("0"),
        le=Decimal("0.05"),
    )

    # No single position may exceed 25% of equity.
    max_position_pct: Decimal = Field(
        default=Decimal("0.25"),
        gt=Decimal("0"),
        le=Decimal("1"),
    )

    # Maximum total portfolio capital exposure.
    max_portfolio_exposure_pct: Decimal = Field(
        default=Decimal("0.60"),
        gt=Decimal("0"),
        le=Decimal("1"),
    )

    max_open_positions: int = Field(
        default=3,
        ge=1,
        le=20,
    )

    # Stop accepting new trades after this daily loss in R.
    daily_loss_limit_r: Decimal = Field(
        default=Decimal("2.0"),
        gt=Decimal("0"),
    )

    # Minimum reward/risk to Target 1.
    min_target1_r: Decimal = Field(
        default=Decimal("1.80"),
        gt=Decimal("0"),
    )

    # Market regime scaling.
    risk_on_scale: Decimal = Field(
        default=Decimal("1.00"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )

    neutral_scale: Decimal = Field(
        default=Decimal("0.50"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )

    risk_off_scale: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )

    allow_short: bool = False
