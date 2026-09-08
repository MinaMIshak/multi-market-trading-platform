from app.data.security_master import (
    InstrumentType,
    build_canonical_security_master,
)


def test_reuters_normalization():
    records = [
        {
            "SYMBOL_CODE":
                "EGS735N2C012",
            "Reuters":
                "NDRL.CA.",
            "ENG_NAME":
                "National Drilling",
            "ARB_NAME":
                None,
            "eng_shortname":
                "National Drilling",
            "arb_shortname":
                None,
        }
    ]

    instruments = (
        build_canonical_security_master(
            records=records,
            source_sha256="a" * 64,
            source_market_date=(
                "2026-09-09"
            ),
        )
    )

    instrument = instruments[0]

    assert (
        instrument.instrument_type
        == InstrumentType.EQUITY
    )

    assert (
        instrument.canonical_ticker
        == "NDRL"
    )

    assert (
        instrument.reuters_raw
        == "NDRL.CA."
    )

    assert (
        instrument.reuters_normalized
        == "NDRL.CA"
    )

    assert (
        "REUTERS_TRAILING_DOT_REMOVED"
        in instrument.normalization_notes
    )


def test_known_index_without_reuters():
    records = [
        {
            "SYMBOL_CODE": "EGX30",
            "Reuters": None,
            "ENG_NAME": None,
            "ARB_NAME": None,
            "eng_shortname": None,
            "arb_shortname": None,
        }
    ]

    instrument = (
        build_canonical_security_master(
            records=records,
            source_sha256="b" * 64,
            source_market_date=(
                "2026-09-09"
            ),
        )[0]
    )

    assert (
        instrument.instrument_type
        == InstrumentType.INDEX
    )

    assert (
        instrument.canonical_ticker
        == "EGX30"
    )
