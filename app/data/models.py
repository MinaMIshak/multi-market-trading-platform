from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class DataAssetType(StrEnum):
    DAILY_BARS = "DAILY_BARS"
    INTRADAY_BARS = "INTRADAY_BARS"
    INDEX_BARS = "INDEX_BARS"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    SECURITY_MASTER = "SECURITY_MASTER"
    MARKET_CALENDAR = "MARKET_CALENDAR"


class BarGranularity(StrEnum):
    D1 = "D1"
    M1 = "M1"
    M5 = "M5"


class IngestionStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class QualitySeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    RIGHTS = "RIGHTS"
    CAPITAL_INCREASE = "CAPITAL_INCREASE"
    CAPITAL_REDUCTION = "CAPITAL_REDUCTION"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"
    OTHER = "OTHER"


class DataModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )


class MarketBar(DataModel):
    symbol: str = Field(
        min_length=1,
        max_length=32,
    )

    timestamp: datetime

    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)

    volume: Decimal = Field(
        default=Decimal("0"),
        ge=0,
    )

    turnover: Decimal | None = Field(
        default=None,
        ge=0,
    )

    source: str = Field(
        min_length=1,
        max_length=64,
    )

    adjusted: bool = False

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "timestamp must be timezone-aware"
            )

        return value


class CorporateAction(DataModel):
    action_id: UUID = Field(
        default_factory=uuid4
    )

    symbol: str = Field(
        min_length=1,
        max_length=32,
    )

    action_type: CorporateActionType
    effective_date: date

    ratio_numerator: Decimal | None = Field(
        default=None,
        gt=0,
    )

    ratio_denominator: Decimal | None = Field(
        default=None,
        gt=0,
    )

    cash_amount: Decimal | None = Field(
        default=None,
        ge=0,
    )

    currency: str | None = None

    source: str = Field(
        min_length=1,
        max_length=64,
    )

    details: dict[str, Any] = Field(
        default_factory=dict
    )

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()


class RawArtifactManifest(DataModel):
    ingestion_id: UUID = Field(
        default_factory=uuid4
    )

    provider: str
    asset_type: DataAssetType
    granularity: BarGranularity | None = None

    symbol: str | None = None
    market_date: date | None = None

    raw_path: str

    sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    byte_size: int = Field(ge=0)
    record_count: int | None = Field(
        default=None,
        ge=0,
    )

    received_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_optional_symbol(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return value.strip().upper()

    @field_validator("received_at")
    @classmethod
    def require_aware_received_at(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "received_at must be timezone-aware"
            )

        return value


class DataQualityIssue(DataModel):
    issue_id: UUID = Field(
        default_factory=uuid4
    )

    ingestion_id: UUID | None = None

    severity: QualitySeverity
    code: str = Field(
        min_length=1,
        max_length=128,
    )

    symbol: str | None = None
    market_date: date | None = None

    message: str = Field(
        min_length=1,
        max_length=2000,
    )

    payload: dict[str, Any] = Field(
        default_factory=dict
    )

    @field_validator("symbol")
    @classmethod
    def normalize_issue_symbol(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return value.strip().upper()
