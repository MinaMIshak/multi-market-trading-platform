"""Additive M6.1 multi-session paper-replay contracts.

Legacy M6 PaperSimulationInput/PaperSimulationResult remain unchanged.
This module models complete explicit cash-equity calendar-day execution
paths across multiple sessions.  No calendar/session inference, I/O,
storage, broker integration, or operational execution occurs here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.domain.models import RiskDecision, TradePlan
from app.paper.models import PaperExecutionConfig, PaperTradeMetrics
from app.strategies.contracts import Contract, aware


D = Decimal


def _exact_utc(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is not timezone.utc
    ):
        raise ValueError("exact datetime.timezone.utc timestamp required")
    return value


def _exact_date(value: date) -> date:
    if type(value) is not date:
        raise ValueError("exact date required")
    return value


def _identifier(value: str, *, field_name: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise ValueError(f"canonical nonblank {field_name} required")
    return value


def _decimal(value, *, field_name: str, optional: bool = False):
    if optional and value is None:
        return None
    if type(value) is not Decimal:
        raise ValueError(f"exact Decimal {field_name} required")
    if not value.is_finite():
        raise ValueError(f"finite Decimal {field_name} required")
    return value


class PaperReplayBar(Contract):
    """Exact Decimal execution observation with explicit session provenance."""

    schema_version: Literal[
        "paper-replay-bar-v1"
    ] = "paper-replay-bar-v1"

    instrument_id: str
    venue_id: str
    symbol: str

    session_id: str
    market_date: date
    session_sequence: int = Field(strict=True, ge=1)

    interval_start_utc: datetime
    interval_end_utc: datetime
    available_at_utc: datetime

    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    traded_value: Decimal | None = Field(default=None, ge=0)

    source_id: str
    provenance_id: str

    @field_validator(
        "instrument_id",
        "venue_id",
        "symbol",
        "session_id",
        "source_id",
        "provenance_id",
        mode="before",
    )
    @classmethod
    def identifiers(cls, value, info):
        return _identifier(
            value,
            field_name=info.field_name,
        )

    @field_validator("market_date", mode="before")
    @classmethod
    def exact_market_date(cls, value):
        return _exact_date(value)

    @field_validator(
        "session_sequence",
        mode="before",
    )
    @classmethod
    def exact_sequence(cls, value):
        if type(value) is not int or value < 1:
            raise ValueError(
                "positive exact-int session_sequence required"
            )
        return value

    @field_validator(
        "interval_start_utc",
        "interval_end_utc",
        "available_at_utc",
        mode="before",
    )
    @classmethod
    def utc_times(cls, value):
        return _exact_utc(value)

    @field_validator(
        "open",
        "high",
        "low",
        "close",
        "volume",
        mode="before",
    )
    @classmethod
    def exact_decimals(cls, value, info):
        return _decimal(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "traded_value",
        mode="before",
    )
    @classmethod
    def exact_optional_decimal(cls, value, info):
        return _decimal(
            value,
            field_name=info.field_name,
            optional=True,
        )

    @model_validator(mode="after")
    def geometry(self):
        if self.interval_end_utc <= self.interval_start_utc:
            raise ValueError(
                "bar interval_end_utc must follow interval_start_utc"
            )

        if self.available_at_utc < self.interval_end_utc:
            raise ValueError(
                "bar cannot be available before interval end"
            )

        if self.high < max(
            self.open,
            self.close,
            self.low,
        ):
            raise ValueError(
                "bar high violates OHLC geometry"
            )

        if self.low > min(
            self.open,
            self.close,
            self.high,
        ):
            raise ValueError(
                "bar low violates OHLC geometry"
            )

        if (
            self.volume == 0
            and self.traded_value not in (None, D(0))
        ):
            raise ValueError(
                "zero-volume bar cannot have positive traded_value"
            )

        if (
            self.volume > 0
            and self.traded_value is not None
            and self.traded_value <= 0
        ):
            raise ValueError(
                "positive-volume bar requires positive traded_value "
                "when traded_value is supplied"
            )

        return self


class PaperReplayCalendarDay(Contract):
    """One explicit complete local calendar day.

    TRADING means one complete admitted execution session.
    CLOSED is explicit negative session evidence.
    """

    schema_version: Literal[
        "paper-replay-calendar-day-v1"
    ] = "paper-replay-calendar-day-v1"

    market_date: date
    state: Literal["TRADING", "CLOSED"]

    session_id: str | None = None
    opens_at_utc: datetime | None = None
    closes_at_utc: datetime | None = None
    granularity_seconds: int | None = None

    bars: tuple[PaperReplayBar, ...] = ()

    @field_validator("market_date", mode="before")
    @classmethod
    def exact_market_date(cls, value):
        return _exact_date(value)

    @field_validator("session_id", mode="before")
    @classmethod
    def canonical_session_id(cls, value):
        if value is None:
            return None
        return _identifier(
            value,
            field_name="session_id",
        )

    @field_validator(
        "opens_at_utc",
        "closes_at_utc",
        mode="before",
    )
    @classmethod
    def exact_optional_utc(cls, value):
        if value is None:
            return None
        return _exact_utc(value)

    @field_validator(
        "granularity_seconds",
        mode="before",
    )
    @classmethod
    def exact_granularity(cls, value):
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError(
                "positive exact-int granularity_seconds required"
            )
        return value

    @field_validator("bars", mode="before")
    @classmethod
    def canonical_bars(cls, value):
        if (
            type(value) is not tuple
            or any(
                type(bar) is not PaperReplayBar
                for bar in value
            )
        ):
            raise ValueError(
                "tuple of canonical PaperReplayBar required"
            )

        return tuple(
            PaperReplayBar(
                **{
                    name: getattr(bar, name)
                    for name
                    in PaperReplayBar.model_fields
                }
            )
            for bar in value
        )

    @model_validator(mode="after")
    def completeness(self):
        if self.state == "CLOSED":
            if (
                self.session_id is not None
                or self.opens_at_utc is not None
                or self.closes_at_utc is not None
                or self.granularity_seconds is not None
                or self.bars
            ):
                raise ValueError(
                    "CLOSED calendar day cannot contain session "
                    "times, granularity, or bars"
                )
            return self

        if self.session_id is None:
            raise ValueError(
                "TRADING calendar day requires session_id"
            )

        if (
            self.opens_at_utc is None
            or self.closes_at_utc is None
        ):
            raise ValueError(
                "TRADING calendar day requires explicit open/close"
            )

        if self.closes_at_utc <= self.opens_at_utc:
            raise ValueError(
                "session close must follow session open"
            )

        if self.granularity_seconds is None:
            raise ValueError(
                "TRADING calendar day requires granularity_seconds"
            )

        if not self.bars:
            raise ValueError(
                "TRADING calendar day requires complete bars"
            )

        width = timedelta(
            seconds=self.granularity_seconds
        )
        duration = (
            self.closes_at_utc
            - self.opens_at_utc
        )

        if duration % width != timedelta(0):
            raise ValueError(
                "session duration is not divisible by granularity"
            )

        expected_count = duration // width

        if len(self.bars) != expected_count:
            raise ValueError(
                "TRADING calendar day bar count is incomplete"
            )

        if self.bars[0].interval_start_utc != self.opens_at_utc:
            raise ValueError(
                "TRADING calendar day must begin at session open"
            )

        if self.bars[-1].interval_end_utc != self.closes_at_utc:
            raise ValueError(
                "TRADING calendar day must end at session close"
            )

        previous = None

        for expected_sequence, bar in enumerate(
            self.bars,
            start=1,
        ):
            if bar.market_date != self.market_date:
                raise ValueError(
                    "bar market_date does not match calendar day"
                )

            if bar.session_id != self.session_id:
                raise ValueError(
                    "bar session_id does not match calendar day"
                )

            if bar.session_sequence != expected_sequence:
                raise ValueError(
                    "session sequence must begin at 1 and be gap-free"
                )

            if (
                bar.interval_end_utc
                - bar.interval_start_utc
                != width
            ):
                raise ValueError(
                    "bar width does not match calendar-day granularity"
                )

            if previous is not None:
                if (
                    bar.interval_start_utc
                    != previous.interval_end_utc
                ):
                    raise ValueError(
                        "calendar-day bar chronology has a gap/overlap"
                    )

                if (
                    bar.available_at_utc
                    < previous.available_at_utc
                ):
                    raise ValueError(
                        "calendar-day bar availability is nonchronological"
                    )

            previous = bar

        return self


class PaperReplayInput(Contract):
    """Canonical complete multi-calendar-day M6.1 replay request."""

    schema_version: Literal[
        "paper-replay-input-v1"
    ] = "paper-replay-input-v1"

    trade_plan: TradePlan
    risk_decision: RiskDecision
    config: PaperExecutionConfig

    instrument_id: str
    venue_id: str
    market_timezone_name: str

    price_basis: Literal[
        "RAW_UNADJUSTED"
    ] = "RAW_UNADJUSTED"

    admission_time: datetime
    calendar_days: tuple[
        PaperReplayCalendarDay,
        ...
    ] = Field(min_length=1)

    # Historical execution/calendar truth is explicitly complete through
    # this as-of point.  It is not the strategy decision timestamp.
    path_complete_through_at: datetime

    @field_validator(
        "trade_plan",
        "risk_decision",
        "config",
        mode="before",
    )
    @classmethod
    def canonical_objects(cls, value, info):
        kind = {
            "trade_plan": TradePlan,
            "risk_decision": RiskDecision,
            "config": PaperExecutionConfig,
        }[info.field_name]

        if type(value) is not kind:
            raise ValueError(
                f"exact canonical {kind.__name__} required"
            )

        validated = kind.model_validate(
            {name: getattr(value, name) for name in kind.model_fields},
            strict=True,
        )
        if validated != value:
            raise ValueError(f"noncanonical {kind.__name__}; repair forbidden")
        for name in kind.model_fields:
            item = getattr(validated, name)
            if isinstance(item, Decimal) and not item.is_finite():
                raise ValueError("finite replay boundary values required")
        return validated

    @field_validator(
        "instrument_id",
        "venue_id",
        mode="before",
    )
    @classmethod
    def identifiers(cls, value, info):
        return _identifier(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "market_timezone_name",
        mode="before",
    )
    @classmethod
    def timezone_name(cls, value):
        value = _identifier(
            value,
            field_name="market_timezone_name",
        )
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                "valid IANA market timezone required"
            ) from exc
        return value

    @field_validator(
        "admission_time",
        "path_complete_through_at",
        mode="before",
    )
    @classmethod
    def exact_times(cls, value):
        return _exact_utc(value)

    @field_validator(
        "calendar_days",
        mode="before",
    )
    @classmethod
    def canonical_days(cls, value):
        if (
            type(value) is not tuple
            or not value
            or any(
                type(day) is not PaperReplayCalendarDay
                for day in value
            )
        ):
            raise ValueError(
                "nonempty tuple of canonical "
                "PaperReplayCalendarDay required"
            )

        return tuple(
            PaperReplayCalendarDay(
                **{
                    name: getattr(day, name)
                    for name
                    in PaperReplayCalendarDay.model_fields
                }
            )
            for day in value
        )

    @model_validator(mode="after")
    def consistency(self):
        p = self.trade_plan
        r = self.risk_decision

        if not (
            aware(p.created_at)
            <= self.admission_time
            < aware(p.valid_until)
        ):
            raise ValueError(
                "admission outside plan validity"
            )

        if self.path_complete_through_at < self.admission_time:
            raise ValueError(
                "path_complete_through_at cannot precede admission"
            )

        if r.trade_plan_id != p.trade_plan_id:
            raise ValueError(
                "trade_plan_id mismatch"
            )

        if p.direction.value != "LONG":
            raise ValueError(
                "unsupported SHORT paper replay"
            )

        if not (
            p.stop_price
            < p.entry_low
            <= p.entry_high
            < p.target_1
        ):
            raise ValueError(
                "invalid replay execution geometry"
            )

        zone = ZoneInfo(
            self.market_timezone_name
        )

        expected_first_date = (
            self.admission_time
            .astimezone(zone)
            .date()
        )
        expected_last_date = (
            self.path_complete_through_at
            .astimezone(zone)
            .date()
        )

        if (
            self.calendar_days[0].market_date
            != expected_first_date
        ):
            raise ValueError(
                "replay calendar must begin on admission local date"
            )

        if (
            self.calendar_days[-1].market_date
            != expected_last_date
        ):
            raise ValueError(
                "replay calendar must end on path-complete local date"
            )

        for previous, current in zip(
            self.calendar_days,
            self.calendar_days[1:],
        ):
            if (
                current.market_date
                != previous.market_date
                + timedelta(days=1)
            ):
                raise ValueError(
                    "replay calendar dates must be explicit, "
                    "ordered, and gap-free"
                )

        previous_bar = None
        session_ids = set()
        for day in self.calendar_days:
            if day.state == "CLOSED":
                continue

            if day.session_id in session_ids:
                raise ValueError("duplicate replay session_id")
            session_ids.add(day.session_id)

            if (
                day.opens_at_utc
                .astimezone(zone)
                .date()
                != day.market_date
                or day.closes_at_utc
                .astimezone(zone)
                .date()
                != day.market_date
            ):
                raise ValueError(
                    "session open/close does not match "
                    "explicit local market_date"
                )

            for bar in day.bars:
                # CLOSED dates do not reset event chronology. Delayed historical
                # evidence may share an availability timestamp, but must never
                # become known in reverse execution order across sessions.
                if previous_bar is not None:
                    if bar.interval_start_utc < previous_bar.interval_end_utc:
                        raise ValueError("replay bar chronology overlaps")
                    if bar.available_at_utc < previous_bar.available_at_utc:
                        raise ValueError("replay bar availability is nonchronological")
                previous_bar = bar

                if (
                    bar.instrument_id,
                    bar.venue_id,
                    bar.symbol,
                ) != (
                    self.instrument_id,
                    self.venue_id,
                    p.symbol,
                ):
                    raise ValueError(
                        "stable instrument/venue/symbol replay mismatch"
                    )

                if (
                    bar.interval_start_utc
                    .astimezone(zone)
                    .date()
                    != day.market_date
                    or bar.interval_end_utc
                    .astimezone(zone)
                    .date()
                    != day.market_date
                ):
                    raise ValueError(
                        "bar interval does not match local market_date"
                    )

                if (
                    bar.available_at_utc
                    > self.path_complete_through_at
                ):
                    raise ValueError(
                        "bar unavailable by path_complete_through_at"
                    )

        last = self.calendar_days[-1]

        if (
            last.state == "TRADING"
            and self.path_complete_through_at
            < last.closes_at_utc
        ):
            raise ValueError(
                "TRADING final calendar day requires "
                "complete-through coverage at least through session close"
            )

        return self


class PaperReplayFill(Contract):
    side: Literal["BUY", "SELL"]
    quantity: int = Field(strict=True, gt=0)

    raw_price: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)

    instrument_id: str
    venue_id: str
    symbol: str

    session_id: str
    market_date: date
    bar_sequence: int = Field(strict=True, ge=1)

    interval_start_utc: datetime
    interval_end_utc: datetime
    known_at_utc: datetime

    source_id: str
    provenance_id: str

    at_open: bool

    @field_validator(
        "instrument_id",
        "venue_id",
        "symbol",
        "session_id",
        "source_id",
        "provenance_id",
        mode="before",
    )
    @classmethod
    def identifiers(cls, value, info):
        return _identifier(
            value,
            field_name=info.field_name,
        )

    @field_validator("market_date", mode="before")
    @classmethod
    def exact_market_date(cls, value):
        return _exact_date(value)

    @field_validator(
        "raw_price",
        "price",
        mode="before",
    )
    @classmethod
    def exact_prices(cls, value, info):
        return _decimal(
            value,
            field_name=info.field_name,
        )

    @field_validator(
        "interval_start_utc",
        "interval_end_utc",
        "known_at_utc",
        mode="before",
    )
    @classmethod
    def exact_times(cls, value):
        return _exact_utc(value)

    @model_validator(mode="after")
    def chronology(self):
        if self.interval_end_utc <= self.interval_start_utc:
            raise ValueError(
                "fill interval end must follow start"
            )

        if self.known_at_utc < self.interval_end_utc:
            raise ValueError(
                "fill cannot be known before bar interval end"
            )

        return self


class PaperReplayPosition(Contract):
    mode: Literal["PAPER"] = "PAPER"

    trade_plan_id: UUID
    quantity: int = Field(strict=True, gt=0)

    entry: PaperReplayFill
    exit: PaperReplayFill | None = None

    state: Literal["OPEN", "CLOSED"]

    @field_validator("entry", mode="before")
    @classmethod
    def canonical_entry(cls, value):
        if type(value) is not PaperReplayFill:
            raise ValueError(
                "canonical PaperReplayFill entry required"
            )

        return PaperReplayFill(
            **{
                name: getattr(value, name)
                for name in PaperReplayFill.model_fields
            }
        )

    @field_validator("exit", mode="before")
    @classmethod
    def canonical_exit(cls, value):
        if value is None:
            return None

        if type(value) is not PaperReplayFill:
            raise ValueError(
                "canonical PaperReplayFill exit required"
            )

        return PaperReplayFill(
            **{
                name: getattr(value, name)
                for name in PaperReplayFill.model_fields
            }
        )

    @model_validator(mode="after")
    def consistency(self):
        if self.entry.side != "BUY":
            raise ValueError(
                "paper replay entry must be BUY"
            )

        if self.entry.quantity != self.quantity:
            raise ValueError(
                "position quantity does not match entry"
            )

        if self.state == "OPEN":
            if self.exit is not None:
                raise ValueError(
                    "OPEN replay position cannot have exit"
                )
            return self

        if self.exit is None:
            raise ValueError(
                "CLOSED replay position requires exit"
            )

        if self.exit.side != "SELL":
            raise ValueError(
                "paper replay exit must be SELL"
            )

        if self.exit.quantity != self.quantity:
            raise ValueError(
                "position quantity does not match exit"
            )

        if self.exit.known_at_utc < self.entry.known_at_utc:
            raise ValueError(
                "exit cannot become known before entry"
            )

        return self


class PaperReplayResult(Contract):
    schema_version: Literal[
        "paper-replay-result-v1"
    ] = "paper-replay-result-v1"

    state: Literal[
        "REJECTED",
        "INCOMPLETE",
        "OPEN",
        "COMPLETED",
        "NO_FILL",
    ]

    outcome: Literal[
        "WIN",
        "LOSS",
        "TIME_EXIT",
        "NO_FILL",
    ] | None = None

    rejection_reason: str | None = None

    exit_reason: Literal[
        "STOP",
        "TARGET_1",
        "TIME_EXIT",
        "SESSION_END",
    ] | None = None

    position: PaperReplayPosition | None = None
    metrics: PaperTradeMetrics | None = None

    @field_validator(
        "rejection_reason",
        mode="before",
    )
    @classmethod
    def canonical_reason(cls, value):
        if value is None:
            return None
        return _identifier(
            value,
            field_name="rejection_reason",
        )

    @field_validator("position", mode="before")
    @classmethod
    def canonical_position(cls, value):
        if value is None:
            return None

        if type(value) is not PaperReplayPosition:
            raise ValueError(
                "canonical PaperReplayPosition required"
            )

        return PaperReplayPosition(
            **{
                name: getattr(value, name)
                for name in PaperReplayPosition.model_fields
            }
        )

    @field_validator("metrics", mode="before")
    @classmethod
    def canonical_metrics(cls, value):
        if value is None:
            return None

        if type(value) is not PaperTradeMetrics:
            raise ValueError(
                "canonical PaperTradeMetrics required"
            )

        return PaperTradeMetrics(
            **{
                name: getattr(value, name)
                for name in PaperTradeMetrics.model_fields
            }
        )

    @model_validator(mode="after")
    def state_contract(self):
        if self.state == "REJECTED":
            if (
                self.rejection_reason is None
                or self.outcome is not None
                or self.exit_reason is not None
                or self.position is not None
                or self.metrics is not None
            ):
                raise ValueError(
                    "invalid REJECTED replay result"
                )
            return self

        if self.rejection_reason is not None:
            raise ValueError(
                "only REJECTED replay result may have rejection_reason"
            )

        if self.state == "INCOMPLETE":
            if any(
                value is not None
                for value in (
                    self.outcome,
                    self.exit_reason,
                    self.position,
                    self.metrics,
                )
            ):
                raise ValueError(
                    "invalid INCOMPLETE replay result"
                )
            return self

        if self.state == "NO_FILL":
            if (
                self.outcome != "NO_FILL"
                or self.exit_reason is not None
                or self.position is not None
                or self.metrics is not None
            ):
                raise ValueError(
                    "invalid NO_FILL replay result"
                )
            return self

        if self.state == "OPEN":
            if (
                self.outcome is not None
                or self.exit_reason is not None
                or self.metrics is not None
                or self.position is None
                or self.position.state != "OPEN"
            ):
                raise ValueError(
                    "invalid OPEN replay result"
                )
            return self

        if (
            self.position is None
            or self.position.state != "CLOSED"
            or self.position.exit is None
            or self.metrics is None
            or self.exit_reason is None
        ):
            raise ValueError(
                "COMPLETED replay result requires closed "
                "position, exit, reason, and metrics"
            )

        expected_outcome = {
            "STOP": "LOSS",
            "TARGET_1": "WIN",
            "TIME_EXIT": "TIME_EXIT",
            "SESSION_END": "TIME_EXIT",
        }[self.exit_reason]

        if self.outcome != expected_outcome:
            raise ValueError(
                "COMPLETED replay outcome does not match exit reason"
            )

        return self


__all__ = [
    "PaperReplayBar",
    "PaperReplayCalendarDay",
    "PaperReplayFill",
    "PaperReplayInput",
    "PaperReplayPosition",
    "PaperReplayResult",
]
