"""Frozen normalized paper budget declaration; no allocation or performance claim."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.paper.shadow_collection import _publish_once
from app.paper.shadow_fills import _canonical, _utc
from app.paper.shadow_ledger import LABEL


class ShadowPortfolioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["shadow-portfolio-policy-v2"] = "shadow-portfolio-policy-v2"
    label: Literal["EXPERIMENTAL / PAPER ONLY"] = LABEL
    normalized_initial_nav: Literal[100] = 100
    base_currency: Literal["USD", "EGP"]
    initial_capital: Decimal = Field(gt=0)
    effective_at: datetime
    egx_capital_fraction: Decimal = Field(ge=0, le=1)
    us_capital_fraction: Decimal = Field(ge=0, le=1)
    minimum_cash_fraction: Decimal = Field(ge=0, le=1)
    max_position_fraction: Decimal = Field(gt=0, le=1)
    max_position_risk_fraction: Decimal = Field(gt=0, le=1)
    max_portfolio_risk_fraction: Decimal = Field(gt=0, le=1)

    @field_validator("effective_at", mode="before")
    @classmethod
    def utc_time(cls, value):
        return _utc(value)

    @field_validator(
        "initial_capital", "egx_capital_fraction", "us_capital_fraction", "minimum_cash_fraction",
        "max_position_fraction", "max_position_risk_fraction",
        "max_portfolio_risk_fraction", mode="before",
    )
    @classmethod
    def exact_fraction(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("finite exact Decimal required")
        return value

    @model_validator(mode="after")
    def shared_budget(self):
        # Exact rational arithmetic is independent of caller Decimal precision.
        invested = Fraction(self.egx_capital_fraction) + Fraction(self.us_capital_fraction)
        if invested <= 0 or invested + Fraction(self.minimum_cash_fraction) > 1:
            raise ValueError("sleeves and cash must fit one shared portfolio")
        if self.max_position_fraction > max(self.egx_capital_fraction, self.us_capital_fraction):
            raise ValueError("position cap exceeds every sleeve")
        if not self.max_position_risk_fraction <= self.max_portfolio_risk_fraction <= invested:
            raise ValueError("risk limits must fit shared invested capital")
        if self.max_position_risk_fraction > self.max_position_fraction:
            raise ValueError("position risk exceeds position capital")
        return self


def _now():
    return datetime.now(timezone.utc)


def _basis(policy):
    if type(policy) is not ShadowPortfolioPolicy:
        raise ValueError("exact portfolio policy required")
    policy = ShadowPortfolioPolicy.model_validate(policy.model_dump(mode="python"))
    return {"policy": policy.model_dump(mode="json"), "status": "POLICY ONLY / NOT ALLOCATED"}


def freeze_portfolio_policy(directory: Path, policy: ShadowPortfolioPolicy) -> Path:
    """One policy per ledger root, frozen before its intended effective time."""
    basis = _basis(policy)
    now = _utc(_now())
    if now >= policy.effective_at:
        raise ValueError("portfolio policy must be frozen before effective time")
    payload = basis | {"policy_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                       "frozen_at": now.isoformat()}
    path = _publish_once(Path(directory) / "portfolio-policy.json", payload)
    if _now() < now or _now() >= policy.effective_at:
        path.unlink()
        raise ValueError("clock crossed portfolio freeze boundary")
    return path


def audit_portfolio_policy(directory: Path, policy: ShadowPortfolioPolicy) -> dict:
    basis = _basis(policy)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate portfolio policy field")
            result[key] = value
        return result

    payload = json.loads((Path(directory) / "portfolio-policy.json").read_bytes(),
                         object_pairs_hook=unique)
    if type(payload) is not dict or set(payload) != set(basis) | {"policy_id", "frozen_at"}:
        raise ValueError("unexpected portfolio policy fields")
    frozen = _utc(datetime.fromisoformat(payload["frozen_at"]))
    expected = basis | {"policy_id": hashlib.sha256(_canonical(basis)).hexdigest(),
                        "frozen_at": frozen.isoformat()}
    if payload != expected or not frozen < policy.effective_at or frozen > _utc(_now()):
        raise ValueError("portfolio policy receipt mismatch")
    return payload
