from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.data.index_canonical import (
    CanonicalIndexDailyBar,
    canonicalize_index_row,
)
from app.data.index_canonical_store import (
    CanonicalIndexArtifactManifest,
    CanonicalIndexStore,
)
from app.data.index_ingestion import (
    IndexIngestionResult,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
    RawArtifactManifest,
)
from app.storage.canonical_artifact_repository import (
    CanonicalArtifactRepository,
)


class IndexCanonicalPipelineError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class _RawIndexPage:
    page_number: int
    ingestion_id: str
    sha256: str
    rows: tuple[
        dict[str, Any],
        ...,
    ]


@dataclass(frozen=True)
class IndexCanonicalizationResult:
    rows: tuple[
        CanonicalIndexDailyBar,
        ...,
    ]
    source_ingestion_ids: tuple[
        str,
        ...,
    ]
    page_count: int


@dataclass(frozen=True)
class IndexCanonicalPromotionResult:
    ingestion: IndexIngestionResult

    rows: tuple[
        CanonicalIndexDailyBar,
        ...,
    ]

    canonical_manifest: (
        CanonicalIndexArtifactManifest
    )

    artifact_id: str


class IndexCanonicalPipeline:
    """
    Convert immutable raw official-index pages
    into one deterministic canonical artifact.

    No network acquisition occurs here.

    Raw bytes are re-hashed before use and page
    provenance is preserved in every canonical row.
    """

    def __init__(
        self,
        *,
        raw_root: str | Path,
    ) -> None:
        self.raw_root = Path(
            raw_root
        ).resolve()

    def _resolve_raw_path(
        self,
        manifest: RawArtifactManifest,
    ) -> Path:
        candidate = (
            self.raw_root
            / manifest.raw_path
        )

        if candidate.is_symlink():
            raise IndexCanonicalPipelineError(
                "raw artifact cannot be a symlink"
            )

        resolved = candidate.resolve()

        try:
            resolved.relative_to(
                self.raw_root
            )
        except ValueError as exc:
            raise IndexCanonicalPipelineError(
                "raw artifact escapes raw root"
            ) from exc

        if not resolved.is_file():
            raise IndexCanonicalPipelineError(
                "raw artifact does not exist"
            )

        return resolved

    def _load_page(
        self,
        *,
        manifest: RawArtifactManifest,
        ingestion: IndexIngestionResult,
    ) -> _RawIndexPage:
        if (
            manifest.provider
            != ingestion.provider
        ):
            raise IndexCanonicalPipelineError(
                "raw provider mismatch"
            )

        if (
            manifest.asset_type
            != DataAssetType.INDEX_BARS
        ):
            raise IndexCanonicalPipelineError(
                "raw asset type mismatch"
            )

        if (
            manifest.granularity
            != BarGranularity.D1
        ):
            raise IndexCanonicalPipelineError(
                "raw granularity mismatch"
            )

        if (
            manifest.symbol
            != ingestion.index_name
        ):
            raise IndexCanonicalPipelineError(
                "raw index name mismatch"
            )

        if (
            manifest.market_date
            != ingestion.snapshot_date
        ):
            raise IndexCanonicalPipelineError(
                "raw snapshot mismatch"
            )

        path = self._resolve_raw_path(
            manifest
        )

        payload = path.read_bytes()

        if len(payload) != manifest.byte_size:
            raise IndexCanonicalPipelineError(
                "raw byte-size mismatch"
            )

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        if digest != manifest.sha256:
            raise IndexCanonicalPipelineError(
                "raw SHA256 mismatch"
            )

        try:
            document = json.loads(
                payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise IndexCanonicalPipelineError(
                "raw index page is not valid JSON"
            ) from exc

        if not isinstance(
            document,
            dict,
        ):
            raise IndexCanonicalPipelineError(
                "raw index page root "
                "must be an object"
            )

        if (
            document.get("success")
            is not True
        ):
            raise IndexCanonicalPipelineError(
                "raw index page success "
                "flag is not true"
            )

        page_number = document.get(
            "page"
        )

        if (
            isinstance(
                page_number,
                bool,
            )
            or not isinstance(
                page_number,
                int,
            )
            or page_number < 1
        ):
            raise IndexCanonicalPipelineError(
                "raw index page number "
                "is invalid"
            )

        data = document.get("data")

        if not isinstance(
            data,
            list,
        ):
            raise IndexCanonicalPipelineError(
                "raw index data must be a list"
            )

        if (
            manifest.record_count
            is None
            or manifest.record_count
            != len(data)
        ):
            raise IndexCanonicalPipelineError(
                "raw page record-count mismatch"
            )

        normalized: list[
            dict[str, Any]
        ] = []

        for row in data:
            if not isinstance(
                row,
                dict,
            ):
                raise IndexCanonicalPipelineError(
                    "raw index row must "
                    "be an object"
                )

            normalized.append(row)

        return _RawIndexPage(
            page_number=page_number,
            ingestion_id=str(
                manifest.ingestion_id
            ),
            sha256=manifest.sha256,
            rows=tuple(normalized),
        )

    def canonicalize_ingestion(
        self,
        ingestion: IndexIngestionResult,
    ) -> IndexCanonicalizationResult:
        if not ingestion.manifests:
            raise IndexCanonicalPipelineError(
                "index ingestion has no "
                "raw manifests"
            )

        pages = [
            self._load_page(
                manifest=manifest,
                ingestion=ingestion,
            )
            for manifest
            in ingestion.manifests
        ]

        pages.sort(
            key=lambda item: (
                item.page_number
            )
        )

        actual_pages = tuple(
            page.page_number
            for page in pages
        )

        expected_pages = tuple(
            range(
                1,
                len(pages) + 1,
            )
        )

        if actual_pages != expected_pages:
            raise IndexCanonicalPipelineError(
                "raw index pages are not "
                "contiguous from page 1"
            )

        source_ids = tuple(
            page.ingestion_id
            for page in pages
        )

        if len(source_ids) != len(
            set(source_ids)
        ):
            raise IndexCanonicalPipelineError(
                "duplicate source ingestion id"
            )

        rows: list[
            CanonicalIndexDailyBar
        ] = []

        for page in pages:
            for row_number, row in enumerate(
                page.rows,
                start=1,
            ):
                rows.append(
                    canonicalize_index_row(
                        row,
                        index_name=(
                            ingestion
                            .index_name
                        ),
                        source_provider=(
                            ingestion
                            .provider
                        ),
                        source_snapshot_date=(
                            ingestion
                            .snapshot_date
                        ),
                        source_page_number=(
                            page.page_number
                        ),
                        source_row_number=(
                            row_number
                        ),
                        source_sha256=(
                            page.sha256
                        ),
                    )
                )

        if len(rows) != (
            ingestion.record_count
        ):
            raise IndexCanonicalPipelineError(
                "canonical row count does "
                "not match ingestion"
            )

        return IndexCanonicalizationResult(
            rows=tuple(rows),
            source_ingestion_ids=(
                source_ids
            ),
            page_count=len(pages),
        )

    def finalize_ingestion(
        self,
        ingestion: IndexIngestionResult,
        *,
        canonical_store: (
            CanonicalIndexStore
        ),
        repository: (
            CanonicalArtifactRepository
        ),
    ) -> IndexCanonicalPromotionResult:
        canonicalized = (
            self.canonicalize_ingestion(
                ingestion
            )
        )

        manifest = canonical_store.store(
            canonicalized.rows
        )

        if (
            manifest.provider
            != ingestion.provider
        ):
            raise IndexCanonicalPipelineError(
                "canonical provider mismatch"
            )

        if (
            manifest.index_name
            != ingestion.index_name
        ):
            raise IndexCanonicalPipelineError(
                "canonical index mismatch"
            )

        if (
            manifest.source_snapshot_date
            != ingestion.snapshot_date
        ):
            raise IndexCanonicalPipelineError(
                "canonical snapshot mismatch"
            )

        if (
            manifest.record_count
            != ingestion.record_count
        ):
            raise IndexCanonicalPipelineError(
                "canonical record-count mismatch"
            )

        artifact_id = (
            repository
            .promote_validated_artifact(
                manifest=manifest,
                source_ingestion_ids=list(
                    canonicalized
                    .source_ingestion_ids
                ),
                metadata={
                    "pipeline": (
                        "official-index-"
                        "canonical-v1"
                    ),
                    "page_count": (
                        canonicalized
                        .page_count
                    ),
                    "batch": dict(
                        ingestion
                        .batch_metadata
                    ),
                },
            )
        )

        return IndexCanonicalPromotionResult(
            ingestion=ingestion,
            rows=canonicalized.rows,
            canonical_manifest=manifest,
            artifact_id=artifact_id,
        )
