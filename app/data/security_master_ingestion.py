from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from app.data.models import (
    DataAssetType,
    IngestionStatus,
    RawArtifactManifest,
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
    ) -> RawArtifactManifest:
        ...


class SecurityMasterProvider(Protocol):
    @property
    def name(self) -> str:
        ...

    def fetch_security_master(self) -> Any:
        ...


@dataclass(frozen=True)
class SecurityMasterIngestionResult:
    provider: str
    snapshot_date: date

    manifest: RawArtifactManifest

    record_count: int


class SecurityMasterIngestor:
    """
    Persist a provider security-master snapshot
    as a single immutable raw artifact.

    This layer deliberately does NOT:
    - canonicalize instruments;
    - resolve or assign aliases;
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
        provider: SecurityMasterProvider,
        snapshot_date: date,
    ) -> SecurityMasterIngestionResult:
        if type(snapshot_date) is not date:
            raise ValueError(
                "snapshot_date must be "
                "a calendar date"
            )

        provider_name = provider.name

        if (
            not isinstance(provider_name, str)
            or not provider_name
            or provider_name
            != provider_name.strip().lower()
            or provider_name == "canonical"
        ):
            raise ValueError(
                "security master provider "
                "name must be canonical"
            )

        response = (
            provider.fetch_security_master()
        )

        if provider.name != provider_name:
            raise ValueError(
                "security master provider "
                "identity changed during fetch"
            )

        if response.record_count is None:
            raise ValueError(
                "security master provider "
                "record_count is required"
            )

        if (
            type(response.record_count) is not int
            or response.record_count < 0
        ):
            raise ValueError(
                "security master provider "
                "record_count must be a "
                "nonnegative integer"
            )

        manifest = self.raw_store.store_bytes(
            provider=provider_name,
            asset_type=(
                DataAssetType.SECURITY_MASTER
            ),
            payload=response.payload,
            filename=response.filename,
            market_date=snapshot_date,
            record_count=(
                response.record_count
            ),
        )

        ledger_metadata = {
            "snapshot_date": (
                snapshot_date.isoformat()
            ),
            "response": dict(
                response.metadata
            ),
        }

        persisted_manifest = (
            self.repository.save_manifest(
                manifest,
                status=(
                    IngestionStatus.RECEIVED
                ),
                source_uri=(
                    response.source_uri
                ),
                metadata=ledger_metadata,
            )
        )

        return SecurityMasterIngestionResult(
            provider=provider_name,
            snapshot_date=snapshot_date,
            manifest=persisted_manifest,
            record_count=(
                response.record_count
            ),
        )
