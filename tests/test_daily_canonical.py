from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.data.daily_canonical import (
    CanonicalDailyBar,
    DailyBarSemanticClass,
    DailyCanonicalizationError,
    canonicalize_daily_row,
)


BASE = {
    "instrument_id": uuid4(),
    "canonical_symbol": "comi",
    "provider_symbol": "comi.egx",
    "market_date": date(2026, 9, 8),
    "source_provider": "EODHD",
    "source_snapshot_date": date(2026, 9, 9),
    "source_row_number": 1,
    "source_sha256": "a" * 64,
}


def test_valid_executable_ohlcv():
    bar = CanonicalDailyBar(
        **BASE,
        semantic_class=(
            DailyBarSemanticClass.VALID_EXECUTABLE
        ),
        open=Decimal("140.06"),
        high=Decimal("141"),
        low=Decimal("138.55"),
        close=Decimal("138.55"),
        volume=Decimal("3725471"),
        provider_adjusted_close_reference=Decimal("138.55"),
    )

    assert bar.canonical_symbol == "COMI"
    assert bar.provider_symbol == "COMI.EGX"
    assert bar.source_provider == "eodhd"


def test_invalid_executable_range_is_rejected():
    with pytest.raises(
        ValueError,
        match="open outside high-low range",
    ):
        CanonicalDailyBar(
            **BASE,
            semantic_class=(
                DailyBarSemanticClass.VALID_EXECUTABLE
            ),
            open=Decimal("150"),
            high=Decimal("141"),
            low=Decimal("138"),
            close=Decimal("140"),
            volume=Decimal("100"),
        )


def test_quarantine_withholds_executable_ohlcv():
    bar = CanonicalDailyBar(
        **BASE,
        semantic_class=(
            DailyBarSemanticClass.QUARANTINED_ANOMALY
        ),
        provider_adjusted_close_reference=Decimal("138.55"),
        quality_flags=("OPEN_OUTSIDE_RANGE",),
    )

    assert bar.open is None
    assert bar.high is None
    assert bar.low is None
    assert bar.close is None
    assert bar.volume is None


def test_quarantine_rejects_executable_prices():
    with pytest.raises(
        ValueError,
        match="withhold executable OHLCV",
    ):
        CanonicalDailyBar(
            **BASE,
            semantic_class=(
                DailyBarSemanticClass.QUARANTINED_ANOMALY
            ),
            close=Decimal("138.55"),
        )


def canonicalize(row):
    return canonicalize_daily_row(
        row,
        instrument_id=BASE["instrument_id"],
        canonical_symbol="COMI",
        provider_symbol="COMI.EGX",
        source_provider="eodhd",
        source_snapshot_date=date(2026, 9, 9),
        source_row_number=1,
        source_sha256="b" * 64,
    )


def test_raw_row_becomes_executable():
    bar=canonicalize({
        "date":"2026-09-08",
        "open":140.06,
        "high":141,
        "low":138.55,
        "close":138.55,
        "adjusted_close":138.55,
        "volume":3725471,
    })

    assert bar.semantic_class == (
        DailyBarSemanticClass.VALID_EXECUTABLE
    )
    assert bar.close == Decimal("138.55")
    assert bar.volume == Decimal("3725471")


def test_bad_ohlc_is_quarantined():
    bar=canonicalize({
        "date":"2026-09-08",
        "open":150,
        "high":141,
        "low":138,
        "close":140,
        "adjusted_close":140,
        "volume":100,
    })

    assert bar.semantic_class == (
        DailyBarSemanticClass.QUARANTINED_ANOMALY
    )
    assert "OPEN_OUTSIDE_RANGE" in bar.quality_flags
    assert bar.open is None
    assert bar.high is None
    assert bar.low is None
    assert bar.close is None
    assert bar.volume is None
    assert (
        bar.provider_adjusted_close_reference
        == Decimal("140")
    )


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_missing_required_field_is_rejected(field):
    row = {"date":"2026-09-08", "open":140, "high":141, "low":138,
           "close":139, "volume":100}
    del row[field]
    with pytest.raises(
        DailyCanonicalizationError,
        match="missing daily field",
    ):
        canonicalize(row)


@pytest.mark.parametrize("extra", [{}, {"adjusted_close": None}])
def test_adjusted_close_is_optional_and_never_derived(extra):
    # Provider-neutral: sources without an adjusted series remain executable;
    # the audit-only reference stays None rather than copying close.
    bar=canonicalize({"date":"2026-09-08", "open":140, "high":141,
                      "low":138, "close":139, "volume":100, **extra})
    assert bar.semantic_class == DailyBarSemanticClass.VALID_EXECUTABLE
    assert bar.provider_adjusted_close_reference is None
    assert bar.close == Decimal("139")


@pytest.mark.parametrize("value,match", [
    ("x", "invalid daily field: adjusted_close"),
    (True, "invalid daily field: adjusted_close"),
    (0, "non-positive daily field: adjusted_close"),
    ("NaN", "non-finite daily field: adjusted_close"),
])
def test_supplied_adjusted_close_is_still_validated(value, match):
    with pytest.raises(DailyCanonicalizationError, match=match):
        canonicalize({"date":"2026-09-08", "open":140, "high":141, "low":138,
                      "close":139, "volume":100, "adjusted_close": value})


def test_serialization_is_deterministic_and_preserves_provenance():
    import json
    from app.data.daily_canonical import serialize_daily_rows

    first=canonicalize({
        "date":"2026-09-08",
        "open":140,
        "high":141,
        "low":138,
        "close":139,
        "adjusted_close":139,
        "volume":100,
    })

    second=canonicalize({
        "date":"2026-09-07",
        "open":139,
        "high":140,
        "low":137,
        "close":138,
        "adjusted_close":138,
        "volume":200,
    })

    a=serialize_daily_rows([first,second])
    b=serialize_daily_rows([second,first])

    assert a == b

    doc=json.loads(a)

    assert doc[0]["market_date"] == "2026-09-07"
    assert doc[1]["market_date"] == "2026-09-08"
    assert doc[0]["source_provider"] == "eodhd"
    assert doc[0]["source_row_number"] == 1
    assert doc[0]["source_sha256"] == "b" * 64
    assert doc[0]["provider_symbol"] == "COMI.EGX"


def test_serialization_rejects_duplicate_dates():
    from app.data.daily_canonical import serialize_daily_rows

    bar=canonicalize({
        "date":"2026-09-08",
        "open":140,
        "high":141,
        "low":138,
        "close":139,
        "adjusted_close":139,
        "volume":100,
    })

    with pytest.raises(
        ValueError,
        match="duplicate market_date",
    ):
        serialize_daily_rows([bar,bar])
