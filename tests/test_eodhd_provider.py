import json
from datetime import date

import pytest

from app.data.providers.eodhd import (
    EODHDAuthenticationError,
    EODHDProvider,
    EODHDResponseError,
)


def row(day="2026-09-07"):
    return {
        "date": day,
        "open": 142,
        "high": 142.8,
        "low": 140.06,
        "close": 140.06,
        "adjusted_close": 140.06,
        "volume": 3867177,
    }


class FakeEODHD(EODHDProvider):
    def __init__(self, payload, token="SUPER_SECRET_TOKEN"):
        super().__init__(
            base_url="https://example.test",
            api_token=token,
        )
        self.payload = payload

    def _read(self, req):
        return self.payload


def test_valid_daily_response_and_secret_boundary():
    provider = FakeEODHD(
        json.dumps([row()]).encode()
    )

    result = provider.fetch_daily_bars(
        symbol="comi.egx",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 9),
    )

    assert result.record_count == 1
    assert result.filename.startswith("COMI.EGX-")
    assert result.source_uri == (
        "https://example.test/api/eod/COMI.EGX"
    )
    assert "SUPER_SECRET_TOKEN" not in result.source_uri
    assert "SUPER_SECRET_TOKEN" not in str(result.metadata)


def test_token_is_required():
    provider = EODHDProvider()

    with pytest.raises(EODHDAuthenticationError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_invalid_schema_is_rejected():
    provider = FakeEODHD(
        json.dumps([{"date": "2026-09-07"}]).encode()
    )

    with pytest.raises(EODHDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_duplicate_date_is_rejected():
    payload = json.dumps([
        row("2026-09-07"),
        row("2026-09-07"),
    ]).encode()

    provider = FakeEODHD(payload)

    with pytest.raises(EODHDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_unsorted_dates_are_rejected():
    payload = json.dumps([
        row("2026-09-08"),
        row("2026-09-07"),
    ]).encode()

    provider = FakeEODHD(payload)

    with pytest.raises(EODHDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "adjusted_close", "volume"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_market_values_are_rejected(field, value):
    observation = row()
    observation[field] = value
    provider = FakeEODHD(json.dumps([observation]).encode())
    with pytest.raises(EODHDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "adjusted_close"])
def test_non_positive_field_error_does_not_claim_non_finite(field):
    # A plain int like close=0 is neither NaN nor Inf; the raised message must
    # say so precisely, not blame finiteness for an ordinary threshold miss.
    observation = row()
    observation[field] = 0
    provider = FakeEODHD(json.dumps([observation]).encode())
    with pytest.raises(EODHDResponseError, match=f"non-positive EODHD field: {field}"):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_negative_volume_error_does_not_claim_non_finite():
    observation = row()
    observation["volume"] = -100
    provider = FakeEODHD(json.dumps([observation]).encode())
    with pytest.raises(EODHDResponseError, match="negative EODHD volume"):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_finite_zero_volume_is_preserved():
    observation = row()
    observation["volume"] = 0
    result = FakeEODHD(json.dumps([observation]).encode()).fetch_daily_bars(
        symbol="COMI.EGX",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 9),
    )
    assert result.record_count == 1
