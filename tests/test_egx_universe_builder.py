from datetime import (
    date,
    datetime,
    timezone,
)
from uuid import uuid4

import pytest

from app.data.egx_universe_builder import (
    build_egx_universe_candidate,
)
from app.data.security_master import (
    CanonicalInstrument,
    InstrumentType,
)


DAY = date(2026, 11, 23)

NOW = datetime(
    2026,
    11,
    23,
    20,
    30,
    tzinfo=timezone.utc,
)


def instrument(
    *,
    ticker,
    symbol_code,
    name,
    kind=InstrumentType.EQUITY,
):
    return CanonicalInstrument(
        instrument_id=uuid4(),
        instrument_type=kind,
        canonical_ticker=ticker,
        name_en=name,
        name_ar=None,
        short_name_en=None,
        short_name_ar=None,
        source_provider="egid",
        source_symbol_code=symbol_code,
        reuters_raw=(
            f"{ticker}.CA"
            if kind is InstrumentType.EQUITY
            else None
        ),
        reuters_normalized=(
            f"{ticker}.CA"
            if kind is InstrumentType.EQUITY
            else None
        ),
        source_sha256="a" * 64,
        source_market_date=DAY.isoformat(),
        normalization_notes=[],
    )


def test_builder_separates_membership_and_eligibility():
    comi = instrument(
        ticker="COMI",
        symbol_code="EGS60121C018",
        name=(
            "Commercial International "
            "Bank-Egypt (CIB)"
        ),
    )

    preferred = instrument(
        ticker="CCAPP",
        symbol_code="EGS73541P048",
        name=(
            "QALA For Financial Investments "
            "- Preferred Shares"
        ),
    )

    right = instrument(
        ticker="KORA_R1",
        symbol_code="EGS924O1C014",
        name=(
            "Subscription Rights Of KORRA -1"
        ),
    )

    index = instrument(
        ticker="EGX30",
        symbol_code="EGX30",
        name="EGX 30",
        kind=InstrumentType.INDEX,
    )

    universe = build_egx_universe_candidate(
        instruments=(
            index,
            preferred,
            right,
            comi,
        ),
        effective_date=DAY,
        published_at=NOW,
    )

    by_symbol = {
        member.symbol: member
        for member in universe.members
    }

    assert set(by_symbol) == {
        "COMI",
        "CCAPP",
        "KORA_R1",
    }

    assert (
        by_symbol["COMI"].eligible
        is True
    )

    assert (
        by_symbol["CCAPP"].eligible
        is False
    )

    assert (
        by_symbol["KORA_R1"].eligible
        is False
    )

    assert "EGX30" not in by_symbol


@pytest.mark.parametrize(
    (
        "ticker",
        "symbol_code",
        "name",
    ),
    [
        (
            "ALMF",
            "EGMKM001DM19",
            "Arab Land Direct Mutual Fund",
        ),
        (
            "CCAPP",
            "EGS73541P048",
            (
                "QALA For Financial "
                "Investments - Preferred Shares"
            ),
        ),
        (
            "EGREF",
            "EGS694B1C017",
            (
                "Egyptians Real Estate "
                "Fund Certificates"
            ),
        ),
        (
            "EGX30ETF",
            "EGS69491M015",
            "EGX 30 INDEX ETF",
        ),
        (
            "KASABF",
            "EGS696Z1C017",
            (
                "Certificates Of Odin Egyptian "
                "Equity Investment Fund-KASAB"
            ),
        ),
        (
            "GROV",
            "EGS69921C012",
            (
                "Grova Special Purpose "
                "Acquisition Company"
            ),
        ),
        (
            "OCAP",
            "EGS699Z1C014",
            (
                "O G Capital For "
                "Investments SPAC"
            ),
        ),
    ],
)
def test_reviewed_special_class_is_explicitly_ineligible(
    ticker,
    symbol_code,
    name,
):
    item = instrument(
        ticker=ticker,
        symbol_code=symbol_code,
        name=name,
    )

    ordinary = instrument(
        ticker="COMI",
        symbol_code="EGS60121C018",
        name=(
            "Commercial International "
            "Bank-Egypt (CIB)"
        ),
    )

    universe = build_egx_universe_candidate(
        instruments=(ordinary, item),
        effective_date=DAY,
        published_at=NOW,
    )

    by_symbol = {
        member.symbol: member
        for member in universe.members
    }

    assert (
        by_symbol[ticker].eligible
        is False
    )


def test_easb_remains_ordinary_candidate():
    easb = instrument(
        ticker="EASB",
        symbol_code="EGS681D1C010",
        name=(
            "Egyptian Arabian(Themar)Comp. "
            "For Securities&Bonds Brok. EAC"
        ),
    )

    universe = build_egx_universe_candidate(
        instruments=(easb,),
        effective_date=DAY,
        published_at=NOW,
    )

    assert (
        universe.members[0].symbol
        == "EASB"
    )

    assert (
        universe.members[0].eligible
        is True
    )


def test_unknown_security_class_fails_closed():
    ambiguous = instrument(
        ticker="FAKE_R1",
        symbol_code="EGS00000C001",
        name="Ordinary Looking Company",
    )

    with pytest.raises(
        ValueError,
        match="unknown EGX security class",
    ):
        build_egx_universe_candidate(
            instruments=(ambiguous,),
            effective_date=DAY,
            published_at=NOW,
        )


def test_empty_snapshot_fails_closed():
    with pytest.raises(
        ValueError,
        match="non-empty",
    ):
        build_egx_universe_candidate(
            instruments=(),
            effective_date=DAY,
            published_at=NOW,
        )


def test_all_ineligible_snapshot_fails_closed():
    right = instrument(
        ticker="INEG_R3",
        symbol_code="EGS924P1C013",
        name=(
            "Subscription Rights Of "
            "Integrated Engineering "
            "Group S.A.E -3"
        ),
    )

    with pytest.raises(
        ValueError,
        match="no eligible securities",
    ):
        build_egx_universe_candidate(
            instruments=(right,),
            effective_date=DAY,
            published_at=NOW,
        )
