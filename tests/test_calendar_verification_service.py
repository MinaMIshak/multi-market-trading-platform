from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.calendar_verification import (
    CalendarVerificationDecision,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.domain.enums import (
    MarketSessionStatus,
)


DAY = date(2026, 9, 10)
NOW = datetime(
    2026,
    9,
    10,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeEvidenceRepository:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def load(self, market_date):
        self.calls.append(market_date)
        return self.evidence


class FakeTradingRepository:
    def __init__(self):
        self.saved = []

    def save_market_session(
        self,
        session,
    ):
        self.saved.append(session)


class FakePolicy:
    def __init__(self, status):
        self.status = status
        self.calls = []

    def evaluate(
        self,
        *,
        market_date,
        evidence,
    ):
        self.calls.append(
            (market_date, evidence)
        )

        return CalendarVerificationDecision(
            market_date=market_date,
            status=self.status,
            reasons=("test",),
        )


def build(status):
    evidence = [SimpleNamespace()]
    evidence_repo = FakeEvidenceRepository(
        evidence
    )
    trading_repo = FakeTradingRepository()
    policy = FakePolicy(status)

    service = CalendarVerificationService(
        evidence_repository=evidence_repo,
        trading_repository=trading_repo,
        policy=policy,
    )

    return (
        service,
        evidence_repo,
        trading_repo,
        policy,
        evidence,
    )


def test_verified_decision_is_persisted():
    (
        service,
        evidence_repo,
        trading_repo,
        policy,
        evidence,
    ) = build(
        MarketSessionStatus.VERIFIED
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert (
        decision.status
        == MarketSessionStatus.VERIFIED
    )
    assert evidence_repo.calls == [DAY]
    assert policy.calls == [
        (DAY, evidence)
    ]

    assert len(trading_repo.saved) == 1

    session = trading_repo.saved[0]

    assert session.market_date == DAY
    assert (
        session.status
        == MarketSessionStatus.VERIFIED
    )
    assert session.data_verified_at == NOW


def test_unknown_decision_is_not_persisted():
    (
        service,
        _,
        trading_repo,
        _,
        _,
    ) = build(
        MarketSessionStatus.UNKNOWN
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )
    assert trading_repo.saved == []


def test_holiday_is_not_persisted():
    (
        service,
        _,
        trading_repo,
        _,
        _,
    ) = build(
        MarketSessionStatus.HOLIDAY
    )

    decision = service.verify(
        DAY,
        verified_at=NOW,
    )

    assert (
        decision.status
        == MarketSessionStatus.HOLIDAY
    )
    assert trading_repo.saved == []


def test_naive_verified_at_fails_closed():
    (
        service,
        _,
        trading_repo,
        _,
        _,
    ) = build(
        MarketSessionStatus.VERIFIED
    )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        service.verify(
            DAY,
            verified_at=datetime(
                2026,
                9,
                10,
                12,
                0,
            ),
        )

    assert trading_repo.saved == []


def test_unknown_does_not_validate_timestamp():
    (
        service,
        _,
        trading_repo,
        _,
        _,
    ) = build(
        MarketSessionStatus.UNKNOWN
    )

    decision = service.verify(
        DAY,
        verified_at=datetime(
            2026,
            9,
            10,
            12,
            0,
        ),
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )
    assert trading_repo.saved == []
