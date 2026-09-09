from datetime import date, timedelta

import pytest

from app.core.calendar_verification import (
    CalendarVerificationPolicy,
    OfficialIndexEvidence,
)
from app.domain.enums import (
    MarketSessionStatus,
)


DAY = date(2026, 9, 10)


def evidence(
    name: str,
    *,
    provider="egx_official_public",
    snapshot=DAY,
    newest=DAY,
    validated=True,
):
    return OfficialIndexEvidence(
        provider=provider,
        index_name=name,
        source_snapshot_date=snapshot,
        newest_market_date=newest,
        validated=validated,
    )


def complete():
    return [
        evidence("CASE30"),
        evidence("EGX70_EWI"),
        evidence("EGX100_EWI"),
    ]


def test_complete_official_evidence_verifies():
    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=complete(),
    )

    assert (
        decision.status
        == MarketSessionStatus.VERIFIED
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"provider": "other"},
        {"validated": False},
        {"snapshot": DAY - timedelta(days=1)},
        {"newest": DAY - timedelta(days=1)},
        {"newest": None},
    ],
)
def test_bad_required_evidence_fails_closed(
    bad,
):
    rows = complete()
    rows[0] = evidence(
        "CASE30",
        **bad,
    )

    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=rows,
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )


def test_missing_required_index_fails_closed():
    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=complete()[:-1],
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )


def test_duplicate_required_index_fails_closed():
    rows = complete()
    rows.append(
        evidence("CASE30")
    )

    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=rows,
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )


def test_unrelated_index_is_ignored():
    rows = complete()
    rows.append(
        evidence("UNRELATED")
    )

    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=rows,
    )

    assert (
        decision.status
        == MarketSessionStatus.VERIFIED
    )


def test_no_evidence_never_infers_holiday():
    decision = CalendarVerificationPolicy().evaluate(
        market_date=DAY,
        evidence=[],
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )
    assert (
        decision.status
        != MarketSessionStatus.HOLIDAY
    )
