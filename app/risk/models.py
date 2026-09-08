from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RiskContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    open_positions: int = Field(
        default=0,
        ge=0,
    )

    # Current portfolio market value before the new trade.
    current_exposure_value: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
    )

    # Realized P/L for the current trading day expressed in R.
    # Example: -1.5 means the account has lost 1.5R today.
    daily_realized_r: Decimal = Decimal("0")

    # Optional maximum value permitted by liquidity rules
    # for this specific symbol.
    liquidity_cap_value: Decimal | None = Field(
        default=None,
        gt=Decimal("0"),
    )
