from datetime import date

from app.core.holiday_verification import (
    HolidayAuthority,
    HolidayVerificationPolicy,
    OfficialHolidayEvidence,
)
from app.domain.enums import MarketSessionStatus


THURSDAY = date(2026, 7, 2)
TUESDAY = date(2026, 6, 30)


def item(
    authority,
    *,
    observed=THURSDAY,
    closed=True,
    nominal=TUESDAY,
):
    return OfficialHolidayEvidence(
        authority=authority,
        observed_date=observed,
        market_closed=closed,
        nominal_date=nominal,
    )


def test_explicit_egx_closure_verifies_holiday():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[
            item(HolidayAuthority.EGX_OFFICIAL)
        ],
    )

    assert decision.status == MarketSessionStatus.HOLIDAY


def test_shifted_holiday_uses_observed_date():
    evidence = [
        item(HolidayAuthority.EGX_OFFICIAL)
    ]

    thursday = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=evidence,
    )
    tuesday = HolidayVerificationPolicy().evaluate(
        market_date=TUESDAY,
        evidence=evidence,
    )

    assert thursday.status == MarketSessionStatus.HOLIDAY
    assert tuesday.status == MarketSessionStatus.UNKNOWN


def test_government_holiday_alone_is_not_enough():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[
            item(
                HolidayAuthority.EGYPT_GOVERNMENT
            )
        ],
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert decision.reasons == (
        "awaiting_egx_closure_confirmation",
    )


def test_wrong_observed_date_is_ignored():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[
            item(
                HolidayAuthority.EGX_OFFICIAL,
                observed=date(2026, 7, 3),
            )
        ],
    )

    assert decision.status == MarketSessionStatus.UNKNOWN


def test_egx_nonclosure_does_not_create_holiday():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[
            item(
                HolidayAuthority.EGX_OFFICIAL,
                closed=False,
            )
        ],
    )

    assert decision.status == MarketSessionStatus.UNKNOWN


def test_conflicting_egx_evidence_fails_closed():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[
            item(HolidayAuthority.EGX_OFFICIAL),
            item(
                HolidayAuthority.EGX_OFFICIAL,
                closed=False,
            ),
        ],
    )

    assert decision.status == MarketSessionStatus.UNKNOWN


def test_empty_evidence_never_infers_holiday():
    decision = HolidayVerificationPolicy().evaluate(
        market_date=THURSDAY,
        evidence=[],
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert decision.status != MarketSessionStatus.HOLIDAY
