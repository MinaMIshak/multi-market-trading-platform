from datetime import date
from types import SimpleNamespace

import pytest

from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.schedule import CalendarTruth
from app.domain.enums import (
    MarketSessionStatus,
)


MARKET_DATE = date(2026, 9, 10)


class FakeRepository:
    def __init__(self, session):
        self.session = session
        self.calls = []

    def get_market_session(
        self,
        market_date,
    ):
        self.calls.append(market_date)
        return self.session


def test_missing_session_is_unverified():
    repository = FakeRepository(None)

    truth = CalendarTruthResolver(
        repository
    ).resolve(
        MARKET_DATE
    )

    assert truth == CalendarTruth.UNVERIFIED
    assert repository.calls == [
        MARKET_DATE
    ]


def test_verified_maps_to_trading_day():
    repository = FakeRepository(
        SimpleNamespace(
            status=(
                MarketSessionStatus.VERIFIED
            )
        )
    )

    truth = CalendarTruthResolver(
        repository
    ).resolve(
        MARKET_DATE
    )

    assert (
        truth
        == CalendarTruth
        .VERIFIED_TRADING_DAY
    )


def test_holiday_maps_to_non_trading_day():
    repository = FakeRepository(
        SimpleNamespace(
            status=(
                MarketSessionStatus.HOLIDAY
            )
        )
    )

    truth = CalendarTruthResolver(
        repository
    ).resolve(
        MARKET_DATE
    )

    assert (
        truth
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )


@pytest.mark.parametrize(
    "status",
    [
        MarketSessionStatus.PRE_MARKET,
        MarketSessionStatus.OPEN,
        MarketSessionStatus.FIRST15_COMPLETE,
        MarketSessionStatus.CLOSED,
        MarketSessionStatus.DATA_PENDING,
        MarketSessionStatus.UNKNOWN,
    ],
)
def test_non_authoritative_states_fail_closed(
    status,
):
    repository = FakeRepository(
        SimpleNamespace(
            status=status
        )
    )

    truth = CalendarTruthResolver(
        repository
    ).resolve(
        MARKET_DATE
    )

    assert truth == CalendarTruth.UNVERIFIED
