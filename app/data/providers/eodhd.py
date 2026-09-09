from __future__ import annotations

import json
from datetime import date
from urllib import error, parse, request

from app.data.provider import ProviderResponse


class EODHDProviderError(RuntimeError):
    pass


class EODHDAuthenticationError(EODHDProviderError):
    pass


class EODHDResponseError(EODHDProviderError):
    pass


_REQUIRED = {
    "date",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
}


class EODHDProvider:
    def __init__(
        self,
        *,
        base_url: str = "https://eodhd.com",
        api_token: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip() if api_token else None
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "eodhd"

    def _read(self, req: request.Request) -> bytes:
        try:
            with request.urlopen(
                req,
                timeout=self.timeout_seconds,
            ) as response:
                return response.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise EODHDAuthenticationError(
                    "EODHD authentication or subscription required "
                    f"(HTTP {exc.code})"
                ) from exc
            if exc.code == 429:
                raise EODHDProviderError(
                    "EODHD rate limit exceeded (HTTP 429)"
                ) from exc
            raise EODHDProviderError(
                f"EODHD request failed with HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            raise EODHDProviderError(
                "EODHD network request failed"
            ) from exc

    def _validate_daily_payload(
        self,
        payload: bytes,
        *,
        start_date: date,
        end_date: date,
    ) -> int:
        try:
            doc = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EODHDResponseError(
                "invalid EODHD JSON response"
            ) from exc

        if not isinstance(doc, list):
            raise EODHDResponseError(
                "EODHD daily response must be a list"
            )

        previous: date | None = None
        seen: set[date] = set()

        for row in doc:
            if not isinstance(row, dict) or not _REQUIRED <= row.keys():
                raise EODHDResponseError(
                    "invalid EODHD daily row schema"
                )

            try:
                market_date = date.fromisoformat(str(row["date"]))
            except ValueError as exc:
                raise EODHDResponseError(
                    "invalid EODHD market date"
                ) from exc

            if not start_date <= market_date <= end_date:
                raise EODHDResponseError(
                    "EODHD row outside requested date range"
                )

            if market_date in seen:
                raise EODHDResponseError(
                    "duplicate EODHD market date"
                )

            if previous is not None and market_date <= previous:
                raise EODHDResponseError(
                    "EODHD daily rows are not strictly ordered"
                )

            for field in ("open", "high", "low", "close", "adjusted_close"):
                value = row[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EODHDResponseError(
                        f"invalid EODHD numeric field: {field}"
                    )
                if value <= 0:
                    raise EODHDResponseError(
                        f"non-positive EODHD field: {field}"
                    )

            volume = row["volume"]
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise EODHDResponseError(
                    "invalid EODHD volume"
                )
            if volume < 0:
                raise EODHDResponseError(
                    "negative EODHD volume"
                )

            seen.add(market_date)
            previous = market_date

        return len(doc)

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResponse:
        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol cannot be empty")
        if end_date < start_date:
            raise ValueError("end_date cannot be before start_date")
        if not self.api_token:
            raise EODHDAuthenticationError(
                "EODHD API token is required"
            )

        encoded_symbol = parse.quote(symbol, safe="")
        endpoint = f"{self.base_url}/api/eod/{encoded_symbol}"

        url = endpoint + "?" + parse.urlencode({
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "api_token": self.api_token,
            "fmt": "json",
        })

        payload = self._read(
            request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "EGX-Trading-Platform/0.1",
                },
            )
        )

        record_count = self._validate_daily_payload(
            payload,
            start_date=start_date,
            end_date=end_date,
        )

        return ProviderResponse(
            payload=payload,
            filename=(
                f"{symbol}-{start_date.isoformat()}-"
                f"{end_date.isoformat()}-D1.json"
            ),
            source_uri=endpoint,
            record_count=record_count,
            metadata={
                "endpoint": "eod",
                "symbol": symbol,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjusted_close": "provider_supplied_reference_only",
            },
        )
