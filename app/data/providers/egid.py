from __future__ import annotations

import json
from datetime import date
from urllib import error, parse, request

from app.data.models import (
    BarGranularity,
)
from app.data.provider import (
    ProviderResponse,
)


class EGIDAuthenticationError(
    RuntimeError
):
    pass


class EGIDProvider:
    def __init__(
        self,
        *,
        base_url: str = (
            "https://ticker.egidegypt.com"
        ),
        bearer_token: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = (
            base_url.rstrip("/")
        )

        self.bearer_token = (
            bearer_token.strip()
            if bearer_token
            else None
        )

        self.timeout_seconds = (
            timeout_seconds
        )

    @property
    def name(self) -> str:
        return "egid"

    def _headers(
        self,
        *,
        authenticated: bool,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "EGX-Trading-Platform/0.1"
            ),
        }

        if authenticated:
            if not self.bearer_token:
                raise EGIDAuthenticationError(
                    "EGID bearer token "
                    "is required"
                )

            headers["Authorization"] = (
                "Bearer "
                + self.bearer_token
            )

        return headers

    def _open(
        self,
        req: request.Request,
    ) -> bytes:
        try:
            with request.urlopen(
                req,
                timeout=self.timeout_seconds,
            ) as response:
                return response.read()

        except error.HTTPError as exc:
            if exc.code in {
                401,
                403,
            }:
                raise (
                    EGIDAuthenticationError(
                        "EGID authentication "
                        "or subscription required "
                        f"(HTTP {exc.code})"
                    )
                ) from exc

            raise RuntimeError(
                "EGID request failed "
                f"with HTTP {exc.code}"
            ) from exc

        except error.URLError as exc:
            raise RuntimeError(
                "EGID network request failed"
            ) from exc

    def fetch_security_master(
        self,
    ) -> ProviderResponse:
        url = (
            self.base_url
            + "/api/DelayedFeed/"
            + "getAllMarketWatchNames"
        )

        req = request.Request(
            url,
            method="GET",
            headers=self._headers(
                authenticated=False
            ),
        )

        payload = self._open(req)

        parsed = json.loads(
            payload.decode("utf-8")
        )

        if not isinstance(
            parsed,
            list,
        ):
            raise ValueError(
                "unexpected EGID security "
                "master response type"
            )

        return ProviderResponse(
            payload=payload,
            filename=(
                "market-watch-names.json"
            ),
            source_uri=url,
            record_count=len(parsed),
            metadata={
                "endpoint": (
                    "getAllMarketWatchNames"
                ),
                "access": "anonymous",
            },
        )

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResponse:
        if end_date < start_date:
            raise ValueError(
                "end_date cannot be "
                "before start_date"
            )

        url = (
            self.base_url
            + "/api/DelayedFeed/"
            + "getSymbolHistory"
        )

        body = json.dumps(
            {
                "SymbolCode": symbol,
                "Skip": 0,
                "Take": 100000,
                "FromDate": (
                    start_date.isoformat()
                    + "T00:00:00"
                ),
                "ToDate": (
                    end_date.isoformat()
                    + "T23:59:59"
                ),
            }
        ).encode("utf-8")

        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                **self._headers(
                    authenticated=True
                ),
                "Content-Type": (
                    "application/json"
                ),
            },
        )

        payload = self._open(req)

        return ProviderResponse(
            payload=payload,
            filename=(
                f"{symbol.upper()}-history.json"
            ),
            source_uri=url,
            metadata={
                "endpoint": (
                    "getSymbolHistory"
                ),
                "start_date": (
                    start_date.isoformat()
                ),
                "end_date": (
                    end_date.isoformat()
                ),
            },
        )

    def fetch_intraday_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        granularity: BarGranularity,
    ) -> ProviderResponse:
        raise NotImplementedError(
            "EGID intraday historical "
            "contract has not yet been "
            "validated"
        )

    def fetch_corporate_actions(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResponse:
        raise NotImplementedError(
            "EGID corporate actions "
            "endpoint has not yet been "
            "validated"
        )
