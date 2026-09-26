from __future__ import annotations

from datetime import time
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class CalendarTruth(StrEnum):
    VERIFIED_TRADING_DAY = "VERIFIED_TRADING_DAY"
    VERIFIED_NON_TRADING_DAY = "VERIFIED_NON_TRADING_DAY"
    UNVERIFIED = "UNVERIFIED"


class SessionPhase(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    OPENING_RANGE = "OPENING_RANGE"
    REGULAR = "REGULAR"
    PRE_CLOSE = "PRE_CLOSE"
    POST_CLOSE = "POST_CLOSE"


class CheckpointName(StrEnum):
    CALENDAR_MAINTENANCE = "CALENDAR_MAINTENANCE"

    MASTER_HEALTH_0945 = "MASTER_HEALTH_0945"
    SESSION_HEALTH_0950 = "SESSION_HEALTH_0950"

    CALENDAR_LIVE_1005 = "CALENDAR_LIVE_1005"
    CALENDAR_LIVE_1008 = "CALENDAR_LIVE_1008"
    CALENDAR_LIVE_1011 = "CALENDAR_LIVE_1011"
    CALENDAR_LIVE_1013 = "CALENDAR_LIVE_1013"

    SESSION_HEALTH_1014 = "SESSION_HEALTH_1014"

    D1_OPEN = "D1_OPEN"
    FIRST15_SHADOW = "FIRST15_SHADOW"
    MARKET_REGIME_SHADOW = "MARKET_REGIME_SHADOW"

    SESSION_HEALTH_1023 = "SESSION_HEALTH_1023"

    PRECLOSE_SHADOW_1350 = "PRECLOSE_SHADOW_1350"
    PRECLOSE_SHADOW_1405 = "PRECLOSE_SHADOW_1405"

    SESSION_HEALTH_1415 = "SESSION_HEALTH_1415"

    AFTER_SESSION_PRIMARY = "AFTER_SESSION_PRIMARY"
    AFTER_SESSION_FALLBACK = "AFTER_SESSION_FALLBACK"

    EGX_SCAN_PRIMARY = "EGX_SCAN_PRIMARY"
    EGX_SCAN_FALLBACK = "EGX_SCAN_FALLBACK"

    MASTER_HEALTH_1845 = "MASTER_HEALTH_1845"


class ScheduledCheckpoint(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    name: CheckpointName
    at: time

    # How long after scheduled time the task may still start.
    max_lateness_minutes: int = Field(
        ge=0,
        le=360,
    )

    # Trading logic cannot run until calendar truth says
    # this is a verified trading day.
    requires_verified_trading_day: bool = False

    # Used for jobs such as the 18:15 fallback.
    fallback_for: CheckpointName | None = None


def _default_checkpoints() -> list[ScheduledCheckpoint]:
    return [
        ScheduledCheckpoint(
            name=CheckpointName.CALENDAR_MAINTENANCE,
            at=time(8, 30),
            max_lateness_minutes=180,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.MASTER_HEALTH_0945,
            at=time(9, 45),
            max_lateness_minutes=15,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.SESSION_HEALTH_0950,
            at=time(9, 50),
            max_lateness_minutes=15,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.CALENDAR_LIVE_1005,
            at=time(10, 5),
            max_lateness_minutes=3,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.CALENDAR_LIVE_1008,
            at=time(10, 8),
            max_lateness_minutes=3,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.CALENDAR_LIVE_1011,
            at=time(10, 11),
            max_lateness_minutes=2,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.CALENDAR_LIVE_1013,
            at=time(10, 13),
            max_lateness_minutes=2,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.SESSION_HEALTH_1014,
            at=time(10, 14),
            max_lateness_minutes=10,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.D1_OPEN,
            at=time(10, 15),
            max_lateness_minutes=10,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.FIRST15_SHADOW,
            at=time(10, 16),
            max_lateness_minutes=15,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.MARKET_REGIME_SHADOW,
            at=time(10, 21),
            max_lateness_minutes=30,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.SESSION_HEALTH_1023,
            at=time(10, 23),
            max_lateness_minutes=15,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.PRECLOSE_SHADOW_1350,
            at=time(13, 50),
            max_lateness_minutes=15,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.PRECLOSE_SHADOW_1405,
            at=time(14, 5),
            max_lateness_minutes=15,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.SESSION_HEALTH_1415,
            at=time(14, 15),
            max_lateness_minutes=15,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.AFTER_SESSION_PRIMARY,
            at=time(16, 15),
            max_lateness_minutes=120,
            requires_verified_trading_day=True,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.AFTER_SESSION_FALLBACK,
            at=time(18, 15),
            max_lateness_minutes=60,
            requires_verified_trading_day=True,
            fallback_for=CheckpointName.AFTER_SESSION_PRIMARY,
        ),
        ScheduledCheckpoint(
            name=CheckpointName.MASTER_HEALTH_1845,
            at=time(18, 45),
            max_lateness_minutes=30,
        ),
    ]


class MarketSchedulePolicy(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    timezone: str = "Africa/Cairo"

    market_open: time = time(10, 0)
    first15_complete: time = time(10, 15)
    pre_close_start: time = time(13, 50)
    market_close: time = time(14, 30)

    checkpoints: list[ScheduledCheckpoint] = Field(
        default_factory=_default_checkpoints
    )
