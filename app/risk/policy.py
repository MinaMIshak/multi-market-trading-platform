"""Explicit, unvalidated research/paper policy choices; no operational defaults."""
import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    policy_version: str = Field(min_length=1, pattern=r"\S")
    risk_per_trade_pct: Decimal = Field(gt=0, le=Decimal("0.05"))
    max_position_pct: Decimal = Field(gt=0, le=1)
    max_portfolio_exposure_pct: Decimal = Field(gt=0, le=1)
    max_portfolio_open_risk_pct: Decimal = Field(gt=0, le=1)
    max_symbol_exposure_pct: Decimal = Field(gt=0, le=1)
    # Explicit None disables the group cap; zero enables a no-capacity cap.
    max_correlation_group_exposure_pct: Decimal | None = Field(ge=0, le=1)
    max_open_positions: int = Field(ge=1, le=20)
    daily_loss_limit_r: Decimal = Field(gt=0)
    min_target1_r: Decimal = Field(gt=0)
    risk_on_scale: Decimal = Field(ge=0, le=1)
    neutral_scale: Decimal = Field(ge=0, le=1)
    risk_off_scale: Decimal = Field(ge=0, le=0)
    allow_short: bool

    @field_validator("policy_version")
    @classmethod
    def canonicalize_policy_version(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def consistent(self):
        if self.neutral_scale > self.risk_on_scale:
            raise ValueError("neutral_scale cannot exceed risk_on_scale")
        if self.max_position_pct > self.max_portfolio_exposure_pct:
            raise ValueError("position cap cannot exceed portfolio exposure cap")
        if self.max_symbol_exposure_pct > self.max_portfolio_exposure_pct:
            raise ValueError("symbol cap cannot exceed portfolio exposure cap")
        if (self.max_correlation_group_exposure_pct is not None
                and self.max_correlation_group_exposure_pct > self.max_portfolio_exposure_pct):
            raise ValueError("group cap cannot exceed portfolio exposure cap")
        if self.risk_per_trade_pct > self.max_portfolio_open_risk_pct:
            raise ValueError("trade risk cannot exceed portfolio risk cap")
        return self

    @property
    def identity(self) -> str:
        # Canonical fixed-point decimals: equivalent precision has the same identity.
        values = {}
        for key, value in self.model_dump().items():
            if isinstance(value, Decimal):
                canonical = format(value, "f") if value else "0"
                if "." in canonical:
                    canonical = canonical.rstrip("0").rstrip(".")
                values[key] = canonical
            else:
                values[key] = value
        payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
