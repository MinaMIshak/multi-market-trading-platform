import json
from datetime import date
from urllib import error

import pytest

from app.data.providers.egid import (
    EGIDAuthenticationError,
    EGIDProvider,
    EGIDResponseError,
)


class FakeEGID(EGIDProvider):
    def __init__(self, payload, token="SUPER_SECRET_TOKEN"):
        super().__init__(
            base_url="https://example.test",
            bearer_token=token,
        )
        self.payload = payload
        self.last_request = None

    def _open(self, req):
        self.last_request = req
        return self.payload


def test_valid_daily_response_sets_record_count():
    provider = FakeEGID(json.dumps([{"date": "2026-09-07"}]).encode())

    result = provider.fetch_daily_bars(
        symbol="comi.egx",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 9),
    )

    assert result.record_count == 1
    assert result.filename == "COMI.EGX-history.json"
    assert result.source_uri == (
        "https://example.test/api/DelayedFeed/getSymbolHistory"
    )
    assert "SUPER_SECRET_TOKEN" not in str(result.metadata)
    body = json.loads(provider.last_request.data.decode("utf-8"))
    assert body["SymbolCode"] == "COMI.EGX"


def test_symbol_is_stripped_and_uppercased_before_request():
    provider = FakeEGID(json.dumps([]).encode())
    provider.fetch_daily_bars(
        symbol="  comi.egx  ",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 9),
    )
    body = json.loads(provider.last_request.data.decode("utf-8"))
    assert body["SymbolCode"] == "COMI.EGX"


@pytest.mark.parametrize("symbol", ["", "   "])
def test_empty_symbol_is_rejected(symbol):
    provider = FakeEGID(json.dumps([]).encode())
    with pytest.raises(ValueError):
        provider.fetch_daily_bars(
            symbol=symbol,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_end_before_start_is_rejected():
    provider = FakeEGID(json.dumps([]).encode())
    with pytest.raises(ValueError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 9),
            end_date=date(2026, 9, 1),
        )


def test_bearer_token_is_required_for_daily_bars():
    provider = EGIDProvider(base_url="https://example.test")
    with pytest.raises(EGIDAuthenticationError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_malformed_json_daily_response_is_rejected():
    provider = FakeEGID(b"not json")
    with pytest.raises(EGIDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_non_list_daily_response_is_rejected():
    provider = FakeEGID(json.dumps({"error": "nope"}).encode())
    with pytest.raises(EGIDResponseError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_valid_security_master_response_sets_record_count():
    provider = FakeEGID(json.dumps([{"symbol": "COMI"}, {"symbol": "ETEL"}]).encode())
    result = provider.fetch_security_master()
    assert result.record_count == 2
    assert result.filename == "market-watch-names.json"
    assert result.metadata["access"] == "anonymous"


def test_non_list_security_master_response_is_rejected():
    provider = FakeEGID(json.dumps({"error": "nope"}).encode())
    with pytest.raises(EGIDResponseError):
        provider.fetch_security_master()


def test_http_401_maps_to_authentication_error(monkeypatch):
    provider = EGIDProvider(
        base_url="https://example.test", bearer_token="token"
    )

    def raise_401(req, timeout=None):
        raise error.HTTPError(
            "https://example.test", 401, "unauthorized", None, None
        )

    monkeypatch.setattr(
        "app.data.providers.egid.request.urlopen", raise_401
    )
    with pytest.raises(EGIDAuthenticationError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )


def test_http_500_maps_to_runtime_error(monkeypatch):
    provider = EGIDProvider(
        base_url="https://example.test", bearer_token="token"
    )

    def raise_500(req, timeout=None):
        raise error.HTTPError(
            "https://example.test", 500, "server error", None, None
        )

    monkeypatch.setattr(
        "app.data.providers.egid.request.urlopen", raise_500
    )
    with pytest.raises(RuntimeError):
        provider.fetch_daily_bars(
            symbol="COMI.EGX",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 9),
        )
