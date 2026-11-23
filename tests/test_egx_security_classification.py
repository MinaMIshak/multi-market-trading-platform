from uuid import uuid4

import pytest

from app.data.egx_security_classification import (
    SecurityClass,
    classify_egx_security,
    is_rehearsal_ordinary_candidate,
)
from app.data.security_master import (
    CanonicalInstrument,
    InstrumentType,
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
        source_market_date="2026-11-23",
        normalization_notes=[],
    )


@pytest.mark.parametrize(
    (
        "ticker",
        "symbol_code",
        "name",
        "expected",
    ),
    [
        (
            "ALMF",
            "EGMKM001DM19",
            "Arab Land Direct Mutual Fund",
            SecurityClass.FUND,
        ),
        (
            "CCAPP",
            "EGS73541P048",
            "QALA For Financial Investments - Preferred Shares",
            SecurityClass.PREFERRED_SHARE,
        ),
        (
            "EGREF",
            "EGS694B1C017",
            "Egyptians Real Estate Fund Certificates",
            SecurityClass.FUND_CERTIFICATE,
        ),
        (
            "EGX30ETF",
            "EGS69491M015",
            "EGX 30 INDEX ETF",
            SecurityClass.ETF,
        ),
        (
            "KASABF",
            "EGS696Z1C017",
            "Certificates Of Odin Egyptian Equity Investment Fund-KASAB",
            SecurityClass.FUND_CERTIFICATE,
        ),
        (
            "GROV",
            "EGS69921C012",
            "Grova Special Purpose Acquisition Company",
            SecurityClass.SPAC,
        ),
        (
            "OCAP",
            "EGS699Z1C014",
            "O G Capital For Investments SPAC",
            SecurityClass.SPAC,
        ),
    ],
)
def test_exact_reviewed_special_classes(
    ticker,
    symbol_code,
    name,
    expected,
):
    item = instrument(
        ticker=ticker,
        symbol_code=symbol_code,
        name=name,
    )

    assert (
        classify_egx_security(item)
        is expected
    )


@pytest.mark.parametrize(
    (
        "ticker",
        "symbol_code",
        "name",
    ),
    [
        (
            "INEG_R3",
            "EGS924P1C013",
            "Subscription Rights Of Integrated Engineering Group S.A.E -3",
        ),
        (
            "KORA_R1",
            "EGS924O1C014",
            "Subscription Rights Of KORRA -1",
        ),
        (
            "LUTS_R1",
            "EGS924K1C018",
            "Subscription Rights Of Lotus For Agricultural Invest&Dev-1",
        ),
    ],
)
def test_subscription_right_requires_matching_ticker_and_name(
    ticker,
    symbol_code,
    name,
):
    item = instrument(
        ticker=ticker,
        symbol_code=symbol_code,
        name=name,
    )

    assert (
        classify_egx_security(item)
        is SecurityClass.SUBSCRIPTION_RIGHT
    )


def test_right_ticker_without_right_name_fails_closed():
    item = instrument(
        ticker="FAKE_R1",
        symbol_code="EGS00000C001",
        name="Ordinary Looking Company",
    )

    assert (
        classify_egx_security(item)
        is SecurityClass.UNKNOWN
    )


def test_right_name_without_right_ticker_fails_closed():
    item = instrument(
        ticker="FAKE",
        symbol_code="EGS00000C002",
        name="Subscription Rights Of Fake Company -1",
    )

    assert (
        classify_egx_security(item)
        is SecurityClass.UNKNOWN
    )


def test_index_is_not_security_candidate():
    item = instrument(
        ticker="EGX30",
        symbol_code="EGX30",
        name="EGX 30",
        kind=InstrumentType.INDEX,
    )

    assert (
        classify_egx_security(item)
        is SecurityClass.INDEX
    )


def test_easb_bonds_word_is_not_security_type():
    item = instrument(
        ticker="EASB",
        symbol_code="EGS681D1C010",
        name=(
            "Egyptian Arabian(Themar)Comp. "
            "For Securities&Bonds Brok. EAC"
        ),
    )

    assert (
        classify_egx_security(item)
        is SecurityClass
        .ORDINARY_SHARE_CANDIDATE
    )


def test_normal_equity_is_only_candidate_not_certification():
    item = instrument(
        ticker="COMI",
        symbol_code="EGS60121C018",
        name=(
            "Commercial International "
            "Bank-Egypt (CIB)"
        ),
    )

    classification = (
        classify_egx_security(item)
    )

    assert (
        classification
        is SecurityClass
        .ORDINARY_SHARE_CANDIDATE
    )

    assert (
        is_rehearsal_ordinary_candidate(
            classification
        )
        is True
    )


@pytest.mark.parametrize(
    "classification",
    [
        SecurityClass.PREFERRED_SHARE,
        SecurityClass.ETF,
        SecurityClass.FUND,
        SecurityClass.FUND_CERTIFICATE,
        SecurityClass.SPAC,
        SecurityClass.SUBSCRIPTION_RIGHT,
        SecurityClass.INDEX,
        SecurityClass.UNKNOWN,
    ],
)
def test_special_or_unknown_is_not_ordinary_candidate(
    classification,
):
    assert (
        is_rehearsal_ordinary_candidate(
            classification
        )
        is False
    )
