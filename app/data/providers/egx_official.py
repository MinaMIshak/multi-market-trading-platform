from __future__ import annotations

import json
import math

from datetime import date
from http.cookiejar import CookieJar
from typing import Any
from urllib import (
    error,
    parse,
    request,
)

from app.data.provider import (
    ProviderBatchResponse,
    ProviderResponse,
)


class EGXOfficialProviderError(
    RuntimeError
):
    pass


class EGXOfficialResponseError(
    EGXOfficialProviderError
):
    pass


class EGXOfficialPublicProvider:
    """
    Anonymous public-data provider for the
    official Egyptian Exchange web BFF.

    Important observed behavior:

    - Pagination is one-based.
    - totalCount is usable.
    - totalPages is currently unreliable.
    - hasNextPage is currently unreliable.
    - hasPreviousPage is currently unreliable.

    Therefore pagination MUST be calculated
    from totalCount and the requested page size.
    """

    def __init__(
        self,
        *,
        base_url: str = (
            "https://beta.egx.com.eg"
        ),
        timeout_seconds: int = 30,
        opener: Any | None = None,
    ) -> None:
        self.base_url = (
            base_url.rstrip("/")
        )

        self.timeout_seconds = (
            timeout_seconds
        )

        if opener is None:
            cookie_jar = CookieJar()

            opener = (
                request.build_opener(
                    request
                    .HTTPCookieProcessor(
                        cookie_jar
                    )
                )
            )

        self._opener = opener

    @property
    def name(self) -> str:
        return "egx_official_public"

    @staticmethod
    def _normalize_index_name(
        value: str,
    ) -> str:
        value = value.strip().upper()

        if not value:
            raise ValueError(
                "index_name cannot be empty"
            )

        if len(value) > 64:
            raise ValueError(
                "index_name is too long"
            )

        return value

    def _read(
        self,
        req: request.Request,
    ) -> tuple[
        int,
        str | None,
        bytes,
    ]:
        try:
            with self._opener.open(
                req,
                timeout=self.timeout_seconds,
            ) as response:
                status = getattr(
                    response,
                    "status",
                    None,
                )

                if status is None:
                    status = (
                        response.getcode()
                    )

                content_type = (
                    response.headers.get(
                        "Content-Type"
                    )
                )

                payload = response.read()

                return (
                    int(status),
                    content_type,
                    payload,
                )

        except error.HTTPError as exc:
            raise EGXOfficialProviderError(
                "official EGX request failed "
                f"with HTTP {exc.code}"
            ) from exc

        except error.URLError as exc:
            raise EGXOfficialProviderError(
                "official EGX network "
                "request failed"
            ) from exc

    def _read_json(
        self,
        req: request.Request,
    ) -> tuple[
        bytes,
        dict[str, Any],
    ]:
        (
            status,
            content_type,
            payload,
        ) = self._read(req)

        if status != 200:
            raise EGXOfficialResponseError(
                "official EGX returned "
                f"unexpected HTTP {status}"
            )

        media_type = (
            content_type
            .split(";", 1)[0]
            .strip()
            .lower()
            if content_type
            else ""
        )

        if media_type != "application/json":
            raise EGXOfficialResponseError(
                "official EGX expected "
                "application/json but "
                f"received {content_type!r}"
            )

        try:
            parsed = json.loads(
                payload.decode("utf-8")
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise EGXOfficialResponseError(
                "official EGX returned "
                "invalid JSON"
            ) from exc

        if not isinstance(
            parsed,
            dict,
        ):
            raise EGXOfficialResponseError(
                "official EGX JSON root "
                "must be an object"
            )

        return payload, parsed

    def _since_inception_page_url(
        self,
        *,
        index_name: str,
    ) -> str:
        query = parse.urlencode(
            {
                "index": index_name,
            }
        )

        return (
            self.base_url
            + "/en/market/indices/"
            + "since-inception?"
            + query
        )

    def _warm_index_session(
        self,
        *,
        index_name: str,
    ) -> None:
        url = self._since_inception_page_url(
            index_name=index_name,
        )

        req = request.Request(
            url,
            method="GET",
            headers={
                "Accept": "text/html,*/*",
                "User-Agent": (
                    "EGX-Trading-Platform/"
                    "0.1"
                ),
            },
        )

        (
            status,
            _content_type,
            _payload,
        ) = self._read(req)

        if status != 200:
            raise EGXOfficialResponseError(
                "official EGX warm session "
                f"failed with HTTP {status}"
            )

    @staticmethod
    def _validate_page_document(
        document: dict[str, Any],
        *,
        requested_page: int,
        requested_page_size: int,
    ) -> list[dict[str, Any]]:
        if document.get(
            "success"
        ) is not True:
            raise EGXOfficialResponseError(
                "official EGX response "
                "success flag is not true"
            )

        data = document.get("data")

        if not isinstance(
            data,
            list,
        ):
            raise EGXOfficialResponseError(
                "official EGX data field "
                "must be a list"
            )

        response_page = document.get(
            "page"
        )

        if (
            not isinstance(
                response_page,
                int,
            )
            or isinstance(
                response_page,
                bool,
            )
            or response_page
            != requested_page
        ):
            raise EGXOfficialResponseError(
                "official EGX response page "
                "does not match requested "
                f"page {requested_page}"
            )

        response_page_size = (
            document.get(
                "pageSize"
            )
        )

        if (
            not isinstance(
                response_page_size,
                int,
            )
            or isinstance(
                response_page_size,
                bool,
            )
            or response_page_size
            != requested_page_size
        ):
            raise EGXOfficialResponseError(
                "official EGX response "
                "pageSize does not match "
                "requested page size"
            )

        total_count = document.get(
            "totalCount"
        )

        if (
            not isinstance(
                total_count,
                int,
            )
            or isinstance(
                total_count,
                bool,
            )
            or total_count < 0
        ):
            raise EGXOfficialResponseError(
                "official EGX totalCount "
                "must be a non-negative "
                "integer"
            )

        required_fields = {
            "change",
            "changePer",
            "high",
            "indexClose",
            "indexDay",
            "indexOpen",
            "low",
        }

        normalized_rows: list[
            dict[str, Any]
        ] = []

        for position, row in enumerate(
            data,
            start=1,
        ):
            if not isinstance(
                row,
                dict,
            ):
                raise (
                    EGXOfficialResponseError(
                        "official EGX index "
                        "row must be an object "
                        f"(row {position})"
                    )
                )

            missing = (
                required_fields
                - row.keys()
            )

            if missing:
                raise (
                    EGXOfficialResponseError(
                        "official EGX index "
                        "row is missing fields "
                        f"{sorted(missing)}"
                    )
                )

            normalized_rows.append(
                row
            )

        return normalized_rows

    def _fetch_index_page(
        self,
        *,
        index_name: str,
        start_date: date,
        end_date: date,
        page_number: int,
        page_size: int,
    ) -> tuple[
        ProviderResponse,
        dict[str, Any],
    ]:
        query = parse.urlencode(
            {
                "indexName": index_name,
            }
        )

        url = (
            self.base_url
            + "/api/bff/egx/"
            + "index-since-inception?"
            + query
        )

        body = json.dumps(
            {
                "pageNumber": page_number,
                "pageSize": page_size,
                "fromDate": (
                    start_date.isoformat()
                    + "T00:00:00Z"
                ),
                "toDate": (
                    end_date.isoformat()
                    + "T23:59:59Z"
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")

        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Accept": (
                    "application/json"
                ),
                "Content-Type": (
                    "application/json"
                ),
                "Origin": self.base_url,
                "Referer": (
                    self._since_inception_page_url(
                        index_name=index_name,
                    )
                ),
                "User-Agent": (
                    "EGX-Trading-Platform/"
                    "0.1"
                ),
                "x-egx-bff-request": "1",
                "x-egx-bff-client": "web",
            },
        )

        payload, document = (
            self._read_json(req)
        )

        rows = (
            self._validate_page_document(
                document,
                requested_page=(
                    page_number
                ),
                requested_page_size=(
                    page_size
                ),
            )
        )

        filename = (
            f"{index_name}-"
            f"{start_date.isoformat()}-"
            f"{end_date.isoformat()}-"
            f"page-{page_number:04d}.json"
        )

        response = ProviderResponse(
            payload=payload,
            filename=filename,
            source_uri=url,
            record_count=len(rows),
            metadata={
                "access": "anonymous",
                "endpoint": (
                    "index-since-inception"
                ),
                "index_name": index_name,
                "start_date": (
                    start_date.isoformat()
                ),
                "end_date": (
                    end_date.isoformat()
                ),
                "page_number": (
                    page_number
                ),
                "page_size": page_size,
                "total_count": (
                    document.get(
                        "totalCount"
                    )
                ),
                # Observed but deliberately
                # not trusted for pagination.
                "reported_total_pages": (
                    document.get(
                        "totalPages"
                    )
                ),
                "reported_has_next_page": (
                    document.get(
                        "hasNextPage"
                    )
                ),
                "reported_has_previous_page": (
                    document.get(
                        "hasPreviousPage"
                    )
                ),
            },
        )

        return response, document

    def fetch_index_bars(
        self,
        *,
        index_name: str,
        start_date: date,
        end_date: date,
        page_size: int = 1000,
    ) -> ProviderBatchResponse:
        index_name = (
            self._normalize_index_name(
                index_name
            )
        )

        if end_date < start_date:
            raise ValueError(
                "end_date cannot be "
                "before start_date"
            )

        if (
            isinstance(
                page_size,
                bool,
            )
            or not isinstance(
                page_size,
                int,
            )
            or not 1 <= page_size <= 1000
        ):
            raise ValueError(
                "page_size must be "
                "between 1 and 1000"
            )

        self._warm_index_session(
            index_name=index_name
        )

        first_response, first_document = (
            self._fetch_index_page(
                index_name=index_name,
                start_date=start_date,
                end_date=end_date,
                page_number=1,
                page_size=page_size,
            )
        )

        total_count = int(
            first_document[
                "totalCount"
            ]
        )

        calculated_page_count = (
            math.ceil(
                total_count
                / page_size
            )
            if total_count
            else 1
        )

        responses = [
            first_response
        ]

        for page_number in range(
            2,
            calculated_page_count + 1,
        ):
            (
                page_response,
                page_document,
            ) = self._fetch_index_page(
                index_name=index_name,
                start_date=start_date,
                end_date=end_date,
                page_number=page_number,
                page_size=page_size,
            )

            page_total_count = (
                page_document.get(
                    "totalCount"
                )
            )

            if (
                page_total_count
                != total_count
            ):
                raise (
                    EGXOfficialResponseError(
                        "official EGX "
                        "totalCount changed "
                        "during pagination"
                    )
                )

            responses.append(
                page_response
            )

        fetched_count = sum(
            response.record_count or 0
            for response in responses
        )

        if fetched_count != total_count:
            raise EGXOfficialResponseError(
                "official EGX fetched row "
                "count does not match "
                "totalCount: "
                f"{fetched_count} != "
                f"{total_count}"
            )

        return ProviderBatchResponse(
            responses=tuple(
                responses
            ),
            record_count=total_count,
            metadata={
                "provider": self.name,
                "asset": "index_bars",
                "index_name": (
                    index_name
                ),
                "start_date": (
                    start_date.isoformat()
                ),
                "end_date": (
                    end_date.isoformat()
                ),
                "page_size": page_size,
                "calculated_page_count": (
                    calculated_page_count
                ),
                "pagination_basis": (
                    "ceil(totalCount/"
                    "pageSize)"
                ),
                "reported_total_pages": (
                    first_document.get(
                        "totalPages"
                    )
                ),
                "reported_has_next_page": (
                    first_document.get(
                        "hasNextPage"
                    )
                ),
            },
        )
