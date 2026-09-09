from __future__ import annotations

from datetime import date

import pytest

from app.data.index_ingestion import (
    IndexHistoryIngestor,
)
from app.data.models import (
    IngestionStatus,
)
from app.data.provider import (
    ProviderBatchResponse,
    ProviderResponse,
)
from app.data.raw_store import (
    ImmutableRawStore,
)


class FakeRepository:
    def __init__(self) -> None:
        self.saved = []

    def save_manifest(
        self,
        manifest,
        *,
        status=IngestionStatus.RECEIVED,
        source_uri=None,
        metadata=None,
    ) -> None:
        self.saved.append(
            {
                "manifest": manifest,
                "status": status,
                "source_uri": (
                    source_uri
                ),
                "metadata": metadata,
            }
        )


class FakeIndexProvider:
    name = "egx_official_public"

    def __init__(
        self,
        batch,
    ) -> None:
        self.batch = batch
        self.calls = []

    def fetch_index_bars(
        self,
        *,
        index_name,
        start_date,
        end_date,
        page_size=1000,
    ):
        self.calls.append(
            {
                "index_name": (
                    index_name
                ),
                "start_date": (
                    start_date
                ),
                "end_date": (
                    end_date
                ),
                "page_size": (
                    page_size
                ),
            }
        )

        return self.batch


def make_batch(
    *,
    first_payload=b'{"page":1}',
    second_payload=b'{"page":2}',
):
    return ProviderBatchResponse(
        responses=(
            ProviderResponse(
                payload=(
                    first_payload
                ),
                filename=(
                    "CASE30-page-0001.json"
                ),
                source_uri=(
                    "https://example/"
                    "page1"
                ),
                record_count=2,
                metadata={
                    "page_number": 1,
                },
            ),
            ProviderResponse(
                payload=(
                    second_payload
                ),
                filename=(
                    "CASE30-page-0002.json"
                ),
                source_uri=(
                    "https://example/"
                    "page2"
                ),
                record_count=1,
                metadata={
                    "page_number": 2,
                },
            ),
        ),
        record_count=3,
        metadata={
            "calculated_page_count": 2,
            "pagination_basis": (
                "ceil(totalCount/"
                "pageSize)"
            ),
        },
    )


def test_ingests_pages_as_immutable_raw_artifacts(
    tmp_path,
):
    raw_store = ImmutableRawStore(
        tmp_path / "raw"
    )

    repository = FakeRepository()

    provider = FakeIndexProvider(
        make_batch()
    )

    ingestor = IndexHistoryIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    result = ingestor.ingest(
        provider=provider,
        index_name="case30",
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
        snapshot_date=date(
            2026,
            9,
            9,
        ),
        page_size=1000,
    )

    assert result.index_name == "CASE30"
    assert result.record_count == 3

    assert len(
        result.manifests
    ) == 2

    assert len(
        repository.saved
    ) == 2

    assert all(
        item["status"]
        == IngestionStatus.RECEIVED
        for item in repository.saved
    )

    assert (
        repository.saved[0]
        ["metadata"]
        ["snapshot_date"]
        == "2026-09-09"
    )

    assert (
        provider.calls[0]
        ["index_name"]
        == "CASE30"
    )

    files = sorted(
        (tmp_path / "raw")
        .rglob("*.json")
    )

    assert len(files) == 2

    assert files[0].read_bytes() in {
        b'{"page":1}',
        b'{"page":2}',
    }


def test_same_payload_is_idempotent(
    tmp_path,
):
    raw_store = ImmutableRawStore(
        tmp_path / "raw"
    )

    repository = FakeRepository()

    provider = FakeIndexProvider(
        make_batch()
    )

    ingestor = IndexHistoryIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    kwargs = {
        "provider": provider,
        "index_name": "CASE30",
        "start_date": date(
            1998,
            1,
            1,
        ),
        "end_date": date(
            2026,
            9,
            9,
        ),
        "snapshot_date": date(
            2026,
            9,
            9,
        ),
    }

    first = ingestor.ingest(
        **kwargs
    )

    second = ingestor.ingest(
        **kwargs
    )

    assert [
        manifest.raw_path
        for manifest in first.manifests
    ] == [
        manifest.raw_path
        for manifest in second.manifests
    ]

    assert [
        manifest.sha256
        for manifest in first.manifests
    ] == [
        manifest.sha256
        for manifest in second.manifests
    ]

    files = list(
        (tmp_path / "raw")
        .rglob("*.json")
    )

    assert len(files) == 2


def test_changed_payload_cannot_overwrite_snapshot(
    tmp_path,
):
    raw_store = ImmutableRawStore(
        tmp_path / "raw"
    )

    repository = FakeRepository()

    first_provider = (
        FakeIndexProvider(
            make_batch()
        )
    )

    ingestor = IndexHistoryIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    kwargs = {
        "index_name": "CASE30",
        "start_date": date(
            1998,
            1,
            1,
        ),
        "end_date": date(
            2026,
            9,
            9,
        ),
        "snapshot_date": date(
            2026,
            9,
            9,
        ),
    }

    ingestor.ingest(
        provider=first_provider,
        **kwargs,
    )

    changed_provider = (
        FakeIndexProvider(
            make_batch(
                first_payload=(
                    b'{"page":1,'
                    b'"revised":true}'
                )
            )
        )
    )

    with pytest.raises(
        FileExistsError,
        match=(
            "immutable raw "
            "artifact conflict"
        ),
    ):
        ingestor.ingest(
            provider=changed_provider,
            **kwargs,
        )


def test_rejects_provider_count_mismatch(
    tmp_path,
):
    batch = ProviderBatchResponse(
        responses=(
            ProviderResponse(
                payload=b"{}",
                filename="page.json",
                record_count=1,
            ),
        ),
        record_count=2,
    )

    provider = FakeIndexProvider(
        batch
    )

    ingestor = IndexHistoryIngestor(
        raw_store=(
            ImmutableRawStore(
                tmp_path / "raw"
            )
        ),
        repository=(
            FakeRepository()
        ),
    )

    with pytest.raises(
        ValueError,
        match="record count mismatch",
    ):
        ingestor.ingest(
            provider=provider,
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
            snapshot_date=date(
                2026,
                9,
                9,
            ),
        )

    assert not (
        tmp_path / "raw"
    ).exists()


def test_rejects_missing_batch_record_count(
    tmp_path,
):
    batch = ProviderBatchResponse(
        responses=(),
        record_count=None,
    )

    provider = FakeIndexProvider(
        batch
    )

    ingestor = IndexHistoryIngestor(
        raw_store=(
            ImmutableRawStore(
                tmp_path / "raw"
            )
        ),
        repository=(
            FakeRepository()
        ),
    )

    with pytest.raises(
        ValueError,
        match="record_count",
    ):
        ingestor.ingest(
            provider=provider,
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
            snapshot_date=date(
                2026,
                9,
                9,
            ),
        )
