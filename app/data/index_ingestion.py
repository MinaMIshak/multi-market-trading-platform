from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from app.data.models import (
    BarGranularity,
    DataAssetType,
    IngestionStatus,
    RawArtifactManifest,
)
from app.data.provider import (
    IndexDataProvider,
    ProviderBatchResponse,
)
from app.data.raw_store import (
    ImmutableRawStore,
)


class ManifestRepository(Protocol):
    def save_manifest(
        self,
        manifest: RawArtifactManifest,
        *,
        status: IngestionStatus = (
            IngestionStatus.RECEIVED
        ),
        source_uri: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class IndexIngestionResult:
    provider: str
    index_name: str
    snapshot_date: date

    requested_start_date: date
    requested_end_date: date

    manifests: tuple[
        RawArtifactManifest,
        ...,
    ]

    record_count: int

    batch_metadata: dict[
        str,
        Any,
    ]


class IndexHistoryIngestor:
    """
    Persist provider index-history responses
    as immutable raw artifacts.

    This layer deliberately does NOT:
    - canonicalize bars;
    - forward-fill missing sessions;
    - adjust prices;
    - modify provider payloads.

    Provider bytes are preserved exactly.
    """

    def __init__(
        self,
        *,
        raw_store: ImmutableRawStore,
        repository: ManifestRepository,
    ) -> None:
        self.raw_store = raw_store
        self.repository = repository

    def ingest(
        self,
        *,
        provider: IndexDataProvider,
        index_name: str,
        start_date: date,
        end_date: date,
        snapshot_date: date,
        page_size: int = 1000,
    ) -> IndexIngestionResult:
        index_name = (
            index_name.strip().upper()
        )

        if not index_name:
            raise ValueError(
                "index_name cannot be empty"
            )

        if end_date < start_date:
            raise ValueError(
                "end_date cannot be "
                "before start_date"
            )

        batch: ProviderBatchResponse = (
            provider.fetch_index_bars(
                index_name=index_name,
                start_date=start_date,
                end_date=end_date,
                page_size=page_size,
            )
        )

        expected_count = (
            batch.record_count
        )

        if expected_count is None:
            raise ValueError(
                "index provider batch "
                "record_count is required"
            )

        response_count = sum(
            response.record_count or 0
            for response in batch.responses
        )

        if response_count != expected_count:
            raise ValueError(
                "provider batch record "
                "count mismatch: "
                f"{response_count} != "
                f"{expected_count}"
            )

        manifests: list[
            RawArtifactManifest
        ] = []

        for response in batch.responses:
            manifest = (
                self.raw_store.store_bytes(
                    provider=provider.name,
                    asset_type=(
                        DataAssetType
                        .INDEX_BARS
                    ),
                    payload=response.payload,
                    filename=(
                        response.filename
                    ),
                    market_date=(
                        snapshot_date
                    ),
                    symbol=index_name,
                    granularity=(
                        BarGranularity.D1
                    ),
                    record_count=(
                        response.record_count
                    ),
                )
            )

            ledger_metadata = {
                "snapshot_date": (
                    snapshot_date
                    .isoformat()
                ),
                "requested_start_date": (
                    start_date.isoformat()
                ),
                "requested_end_date": (
                    end_date.isoformat()
                ),
                "batch": dict(
                    batch.metadata
                ),
                "response": dict(
                    response.metadata
                ),
            }

            self.repository.save_manifest(
                manifest,
                status=(
                    IngestionStatus
                    .RECEIVED
                ),
                source_uri=(
                    response.source_uri
                ),
                metadata=ledger_metadata,
            )

            manifests.append(
                manifest
            )

        return IndexIngestionResult(
            provider=provider.name,
            index_name=index_name,
            snapshot_date=(
                snapshot_date
            ),
            requested_start_date=(
                start_date
            ),
            requested_end_date=end_date,
            manifests=tuple(
                manifests
            ),
            record_count=expected_count,
            batch_metadata=dict(
                batch.metadata
            ),
        )
