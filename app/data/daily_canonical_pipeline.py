from __future__ import annotations

import json

from dataclasses import dataclass
from uuid import UUID

from app.data.daily_canonical import (
    CanonicalDailyBar,
    canonicalize_daily_row,
)
from app.data.daily_ingestion import (
    DailyBarIngestionResult,
)
from app.data.daily_canonical_store import (
    CanonicalDailyArtifactManifest,
    DailyCanonicalStore,
)
from app.storage.daily_canonical_artifact_repository import (
    DailyCanonicalArtifactRepository,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
)
from app.data.raw_store import (
    ImmutableRawStore,
)


@dataclass(frozen=True)
class DailyCanonicalPipelineResult:
    ingestion: DailyBarIngestionResult
    rows: tuple[CanonicalDailyBar, ...]


@dataclass(frozen=True)
class DailyCanonicalPromotionResult:
    ingestion: DailyBarIngestionResult
    rows: tuple[CanonicalDailyBar, ...]
    canonical_manifest: CanonicalDailyArtifactManifest
    artifact_id: str


class DailyCanonicalPipeline:
    def __init__(
        self,
        *,
        raw_store: ImmutableRawStore,
    ) -> None:
        self.raw_store = raw_store

    def canonicalize_ingestion(
        self,
        ingestion: DailyBarIngestionResult,
    ) -> DailyCanonicalPipelineResult:
        manifest = ingestion.manifest

        if manifest.provider != ingestion.provider:
            raise ValueError(
                "raw provider does not match ingestion"
            )

        if (
            manifest.asset_type
            != DataAssetType.DAILY_BARS
        ):
            raise ValueError(
                "raw artifact is not DAILY_BARS"
            )

        if (
            manifest.granularity
            != BarGranularity.D1
        ):
            raise ValueError(
                "raw artifact is not D1"
            )

        if (
            manifest.symbol
            != ingestion.canonical_symbol
        ):
            raise ValueError(
                "raw symbol does not match ingestion"
            )

        if (
            manifest.market_date
            != ingestion.snapshot_date
        ):
            raise ValueError(
                "raw snapshot does not match ingestion"
            )

        if manifest.record_count != (
            ingestion.record_count
        ):
            raise ValueError(
                "raw record_count does not "
                "match ingestion"
            )

        payload = self.raw_store.read_verified(
            manifest
        )

        try:
            document = json.loads(
                payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "invalid stored daily JSON"
            ) from exc

        if not isinstance(document, list):
            raise ValueError(
                "stored daily payload "
                "must be a list"
            )

        if len(document) != ingestion.record_count:
            raise ValueError(
                "stored daily row count mismatch"
            )

        instrument_id = UUID(
            ingestion.instrument_id
        )

        rows = tuple(
            canonicalize_daily_row(
                row,
                instrument_id=instrument_id,
                canonical_symbol=(
                    ingestion.canonical_symbol
                ),
                provider_symbol=(
                    ingestion.provider_symbol
                ),
                source_provider=(
                    ingestion.provider
                ),
                source_snapshot_date=(
                    ingestion.snapshot_date
                ),
                source_row_number=index,
                source_sha256=manifest.sha256,
            )
            for index, row in enumerate(
                document,
                start=1,
            )
        )

        if not rows:
            raise ValueError(
                "daily canonical input "
                "cannot be empty"
            )

        return DailyCanonicalPipelineResult(
            ingestion=ingestion,
            rows=rows,
        )

    def finalize_ingestion(
        self,
        ingestion: DailyBarIngestionResult,
        *,
        canonical_store: DailyCanonicalStore,
        repository: DailyCanonicalArtifactRepository,
    ) -> DailyCanonicalPromotionResult:
        canonicalized = (
            self.canonicalize_ingestion(
                ingestion
            )
        )

        manifest = canonical_store.store(
            canonicalized.rows
        )

        if (
            str(manifest.instrument_id)
            != ingestion.instrument_id
        ):
            raise ValueError(
                "canonical instrument_id mismatch"
            )

        if (
            manifest.canonical_symbol
            != ingestion.canonical_symbol
        ):
            raise ValueError(
                "canonical symbol mismatch"
            )

        if (
            manifest.provider_symbol
            != ingestion.provider_symbol
        ):
            raise ValueError(
                "canonical provider_symbol mismatch"
            )

        if (
            manifest.provider
            != ingestion.provider
        ):
            raise ValueError(
                "canonical provider mismatch"
            )

        if (
            manifest.source_snapshot_date
            != ingestion.snapshot_date
        ):
            raise ValueError(
                "canonical snapshot mismatch"
            )

        artifact_id = (
            repository.promote_validated_artifact(
                manifest=manifest,
                source_ingestion_ids=[
                    str(
                        ingestion
                        .manifest
                        .ingestion_id
                    )
                ],
                metadata={
                    "pipeline": (
                        "daily-canonical-v1"
                    ),
                    "raw_sha256": (
                        ingestion
                        .manifest
                        .sha256
                    ),
                },
            )
        )

        return DailyCanonicalPromotionResult(
            ingestion=ingestion,
            rows=canonicalized.rows,
            canonical_manifest=manifest,
            artifact_id=artifact_id,
        )
