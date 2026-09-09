import json

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.data.daily_refresh_admission import (
    DailyRefreshAdmissionError,
    DailyRefreshAdmissionPolicy,
)
from app.data.provider import ProviderResponse


EXPECTED = date(2026, 9, 10)


def make_rows(
    *,
    count,
    end_date,
):
    start = (
        end_date
        - timedelta(days=count - 1)
    )

    rows = []

    for index in range(count):
        market_date = (
            start
            + timedelta(days=index)
        )

        base = 100.0 + index / 100

        rows.append(
            {
                "date": (
                    market_date
                    .isoformat()
                ),
                "open": base,
                "high": base + 2,
                "low": base - 1,
                "close": base + 1,
                "adjusted_close": (
                    base + 1
                ),
                "volume": (
                    1000000 + index
                ),
            }
        )

    return rows


def make_response(rows):
    payload = json.dumps(
        rows,
        separators=(",", ":"),
    ).encode("utf-8")

    return ProviderResponse(
        payload=payload,
        filename="COMI.EGX-D1.json",
        source_uri=(
            "https://example.test/"
            "api/eod/COMI.EGX"
        ),
        record_count=len(rows),
        metadata={},
    )


def validate(
    policy,
    response,
):
    return (
        policy
        .validate_provider_response(
            response,
            instrument_id=str(
                uuid4()
            ),
            canonical_symbol="COMI",
            provider_symbol=(
                "COMI.EGX"
            ),
            provider="eodhd",
            snapshot_date=EXPECTED,
            expected_market_date=(
                EXPECTED
            ),
        )
    )


def test_exact_minimum_history_passes():
    policy = (
        DailyRefreshAdmissionPolicy(
            minimum_valid_bars=260
        )
    )

    rows = make_rows(
        count=260,
        end_date=EXPECTED,
    )

    result = validate(
        policy,
        make_response(rows),
    )

    assert result.record_count == 260
    assert result.valid_bar_count == 260
    assert (
        result.quarantined_bar_count
        == 0
    )

    assert (
        result.newest_market_date
        == EXPECTED
    )

    assert (
        result.newest_valid_market_date
        == EXPECTED
    )


def test_stale_history_is_rejected():
    policy = (
        DailyRefreshAdmissionPolicy()
    )

    rows = make_rows(
        count=260,
        end_date=(
            EXPECTED
            - timedelta(days=1)
        ),
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="stale",
    ):
        validate(
            policy,
            make_response(rows),
        )


def test_latest_quarantined_is_rejected():
    policy = (
        DailyRefreshAdmissionPolicy()
    )

    rows = make_rows(
        count=261,
        end_date=EXPECTED,
    )

    # Preserve the expected market date,
    # but make the newest OHLC unusable.
    rows[-1]["high"] = 90.0
    rows[-1]["low"] = 110.0

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="not executable",
    ):
        validate(
            policy,
            make_response(rows),
        )


def test_insufficient_valid_history_rejected():
    policy = (
        DailyRefreshAdmissionPolicy(
            minimum_valid_bars=260
        )
    )

    rows = make_rows(
        count=259,
        end_date=EXPECTED,
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="insufficient",
    ):
        validate(
            policy,
            make_response(rows),
        )


def test_older_quarantine_does_not_count():
    policy = (
        DailyRefreshAdmissionPolicy(
            minimum_valid_bars=260
        )
    )

    rows = make_rows(
        count=261,
        end_date=EXPECTED,
    )

    rows[0]["high"] = 90.0
    rows[0]["low"] = 110.0

    result = validate(
        policy,
        make_response(rows),
    )

    assert result.record_count == 261
    assert result.valid_bar_count == 260
    assert (
        result.quarantined_bar_count
        == 1
    )

    assert (
        result.newest_valid_market_date
        == EXPECTED
    )


def test_record_count_mismatch_rejected():
    policy = (
        DailyRefreshAdmissionPolicy()
    )

    rows = make_rows(
        count=260,
        end_date=EXPECTED,
    )

    response = make_response(rows)

    bad_response = ProviderResponse(
        payload=response.payload,
        filename=response.filename,
        source_uri=response.source_uri,
        record_count=259,
        metadata={},
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="record_count mismatch",
    ):
        validate(
            policy,
            bad_response,
        )


def test_duplicate_market_date_rejected():
    policy = (
        DailyRefreshAdmissionPolicy()
    )

    rows = make_rows(
        count=260,
        end_date=EXPECTED,
    )

    rows[-2]["date"] = (
        rows[-1]["date"]
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="duplicate",
    ):
        validate(
            policy,
            make_response(rows),
        )


def test_minimum_must_be_positive():
    with pytest.raises(
        ValueError,
        match="positive",
    ):
        DailyRefreshAdmissionPolicy(
            minimum_valid_bars=0
        )
