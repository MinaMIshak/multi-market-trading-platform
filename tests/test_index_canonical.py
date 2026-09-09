from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.index_canonical import (
    CanonicalIndexDailyBar,
    IndexBarSemanticClass,
    IndexCanonicalizationError,
    canonicalize_index_row,
    classify_index_row,
)


SHA = "a" * 64


def canonicalize(row):
    return canonicalize_index_row(
        row,
        index_name="case30",
        source_provider=(
            "EGX_OFFICIAL_PUBLIC"
        ),
        source_snapshot_date=date(
            2026,
            9,
            9,
        ),
        source_page_number=1,
        source_row_number=1,
        source_sha256=SHA,
    )


def test_full_ohlc_row_is_preserved():
    row = {
        "indexDay": (
            "2026-09-08T00:00:00"
        ),
        "indexOpen": 56627.83,
        "high": 56909.93,
        "low": 56174.30,
        "indexClose": 56174.30,
        "change": -453.53,
        "changePer": -0.8,
    }

    bar = canonicalize(row)

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .FULL_OHLC_VALID
    )

    assert (
        bar.market_date
        == date(
            2026,
            9,
            8,
        )
    )

    assert (
        bar.open
        == Decimal(
            "56627.83"
        )
    )

    assert (
        bar.high
        == Decimal(
            "56909.93"
        )
    )

    assert (
        bar.low
        == Decimal(
            "56174.3"
        )
    )

    assert (
        bar.close
        == Decimal(
            "56174.3"
        )
    )

    assert (
        bar.reference_level
        is None
    )

    assert (
        bar.quality_flags
        == ()
    )

    assert (
        bar.usable_for_full_ohlc
        is True
    )

    assert (
        bar.usable_for_close_history
        is True
    )

    assert (
        bar.index_name
        == "CASE30"
    )

    assert (
        bar.source_provider
        == "egx_official_public"
    )


def test_legacy_zero_high_low_withholds_ohlc():
    row = {
        "indexDay": (
            "1998-01-04T00:00:00"
        ),
        "indexOpen": 1000.00,
        "high": 0,
        "low": 0,
        "indexClose": 998.39,
        "change": -1.86,
        "changePer": -0.19,
    }

    bar = canonicalize(row)

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .LEGACY_CLOSE_REFERENCE
    )

    assert bar.open is None
    assert bar.high is None
    assert bar.low is None

    assert (
        bar.reference_level
        == Decimal("1000.0")
    )

    assert (
        bar.close
        == Decimal("998.39")
    )

    assert (
        "HIGH_LOW_UNAVAILABLE"
        in bar.quality_flags
    )

    assert (
        "SOURCE_OPEN_NOT_APPROVED_AS_EXECUTABLE"
        in bar.quality_flags
    )

    assert (
        bar.usable_for_full_ohlc
        is False
    )

    assert (
        bar.usable_for_close_history
        is True
    )


def test_legacy_classification_is_not_date_based():
    row = {
        "indexDay": (
            "2008-11-30T00:00:00"
        ),
        "indexOpen": 4043.72,
        "high": 0,
        "low": 0,
        "indexClose": 4205.86,
        "change": 162.14,
        "changePer": 4.01,
    }

    assert (
        classify_index_row(row)
        == IndexBarSemanticClass
        .LEGACY_CLOSE_REFERENCE
    )


def test_close_one_cent_above_high_is_quarantined_not_fixed():
    row = {
        "indexDay": (
            "2005-09-21T00:00:00"
        ),
        "indexOpen": 5058.62,
        "high": 5103.33,
        "low": 5052.42,
        "indexClose": 5103.34,
        "change": 44.72,
        "changePer": 0.88,
    }

    bar = canonicalize(row)

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .QUARANTINED_ANOMALY
    )

    assert (
        bar.close
        == Decimal("5103.34")
    )

    assert bar.open is None
    assert bar.high is None
    assert bar.low is None

    assert (
        "CLOSE_OUTSIDE_RANGE"
        in bar.quality_flags
    )

    assert (
        bar.usable_for_close_history
        is False
    )


def test_open_one_cent_below_low_is_quarantined_not_fixed():
    row = {
        "indexDay": (
            "2006-01-19T00:00:00"
        ),
        "indexOpen": 7259.75,
        "high": 7464.85,
        "low": 7259.76,
        "indexClose": 7464.85,
        "change": 205.10,
        "changePer": 2.82,
    }

    bar = canonicalize(row)

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .QUARANTINED_ANOMALY
    )

    assert (
        "OPEN_OUTSIDE_RANGE"
        in bar.quality_flags
    )


def test_legacy_open_need_not_equal_previous_close():
    row = {
        "indexDay": (
            "2003-08-11T00:00:00"
        ),
        "indexOpen": 766.16,
        "high": 0,
        "low": 0,
        "indexClose": 785.90,
        "change": 19.74,
        "changePer": 2.58,
    }

    bar = canonicalize(row)

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .LEGACY_CLOSE_REFERENCE
    )

    assert (
        bar.reference_level
        == Decimal("766.16")
    )


def test_nonpositive_close_is_hard_error():
    row = {
        "indexDay": (
            "2026-09-08T00:00:00"
        ),
        "indexOpen": 1,
        "high": 2,
        "low": 1,
        "indexClose": 0,
        "change": 0,
        "changePer": 0,
    }

    with pytest.raises(
        IndexCanonicalizationError,
        match="indexClose",
    ):
        canonicalize(row)


def test_full_model_cannot_be_constructed_without_ohlc():
    with pytest.raises(
        ValidationError,
        match="FULL_OHLC_VALID",
    ):
        CanonicalIndexDailyBar(
            index_name="CASE30",
            market_date=date(
                2026,
                9,
                8,
            ),
            semantic_class=(
                IndexBarSemanticClass
                .FULL_OHLC_VALID
            ),
            close=Decimal(
                "56174.30"
            ),
            source_provider=(
                "egx_official_public"
            ),
            source_snapshot_date=date(
                2026,
                9,
                9,
            ),
            source_page_number=1,
            source_row_number=1,
            source_sha256=SHA,
        )
