from datetime import date
from types import SimpleNamespace

import pytest

from app.core.calendar_maintenance_job import (
    CalendarMaintenanceJob,
)
from app.core.schedule import CalendarTruth
from app.domain.enums import MarketSessionStatus


DAY = date(2026, 9, 10)


class Recorder:
    def __init__(self):
        self.calls = []


class FakeBase:
    def __init__(self, recorder, status):
        self.recorder = recorder
        self.status = status

    def apply(self, market_date):
        self.recorder.calls.append(
            ("base", market_date)
        )
        return self.status


class FakeHoliday:
    def __init__(self, recorder, status):
        self.recorder = recorder
        self.status = status

    def promote(self, market_date):
        self.recorder.calls.append(
            ("holiday", market_date)
        )
        return SimpleNamespace(
            verification=SimpleNamespace(
                status=self.status
            )
        )


class FakeResolver:
    def __init__(self, recorder, truth):
        self.recorder = recorder
        self.truth = truth

    def resolve(self, market_date):
        self.recorder.calls.append(
            ("resolve", market_date)
        )
        return self.truth


def build(
    *,
    base=MarketSessionStatus.UNKNOWN,
    holiday=MarketSessionStatus.UNKNOWN,
    truth=CalendarTruth.UNVERIFIED,
):
    recorder = Recorder()

    job = CalendarMaintenanceJob(
        base_service=FakeBase(
            recorder,
            base,
        ),
        holiday_promotion_service=FakeHoliday(
            recorder,
            holiday,
        ),
        truth_resolver=FakeResolver(
            recorder,
            truth,
        ),
    )

    return job, recorder


def test_runs_in_authority_order():
    job, recorder = build()

    result = job.run(DAY)

    assert recorder.calls == [
        ("base", DAY),
        ("holiday", DAY),
        ("resolve", DAY),
    ]

    assert result.market_date == DAY


def test_weekend_truth_is_reported():
    job, _ = build(
        base=MarketSessionStatus.WEEKEND,
        truth=(
            CalendarTruth
            .VERIFIED_NON_TRADING_DAY
        ),
    )

    result = job.run(DAY)

    assert (
        result.base_status
        == MarketSessionStatus.WEEKEND
    )
    assert (
        result.calendar_truth
        == CalendarTruth.VERIFIED_NON_TRADING_DAY
    )


def test_verified_holiday_truth_is_reported():
    job, _ = build(
        holiday=MarketSessionStatus.HOLIDAY,
        truth=(
            CalendarTruth
            .VERIFIED_NON_TRADING_DAY
        ),
    )

    result = job.run(DAY)

    assert (
        result.holiday_status
        == MarketSessionStatus.HOLIDAY
    )
    assert (
        result.calendar_truth
        == CalendarTruth.VERIFIED_NON_TRADING_DAY
    )


def test_plain_weekday_stays_unverified():
    job, _ = build()

    result = job.run(DAY)

    assert (
        result.calendar_truth
        == CalendarTruth.UNVERIFIED
    )


def test_failure_is_not_silently_swallowed():
    class FailingBase:
        def apply(self, market_date):
            raise RuntimeError(
                "base calendar failure"
            )

    job = CalendarMaintenanceJob(
        base_service=FailingBase(),
        holiday_promotion_service=object(),
        truth_resolver=object(),
    )

    with pytest.raises(
        RuntimeError,
        match="base calendar failure",
    ):
        job.run(DAY)
