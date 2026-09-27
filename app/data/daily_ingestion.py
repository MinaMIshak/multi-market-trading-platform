from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from app.data.index_ingestion import ManifestRepository
from app.data.models import (
    BarGranularity,
    DataAssetType,
    IngestionStatus,
    RawArtifactManifest,
)
from app.data.provider import MarketDataProvider
from app.data.raw_store import ImmutableRawStore


class SymbolResolver(Protocol):
    def resolve(
        self,
        value: str,
        *,
        provider: str | None = None,
    ) -> dict:
        ...


@dataclass(frozen=True)
class DailyBarIngestionResult:
    provider: str
    canonical_symbol: str
    provider_symbol: str
    instrument_id: str
    snapshot_date: date
    requested_start_date: date
    requested_end_date: date
    manifest: RawArtifactManifest
    record_count: int
    response_metadata: dict[str, Any]


class DailyBarIngestor:
    """
    Persist provider daily-bar bytes exactly as received.

    This layer does NOT:
    - infer provider symbols;
    - parse or canonicalize bars;
    - adjust prices;
    - validate market semantics;
    - promote canonical artifacts.
    """

    def __init__(
        self,
        *,
        raw_store: ImmutableRawStore,
        repository: ManifestRepository,
        resolver: SymbolResolver,
        admission_policy: Any | None = None,
    ) -> None:
        self.raw_store = raw_store
        self.repository = repository
        self.resolver = resolver
        self.admission_policy = admission_policy

    def ingest(
        self,
        *,
        provider: MarketDataProvider,
        canonical_symbol: str,
        provider_symbol: str,
        start_date: date,
        end_date: date,
        snapshot_date: date,
    ) -> DailyBarIngestionResult:
        canonical_symbol = canonical_symbol.strip().upper()
        provider_symbol = provider_symbol.strip().upper()

        if not canonical_symbol:
            raise ValueError("canonical_symbol cannot be empty")
        if not provider_symbol:
            raise ValueError("provider_symbol cannot be empty")
        if end_date < start_date:
            raise ValueError(
                "end_date cannot be before start_date"
            )

        instrument = self.resolver.resolve(
            canonical_symbol,
            provider="canonical",
        )

        resolved = str(
            instrument["canonical_ticker"]
        ).strip().upper()

        if resolved != canonical_symbol:
            raise ValueError(
                "resolved canonical ticker mismatch"
            )

        instrument_id = str(
            instrument["instrument_id"]
        )

        response = provider.fetch_daily_bars(
            symbol=provider_symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if response.record_count is None:
            raise ValueError(
                "daily provider record_count is required"
            )

        if self.admission_policy is not None:
            self.admission_policy.validate_provider_response(
                response,
                instrument_id=instrument_id,
                canonical_symbol=canonical_symbol,
                provider_symbol=provider_symbol,
                provider=provider.name,
                snapshot_date=snapshot_date,
                expected_market_date=end_date,
                requested_start_date=start_date,
            )

        manifest = self.raw_store.store_bytes(
            provider=provider.name,
            asset_type=DataAssetType.DAILY_BARS,
            payload=response.payload,
            filename=response.filename,
            market_date=snapshot_date,
            symbol=canonical_symbol,
            granularity=BarGranularity.D1,
            record_count=response.record_count,
        )

        metadata = {
            "canonical_symbol": canonical_symbol,
            "provider_symbol": provider_symbol,
            "instrument_id": instrument_id,
            "snapshot_date": snapshot_date.isoformat(),
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "response": dict(response.metadata),
        }

        persisted_manifest = (
            self.repository.save_manifest(
                manifest,
            status=IngestionStatus.RECEIVED,
            source_uri=response.source_uri,
            metadata=metadata,
            )
        )

        return DailyBarIngestionResult(
            provider=provider.name,
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            instrument_id=instrument_id,
            snapshot_date=snapshot_date,
            requested_start_date=start_date,
            requested_end_date=end_date,
            manifest=persisted_manifest,
            record_count=response.record_count,
            response_metadata=dict(response.metadata),
        )
