from __future__ import annotations

import json

from datetime import date

import pytest

from app.data.providers.egx_official import (
    EGXOfficialPublicProvider,
    EGXOfficialResponseError,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        content_type: str,
        payload: bytes,
    ) -> None:
        self.status = status

        self.headers = {
            "Content-Type": content_type,
        }

        self._payload = payload

    def __enter__(
        self,
    ) -> "FakeResponse":
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        return None

    def read(
        self,
    ) -> bytes:
        return self._payload


class FakeOpener:
    def __init__(
        self,
        responses: list[
            FakeResponse
        ],
    ) -> None:
        self.responses = list(
            responses
        )

        self.requests = []

    def open(
        self,
        req,
        *,
        timeout: int,
    ) -> FakeResponse:
        self.requests.append(
            req
        )

        if not self.responses:
            raise AssertionError(
                "unexpected request"
            )

        return self.responses.pop(0)


def index_row(
    day: str,
) -> dict:
    return {
        "change": 1.0,
        "changePer": 0.1,
        "high": 101.0,
        "indexClose": 100.0,
        "indexDay": day,
        "indexOpen": 99.0,
        "low": 98.0,
    }


def json_response(
    *,
    page: int,
    page_size: int,
    total_count: int,
    rows: list[dict],
    reported_total_pages: int = 1,
    reported_has_next_page: bool = False,
) -> FakeResponse:
    payload = json.dumps(
        {
            "data": rows,
            "totalCount": (
                total_count
            ),
            "page": page,
            "pageSize": page_size,
            "totalPages": (
                reported_total_pages
            ),
            "hasNextPage": (
                reported_has_next_page
            ),
            "hasPreviousPage": False,
            "message": (
                "Data retrieved "
                "successfully."
            ),
            "success": True,
        }
    ).encode("utf-8")

    return FakeResponse(
        status=200,
        content_type=(
            "application/json"
        ),
        payload=payload,
    )


def warm_response() -> FakeResponse:
    return FakeResponse(
        status=200,
        content_type="text/html",
        payload=b"<html></html>",
    )


def test_warm_up_url_and_post_referer_cannot_drift_apart():
    # Both are built from the same _since_inception_page_url helper; this
    # locks in that the warm-up GET and the POST's Referer header can never
    # silently diverge from each other after a future URL-path edit.
    opener = FakeOpener(
        [
            warm_response(),
            json_response(
                page=1,
                page_size=2,
                total_count=1,
                rows=[
                    index_row(
                        "2026-09-08T00:00:00"
                    ),
                ],
            ),
        ]
    )

    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            ),
            opener=opener,
        )
    )

    provider.fetch_index_bars(
        index_name="CASE30",
        start_date=date(1998, 1, 1),
        end_date=date(2026, 9, 9),
        page_size=2,
    )

    warm_request, post_request = (
        opener.requests[0],
        opener.requests[1],
    )

    assert (
        warm_request.full_url
        == post_request.get_header(
            "Referer"
        )
    )


def test_pagination_uses_total_count_not_broken_metadata():
    opener = FakeOpener(
        [
            warm_response(),
            json_response(
                page=1,
                page_size=2,
                total_count=5,
                rows=[
                    index_row(
                        "2026-09-08T00:00:00"
                    ),
                    index_row(
                        "2026-09-07T00:00:00"
                    ),
                ],
                reported_total_pages=2,
                reported_has_next_page=False,
            ),
            json_response(
                page=2,
                page_size=2,
                total_count=5,
                rows=[
                    index_row(
                        "2026-09-06T00:00:00"
                    ),
                    index_row(
                        "2026-09-03T00:00:00"
                    ),
                ],
                reported_total_pages=2,
                reported_has_next_page=False,
            ),
            json_response(
                page=3,
                page_size=2,
                total_count=5,
                rows=[
                    index_row(
                        "2026-09-02T00:00:00"
                    ),
                ],
                reported_total_pages=2,
                reported_has_next_page=False,
            ),
        ]
    )

    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            ),
            opener=opener,
        )
    )

    batch = provider.fetch_index_bars(
        index_name="CASE30",
        start_date=date(
            1998,
            1,
            1,
        ),
        end_date=date(
            2026,
            9,
            9,
        ),
        page_size=2,
    )

    assert batch.record_count == 5
    assert len(batch.responses) == 3

    assert (
        batch.metadata[
            "calculated_page_count"
        ]
        == 3
    )

    assert (
        batch.metadata[
            "reported_total_pages"
        ]
        == 2
    )

    assert (
        batch.metadata[
            "reported_has_next_page"
        ]
        is False
    )

    post_requests = [
        req
        for req in opener.requests
        if req.get_method()
        == "POST"
    ]

    assert len(
        post_requests
    ) == 3

    requested_pages = [
        json.loads(
            req.data.decode(
                "utf-8"
            )
        )["pageNumber"]
        for req in post_requests
    ]

    assert requested_pages == [
        1,
        2,
        3,
    ]


def test_rejects_html_even_with_http_200():
    opener = FakeOpener(
        [
            warm_response(),
            FakeResponse(
                status=200,
                content_type=(
                    "text/html; "
                    "charset=utf-8"
                ),
                payload=(
                    b"<title>"
                    b"Request Rejected"
                    b"</title>"
                ),
            ),
        ]
    )

    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            ),
            opener=opener,
        )
    )

    with pytest.raises(
        EGXOfficialResponseError,
        match="application/json",
    ):
        provider.fetch_index_bars(
            index_name="CASE30",
            start_date=date(
                2026,
                1,
                1,
            ),
            end_date=date(
                2026,
                9,
                9,
            ),
        )


def test_rejects_total_count_change_during_pagination():
    opener = FakeOpener(
        [
            warm_response(),
            json_response(
                page=1,
                page_size=2,
                total_count=3,
                rows=[
                    index_row(
                        "2026-09-08T00:00:00"
                    ),
                    index_row(
                        "2026-09-07T00:00:00"
                    ),
                ],
            ),
            json_response(
                page=2,
                page_size=2,
                total_count=4,
                rows=[
                    index_row(
                        "2026-09-06T00:00:00"
                    ),
                ],
            ),
        ]
    )

    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            ),
            opener=opener,
        )
    )

    with pytest.raises(
        EGXOfficialResponseError,
        match="totalCount changed",
    ):
        provider.fetch_index_bars(
            index_name="CASE30",
            start_date=date(
                2026,
                1,
                1,
            ),
            end_date=date(
                2026,
                9,
                9,
            ),
            page_size=2,
        )


@pytest.mark.parametrize(
    "page_size",
    [
        0,
        1001,
        True,
    ],
)
def test_rejects_invalid_page_size(
    page_size,
):
    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            )
        )
    )

    with pytest.raises(
        ValueError,
        match="page_size",
    ):
        provider.fetch_index_bars(
            index_name="CASE30",
            start_date=date(
                2026,
                1,
                1,
            ),
            end_date=date(
                2026,
                9,
                9,
            ),
            page_size=page_size,
        )


def test_rejects_invalid_date_range():
    provider = (
        EGXOfficialPublicProvider(
            base_url=(
                "https://example.test"
            )
        )
    )

    with pytest.raises(
        ValueError,
        match="end_date",
    ):
        provider.fetch_index_bars(
            index_name="CASE30",
            start_date=date(
                2026,
                9,
                9,
            ),
            end_date=date(
                2026,
                1,
                1,
            ),
        )
