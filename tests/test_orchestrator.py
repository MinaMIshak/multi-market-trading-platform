from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import (
    CalendarTruth,
    CheckpointName,
    MarketSessionOrchestrator,
    SessionPhase,
)


CAIRO = ZoneInfo("Africa/Cairo")


def dt(
    hour: int,
    minute: int,
) -> datetime:
    return datetime(
        2026,
        9,
        9,
        hour,
        minute,
        tzinfo=CAIRO,
    )


def test_session_phases():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    assert (
        orchestrator.session_phase(
            dt(9, 30)
        )
        == SessionPhase.PRE_MARKET
    )

    assert (
        orchestrator.session_phase(
            dt(10, 5)
        )
        == SessionPhase.OPENING_RANGE
    )

    assert (
        orchestrator.session_phase(
            dt(11, 0)
        )
        == SessionPhase.REGULAR
    )

    assert (
        orchestrator.session_phase(
            dt(14, 0)
        )
        == SessionPhase.PRE_CLOSE
    )

    assert (
        orchestrator.session_phase(
            dt(15, 0)
        )
        == SessionPhase.POST_CLOSE
    )


def test_first15_requires_verified_day():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    result = orchestrator.evaluate(
        now=dt(10, 16),
        market_date=dt(10, 16).date(),
        calendar_truth=(
            CalendarTruth.UNVERIFIED
        ),
        completed_jobs={
            CheckpointName.CALENDAR_MAINTENANCE,
            CheckpointName.MASTER_HEALTH_0945,
            CheckpointName.SESSION_HEALTH_0950,
            CheckpointName.CALENDAR_LIVE_1005,
            CheckpointName.CALENDAR_LIVE_1008,
            CheckpointName.CALENDAR_LIVE_1011,
            CheckpointName.CALENDAR_LIVE_1013,
            CheckpointName.SESSION_HEALTH_1014,
        },
    )

    blocked_names = {
        item.name
        for item in result.blocked
    }

    assert (
        CheckpointName.D1_OPEN
        in blocked_names
    )

    assert (
        CheckpointName.FIRST15_SHADOW
        in blocked_names
    )


def test_verified_day_allows_first15():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    result = orchestrator.evaluate(
        now=dt(10, 16),
        market_date=dt(10, 16).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs={
            CheckpointName.CALENDAR_MAINTENANCE,
            CheckpointName.MASTER_HEALTH_0945,
            CheckpointName.SESSION_HEALTH_0950,
            CheckpointName.CALENDAR_LIVE_1005,
            CheckpointName.CALENDAR_LIVE_1008,
            CheckpointName.CALENDAR_LIVE_1011,
            CheckpointName.CALENDAR_LIVE_1013,
            CheckpointName.SESSION_HEALTH_1014,
        },
    )

    due_names = {
        item.name
        for item in result.due
    }

    assert (
        CheckpointName.D1_OPEN
        in due_names
    )

    assert (
        CheckpointName.FIRST15_SHADOW
        in due_names
    )


def test_non_trading_day_blocks_trade_jobs():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    result = orchestrator.evaluate(
        now=dt(10, 16),
        market_date=dt(10, 16).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_NON_TRADING_DAY
        ),
        completed_jobs={
            CheckpointName.CALENDAR_MAINTENANCE,
            CheckpointName.MASTER_HEALTH_0945,
            CheckpointName.SESSION_HEALTH_0950,
            CheckpointName.CALENDAR_LIVE_1005,
            CheckpointName.CALENDAR_LIVE_1008,
            CheckpointName.CALENDAR_LIVE_1011,
            CheckpointName.CALENDAR_LIVE_1013,
            CheckpointName.SESSION_HEALTH_1014,
        },
    )

    blocked_names = {
        item.name
        for item in result.blocked
    }

    assert (
        CheckpointName.D1_OPEN
        in blocked_names
    )

    assert (
        CheckpointName.FIRST15_SHADOW
        in blocked_names
    )


def test_fallback_skipped_after_primary_success():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    result = orchestrator.evaluate(
        now=dt(18, 15),
        market_date=dt(18, 15).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs={
            checkpoint.name
            for checkpoint
            in orchestrator.policy.checkpoints
            if (
                checkpoint.at
                < dt(18, 15).time()
            )
        },
    )

    # Explicitly guarantee primary is recorded successful.
    completed = {
        checkpoint.name
        for checkpoint
        in orchestrator.policy.checkpoints
        if (
            checkpoint.at
            < dt(18, 15).time()
        )
    }

    completed.add(
        CheckpointName.AFTER_SESSION_PRIMARY
    )

    result = orchestrator.evaluate(
        now=dt(18, 15),
        market_date=dt(18, 15).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=completed,
    )

    skipped_names = {
        item.name
        for item in result.skipped
    }

    assert (
        CheckpointName.AFTER_SESSION_FALLBACK
        in skipped_names
    )


def test_fallback_runs_if_primary_not_completed():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    completed = {
        checkpoint.name
        for checkpoint
        in orchestrator.policy.checkpoints
        if (
            checkpoint.at
            < dt(16, 15).time()
        )
    }

    result = orchestrator.evaluate(
        now=dt(18, 15),
        market_date=dt(18, 15).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=completed,
    )

    due_names = {
        item.name
        for item in result.due
    }

    assert (
        CheckpointName.AFTER_SESSION_FALLBACK
        in due_names
    )


def test_old_open_job_becomes_missed():
    orchestrator = (
        MarketSessionOrchestrator()
    )

    completed = {
        checkpoint.name
        for checkpoint
        in orchestrator.policy.checkpoints
        if (
            checkpoint.at
            < dt(10, 15).time()
        )
    }

    result = orchestrator.evaluate(
        now=dt(11, 0),
        market_date=dt(11, 0).date(),
        calendar_truth=(
            CalendarTruth
            .VERIFIED_TRADING_DAY
        ),
        completed_jobs=completed,
    )

    missed_names = {
        item.name
        for item in result.missed
    }

    assert (
        CheckpointName.D1_OPEN
        in missed_names
    )
