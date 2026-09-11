"""US7A identity-safe research planning and PIT risk admission.

This module deliberately does not execute trades or construct paper
simulation inputs. It preserves stable US instrument identity around the
shared market-neutral M5 risk arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.domain import (
    MarketRegimeType,
    RiskDecision,
    RiskDecisionType,
    SignalDirection,
    TradePlan,
    TradeState,
)
from app.risk import (
    RiskContext,
    RiskEngine,
    RiskPolicy,
)
from app.strategies.contracts import Contract
from app.us.research_adapter import USSwingResearchResult


SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _semantic_hash(payload) -> str:
    content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def _uuid_from_hash(digest: str) -> UUID:
    if (
        type(digest) is not str
        or len(digest) != 64
    ):
        raise ValueError(
            "canonical SHA256 digest required"
        )

    return UUID(
        hex=digest[:32]
    )


def _identity(model: Contract) -> str:
    return _semantic_hash(
        model.model_dump(
            mode="json"
        )
    )


def _exact_utc(value):
    if (
        type(value) is not datetime
        or value.tzinfo is not timezone.utc
    ):
        raise ValueError(
            "datetime.timezone.utc required"
        )
    return value


def _finite_decimal(value: Decimal) -> Decimal:
    if (
        type(value) is not Decimal
        or not value.is_finite()
    ):
        raise ValueError(
            "finite exact Decimal required"
        )
    return value


class USResearchPlanTerms(Contract):
    """Explicit strategy/planning semantics; no implicit stop/target defaults."""

    schema_version: Literal[
        "us-research-plan-terms-v1"
    ] = "us-research-plan-terms-v1"

    planning_rule_version: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    entry_reference: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)

    target_1: Decimal = Field(gt=0)
    target_2: Decimal | None
    target_3: Decimal | None

    valid_until: datetime

    @field_validator(
        "planning_rule_version",
        mode="before",
    )
    @classmethod
    def canonical_rule_version(cls, value):
        if type(value) is not str:
            raise ValueError(
                "planning_rule_version must be string"
            )

        value = value.strip()

        if not value:
            raise ValueError(
                "nonblank planning_rule_version required"
            )

        return value

    @field_validator(
        "entry_low",
        "entry_high",
        "entry_reference",
        "stop_price",
        "target_1",
        "target_2",
        "target_3",
        mode="before",
    )
    @classmethod
    def canonical_decimal(cls, value):
        if value is None:
            return None

        return _finite_decimal(value)

    @field_validator(
        "valid_until",
        mode="before",
    )
    @classmethod
    def canonical_time(cls, value):
        return _exact_utc(value)

    @model_validator(mode="after")
    def geometry(self):
        if self.entry_low > self.entry_high:
            raise ValueError(
                "entry_low cannot exceed entry_high"
            )

        if not (
            self.entry_low
            <= self.entry_reference
            <= self.entry_high
        ):
            raise ValueError(
                "entry_reference must lie inside entry zone"
            )

        if self.stop_price >= self.entry_reference:
            raise ValueError(
                "LONG stop must be below entry_reference"
            )

        targets = tuple(
            target
            for target in (
                self.target_1,
                self.target_2,
                self.target_3,
            )
            if target is not None
        )

        previous = self.entry_reference

        for target in targets:
            if target <= previous:
                raise ValueError(
                    "LONG targets must increase above entry"
                )
            previous = target

        return self

    @property
    def identity(self) -> str:
        return _identity(self)


class USResearchTradePlan(Contract):
    """Immutable stable-identity US research plan; never executable by itself."""

    schema_version: Literal[
        "us-research-trade-plan-v1"
    ] = "us-research-trade-plan-v1"

    instrument_id: UUID
    canonical_symbol: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    listing_mic: str = Field(
        pattern=r"^[A-Z0-9]{4}$",
    )

    signal_date: date
    signal_decision_at: datetime

    source_signal_id: str = Field(
        pattern=SHA256_PATTERN,
    )
    source_dataset_id: str = Field(
        pattern=SHA256_PATTERN,
    )

    strategy_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    strategy_version: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    config_id: str = Field(
        pattern=SHA256_PATTERN,
    )

    planning_close_reference: Decimal = Field(
        gt=0,
    )

    planning_rule_version: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    plan_terms_id: str = Field(
        pattern=SHA256_PATTERN,
    )

    signal_id: UUID
    trade_plan_id: UUID

    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    entry_reference: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)

    target_1: Decimal = Field(gt=0)
    target_2: Decimal | None
    target_3: Decimal | None

    created_at: datetime
    valid_until: datetime

    validation_status: Literal[
        "UNVALIDATED"
    ] = "UNVALIDATED"

    execution_allowed: Literal[False] = False

    @field_validator(
        "signal_date",
        mode="before",
    )
    @classmethod
    def canonical_date(cls, value):
        if type(value) is not date:
            raise ValueError(
                "exact date required"
            )
        return value

    @field_validator(
        "signal_decision_at",
        "created_at",
        "valid_until",
        mode="before",
    )
    @classmethod
    def canonical_times(cls, value):
        return _exact_utc(value)

    @field_validator(
        "planning_close_reference",
        "entry_low",
        "entry_high",
        "entry_reference",
        "stop_price",
        "target_1",
        "target_2",
        "target_3",
        mode="before",
    )
    @classmethod
    def canonical_decimals(cls, value):
        if value is None:
            return None
        return _finite_decimal(value)

    @model_validator(mode="after")
    def consistency(self):
        if self.created_at != self.signal_decision_at:
            raise ValueError(
                "research plan must be created at signal decision"
            )

        if self.valid_until <= self.created_at:
            raise ValueError(
                "valid_until must be after plan creation"
            )

        # Reuse the canonical shared TradePlan geometry contract.
        materialize_legacy_trade_plan(self)

        return self

    @property
    def identity(self) -> str:
        return _identity(self)


def _canonical_signal(
    value: USSwingResearchResult,
) -> USSwingResearchResult:
    if type(value) is not USSwingResearchResult:
        raise ValueError(
            "exact USSwingResearchResult required"
        )

    return USSwingResearchResult.model_validate(
        {
            name: getattr(value, name)
            for name in USSwingResearchResult.model_fields
        },
        strict=True,
    )


def _canonical_terms(
    value: USResearchPlanTerms,
) -> USResearchPlanTerms:
    if type(value) is not USResearchPlanTerms:
        raise ValueError(
            "exact USResearchPlanTerms required"
        )

    return USResearchPlanTerms.model_validate(
        {
            name: getattr(value, name)
            for name in USResearchPlanTerms.model_fields
        },
        strict=True,
    )


def build_us_research_trade_plan(
    signal: USSwingResearchResult,
    terms: USResearchPlanTerms,
) -> USResearchTradePlan:
    signal = _canonical_signal(
        signal
    )
    terms = _canonical_terms(
        terms
    )

    if signal.state != "WATCH":
        raise ValueError(
            "WATCH US research signal required"
        )

    if (
        signal.validation_status
        != "UNVALIDATED"
        or signal.execution_allowed is not False
    ):
        raise ValueError(
            "non-executable US6 research signal required"
        )

    if signal.planning_close_reference is None:
        raise ValueError(
            "WATCH planning close reference required"
        )

    if terms.valid_until <= signal.decision_at:
        raise ValueError(
            "plan validity must extend beyond signal decision"
        )

    source_signal_id = _identity(
        signal
    )

    signal_id = _uuid_from_hash(
        _semantic_hash(
            {
                "schema_version": (
                    "us-research-signal-id-v1"
                ),
                "source_signal_id": (
                    source_signal_id
                ),
            }
        )
    )

    trade_plan_id = _uuid_from_hash(
        _semantic_hash(
            {
                "schema_version": (
                    "us-research-trade-plan-id-v1"
                ),
                "source_signal_id": (
                    source_signal_id
                ),
                "plan_terms_id": (
                    terms.identity
                ),
            }
        )
    )

    return USResearchTradePlan(
        instrument_id=signal.instrument_id,
        canonical_symbol=signal.canonical_symbol,
        listing_mic=signal.listing_mic,
        signal_date=signal.signal_date,
        signal_decision_at=signal.decision_at,
        source_signal_id=source_signal_id,
        source_dataset_id=signal.source_dataset_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        config_id=signal.config_id,
        planning_close_reference=(
            signal.planning_close_reference
        ),
        planning_rule_version=(
            terms.planning_rule_version
        ),
        plan_terms_id=terms.identity,
        signal_id=signal_id,
        trade_plan_id=trade_plan_id,
        entry_low=terms.entry_low,
        entry_high=terms.entry_high,
        entry_reference=terms.entry_reference,
        stop_price=terms.stop_price,
        target_1=terms.target_1,
        target_2=terms.target_2,
        target_3=terms.target_3,
        created_at=signal.decision_at,
        valid_until=terms.valid_until,
    )


def _canonical_plan(
    value: USResearchTradePlan,
) -> USResearchTradePlan:
    if type(value) is not USResearchTradePlan:
        raise ValueError(
            "exact USResearchTradePlan required"
        )

    return USResearchTradePlan.model_validate(
        {
            name: getattr(value, name)
            for name in USResearchTradePlan.model_fields
        },
        strict=True,
    )


def materialize_legacy_trade_plan(
    plan: USResearchTradePlan,
) -> TradePlan:
    if type(plan) is not USResearchTradePlan:
        raise ValueError(
            "exact USResearchTradePlan required"
        )

    return TradePlan(
        trade_plan_id=plan.trade_plan_id,
        signal_id=plan.signal_id,
        symbol=plan.canonical_symbol,
        direction=SignalDirection.LONG,
        entry_low=plan.entry_low,
        entry_high=plan.entry_high,
        entry_reference=plan.entry_reference,
        stop_price=plan.stop_price,
        target_1=plan.target_1,
        target_2=plan.target_2,
        target_3=plan.target_3,
        created_at=plan.created_at,
        valid_until=plan.valid_until,
    )


class USResearchRiskSnapshot(Contract):
    """PIT portfolio/risk facts known by one historical risk decision."""

    schema_version: Literal[
        "us-research-risk-snapshot-v1"
    ] = "us-research-risk-snapshot-v1"

    snapshot_version: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    available_at: datetime

    currency: Literal["USD"] = "USD"

    market_regime: MarketRegimeType

    account_equity: Decimal = Field(gt=0)

    open_positions: int = Field(ge=0)
    pending_entries: int = Field(ge=0)

    current_exposure_value: Decimal = Field(ge=0)
    daily_realized_r: Decimal

    cash_balance: Decimal = Field(ge=0)
    reserved_cash: Decimal = Field(ge=0)

    current_open_risk_value: Decimal = Field(ge=0)

    # Stable-instrument exposure. It is mapped to the legacy M5
    # `symbol_exposure_value` compatibility slot only at the adapter boundary.
    instrument_exposure_value: Decimal = Field(ge=0)

    correlation_group_id: str | None
    correlation_group_exposure_value: Decimal | None

    liquidity_cap_value: Decimal | None

    market_regime_source_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    portfolio_source_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    provenance_id: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )

    @field_validator(
        "snapshot_version",
        "market_regime_source_id",
        "portfolio_source_id",
        "provenance_id",
        mode="before",
    )
    @classmethod
    def canonical_text(cls, value):
        if type(value) is not str:
            raise ValueError(
                "exact string required"
            )

        value = value.strip()

        if not value:
            raise ValueError(
                "nonblank string required"
            )

        return value

    @field_validator(
        "available_at",
        mode="before",
    )
    @classmethod
    def canonical_available_at(cls, value):
        return _exact_utc(value)

    @field_validator(
        "account_equity",
        "current_exposure_value",
        "daily_realized_r",
        "cash_balance",
        "reserved_cash",
        "current_open_risk_value",
        "instrument_exposure_value",
        "correlation_group_exposure_value",
        "liquidity_cap_value",
        mode="before",
    )
    @classmethod
    def canonical_decimals(cls, value):
        if value is None:
            return None
        return _finite_decimal(value)

    @model_validator(mode="after")
    def shared_context_consistency(self):
        RiskContext(
            open_positions=self.open_positions,
            pending_entries=self.pending_entries,
            current_exposure_value=(
                self.current_exposure_value
            ),
            daily_realized_r=(
                self.daily_realized_r
            ),
            cash_balance=self.cash_balance,
            reserved_cash=self.reserved_cash,
            current_open_risk_value=(
                self.current_open_risk_value
            ),
            symbol_exposure_value=(
                self.instrument_exposure_value
            ),
            correlation_group_id=(
                self.correlation_group_id
            ),
            correlation_group_exposure_value=(
                self.correlation_group_exposure_value
            ),
            liquidity_cap_value=(
                self.liquidity_cap_value
            ),
        )

        return self

    @property
    def identity(self) -> str:
        return _identity(self)


class USQuantityCap(Contract):
    name: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    quantity: int = Field(ge=0)


class USResearchRiskAdmission(Contract):
    """Immutable M5 result bound back to stable US research identity."""

    schema_version: Literal[
        "us-research-risk-admission-v1"
    ] = "us-research-risk-admission-v1"

    instrument_id: UUID
    canonical_symbol: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    listing_mic: str = Field(
        pattern=r"^[A-Z0-9]{4}$",
    )

    source_plan_id: str = Field(
        pattern=SHA256_PATTERN,
    )
    risk_snapshot_id: str = Field(
        pattern=SHA256_PATTERN,
    )

    trade_plan_id: UUID
    risk_decision_id: UUID

    risk_decision_at: datetime
    trade_state: TradeState

    policy_version: str = Field(
        min_length=1,
        pattern=r"^\S(?:.*\S)?$",
    )
    policy_identity: str = Field(
        pattern=SHA256_PATTERN,
    )

    decision: RiskDecisionType

    account_equity: Decimal = Field(gt=0)
    risk_budget: Decimal = Field(ge=0)
    approved_risk: Decimal = Field(ge=0)

    quantity: int = Field(ge=0)
    max_position_value: Decimal = Field(ge=0)
    portfolio_exposure_pct: Decimal = Field(
        ge=0,
        le=1,
    )

    daily_realized_r: Decimal

    stable_instrument_exposure_value: Decimal = Field(
        ge=0,
    )

    quantity_caps: tuple[
        USQuantityCap,
        ...
    ]

    reasons: tuple[str, ...]
    blockers: tuple[str, ...]

    validation_status: Literal[
        "UNVALIDATED"
    ] = "UNVALIDATED"

    execution_allowed: Literal[False] = False

    @field_validator(
        "risk_decision_at",
        mode="before",
    )
    @classmethod
    def canonical_time(cls, value):
        return _exact_utc(value)

    @field_validator(
        "account_equity",
        "risk_budget",
        "approved_risk",
        "max_position_value",
        "portfolio_exposure_pct",
        "daily_realized_r",
        "stable_instrument_exposure_value",
        mode="before",
    )
    @classmethod
    def canonical_decimals(cls, value):
        return _finite_decimal(value)

    @model_validator(mode="after")
    def canonical_risk_result(self):
        materialize_legacy_risk_decision(
            self
        )
        return self

    @property
    def identity(self) -> str:
        return _identity(self)


def materialize_legacy_risk_decision(
    admission: USResearchRiskAdmission,
) -> RiskDecision:
    if type(admission) is not USResearchRiskAdmission:
        raise ValueError(
            "exact USResearchRiskAdmission required"
        )

    return RiskDecision(
        risk_decision_id=(
            admission.risk_decision_id
        ),
        trade_plan_id=(
            admission.trade_plan_id
        ),
        policy_version=(
            admission.policy_version
        ),
        policy_identity=(
            admission.policy_identity
        ),
        quantity_caps={
            item.name: item.quantity
            for item in admission.quantity_caps
        },
        decision=admission.decision,
        account_equity=(
            admission.account_equity
        ),
        risk_budget=admission.risk_budget,
        approved_risk=(
            admission.approved_risk
        ),
        quantity=admission.quantity,
        max_position_value=(
            admission.max_position_value
        ),
        portfolio_exposure_pct=(
            admission.portfolio_exposure_pct
        ),
        daily_realized_r=(
            admission.daily_realized_r
        ),
        reasons=list(admission.reasons),
        blockers=list(admission.blockers),
    )


def _canonical_snapshot(
    value: USResearchRiskSnapshot,
) -> USResearchRiskSnapshot:
    if type(value) is not USResearchRiskSnapshot:
        raise ValueError(
            "exact USResearchRiskSnapshot required"
        )

    return USResearchRiskSnapshot.model_validate(
        {
            name: getattr(value, name)
            for name in USResearchRiskSnapshot.model_fields
        },
        strict=True,
    )


def _canonical_policy(
    value: RiskPolicy,
) -> RiskPolicy:
    if type(value) is not RiskPolicy:
        raise ValueError(
            "exact RiskPolicy required"
        )

    return RiskPolicy.model_validate(
        value.model_dump(
            mode="python"
        ),
        strict=True,
    )


def admit_us_research_risk(
    plan: USResearchTradePlan,
    *,
    policy: RiskPolicy,
    snapshot: USResearchRiskSnapshot,
    decision_at: datetime,
    trade_state: TradeState,
) -> USResearchRiskAdmission:
    plan = _canonical_plan(
        plan
    )
    policy = _canonical_policy(
        policy
    )
    snapshot = _canonical_snapshot(
        snapshot
    )
    decision_at = _exact_utc(
        decision_at
    )

    if not isinstance(
        trade_state,
        TradeState,
    ):
        raise ValueError(
            "explicit TradeState required"
        )

    if decision_at < plan.created_at:
        raise ValueError(
            "risk decision cannot precede research plan"
        )

    if snapshot.available_at > decision_at:
        raise ValueError(
            "future risk snapshot"
        )

    legacy_plan = materialize_legacy_trade_plan(
        plan
    )

    context = RiskContext(
        open_positions=snapshot.open_positions,
        pending_entries=snapshot.pending_entries,
        current_exposure_value=(
            snapshot.current_exposure_value
        ),
        daily_realized_r=(
            snapshot.daily_realized_r
        ),
        cash_balance=snapshot.cash_balance,
        reserved_cash=snapshot.reserved_cash,
        current_open_risk_value=(
            snapshot.current_open_risk_value
        ),
        # Compatibility slot is intentionally populated from stable
        # instrument exposure, never from current ticker identity.
        symbol_exposure_value=(
            snapshot.instrument_exposure_value
        ),
        correlation_group_id=(
            snapshot.correlation_group_id
        ),
        correlation_group_exposure_value=(
            snapshot.correlation_group_exposure_value
        ),
        liquidity_cap_value=(
            snapshot.liquidity_cap_value
        ),
    )

    shared = RiskEngine(
        policy
    ).evaluate(
        plan=legacy_plan,
        account_equity=(
            snapshot.account_equity
        ),
        market_regime=(
            snapshot.market_regime
        ),
        context=context,
        decision_time=decision_at,
        trade_state=trade_state,
    )

    semantic_decision = (
        shared.model_dump(
            mode="json",
            exclude={
                "risk_decision_id",
            },
        )
    )

    risk_decision_id = _uuid_from_hash(
        _semantic_hash(
            {
                "schema_version": (
                    "us-research-risk-decision-id-v1"
                ),
                "source_plan_id": (
                    plan.identity
                ),
                "risk_snapshot_id": (
                    snapshot.identity
                ),
                "decision_at": (
                    decision_at.isoformat()
                ),
                "trade_state": (
                    trade_state.value
                ),
                "shared_decision": (
                    semantic_decision
                ),
            }
        )
    )

    return USResearchRiskAdmission(
        instrument_id=plan.instrument_id,
        canonical_symbol=(
            plan.canonical_symbol
        ),
        listing_mic=plan.listing_mic,
        source_plan_id=plan.identity,
        risk_snapshot_id=(
            snapshot.identity
        ),
        trade_plan_id=(
            plan.trade_plan_id
        ),
        risk_decision_id=(
            risk_decision_id
        ),
        risk_decision_at=decision_at,
        trade_state=trade_state,
        policy_version=(
            shared.policy_version
        ),
        policy_identity=(
            shared.policy_identity
        ),
        decision=shared.decision,
        account_equity=(
            shared.account_equity
        ),
        risk_budget=shared.risk_budget,
        approved_risk=(
            shared.approved_risk
        ),
        quantity=shared.quantity,
        max_position_value=(
            shared.max_position_value
        ),
        portfolio_exposure_pct=(
            shared.portfolio_exposure_pct
        ),
        daily_realized_r=(
            shared.daily_realized_r
        ),
        stable_instrument_exposure_value=(
            snapshot.instrument_exposure_value
        ),
        quantity_caps=tuple(
            USQuantityCap(
                name=name,
                quantity=quantity,
            )
            for name, quantity
            in sorted(
                shared.quantity_caps.items()
            )
        ),
        reasons=tuple(
            shared.reasons
        ),
        blockers=tuple(
            shared.blockers
        ),
    )


__all__ = [
    "USQuantityCap",
    "USResearchPlanTerms",
    "USResearchRiskAdmission",
    "USResearchRiskSnapshot",
    "USResearchTradePlan",
    "admit_us_research_risk",
    "build_us_research_trade_plan",
    "materialize_legacy_risk_decision",
    "materialize_legacy_trade_plan",
]
