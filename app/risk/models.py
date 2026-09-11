"""Caller-supplied portfolio snapshot including outstanding commitments."""
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    open_positions: int = Field(ge=0)
    pending_entries: int = Field(ge=0)
    current_exposure_value: Decimal = Field(ge=0)
    daily_realized_r: Decimal
    cash_balance: Decimal = Field(ge=0)
    reserved_cash: Decimal = Field(ge=0)
    current_open_risk_value: Decimal = Field(ge=0)
    symbol_exposure_value: Decimal = Field(ge=0)
    correlation_group_id: str | None = Field(default=None, min_length=1, pattern=r"\S")
    correlation_group_exposure_value: Decimal | None = Field(default=None, ge=0)
    liquidity_cap_value: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.reserved_cash > self.cash_balance:
            raise ValueError("reserved_cash cannot exceed cash_balance")
        if self.symbol_exposure_value > self.current_exposure_value:
            raise ValueError("symbol exposure cannot exceed total exposure")
        if (self.correlation_group_exposure_value is not None
                and self.correlation_group_exposure_value > self.current_exposure_value):
            raise ValueError("group exposure cannot exceed total exposure")
        if (self.correlation_group_id is None) != (self.correlation_group_exposure_value is None):
            raise ValueError("group identity and exposure must be supplied together")
        return self
