from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.data.index_canonical_pipeline import (
    IndexCanonicalPipeline,
)
from app.data.index_canonical_store import (
    CanonicalIndexStore,
)
from app.data.index_ingestion import (
    IndexHistoryIngestor,
)
from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.official_index_refresh_job import (
    OfficialIndexRefreshJob,
)
from app.data.raw_store import (
    ImmutableRawStore,
)
from app.storage.canonical_artifact_repository import (
    CanonicalArtifactRepository,
)


@dataclass(frozen=True)
class OfficialIndexRefreshRuntime:
    job: OfficialIndexRefreshJob


def build_official_index_refresh_runtime(
    *,
    database,
    root: str | Path,
) -> OfficialIndexRefreshRuntime:
    """
    Compose the official-index refresh
    data pipeline.

    No provider is constructed here.
    Therefore building this runtime performs
    no network acquisition.
    """

    root = Path(root)

    raw_root = root / "raw"

    raw_store = ImmutableRawStore(
        raw_root
    )

    ingestor = IndexHistoryIngestor(
        raw_store=raw_store,
        repository=(
            DataIngestionRepository(
                database
            )
        ),
    )

    pipeline = IndexCanonicalPipeline(
        raw_root=raw_root
    )

    canonical_store = (
        CanonicalIndexStore
        .from_data_root(
            root
        )
    )

    artifact_repository = (
        CanonicalArtifactRepository(
            database
        )
    )

    job = OfficialIndexRefreshJob(
        ingestor=ingestor,
        pipeline=pipeline,
        canonical_store=canonical_store,
        artifact_repository=(
            artifact_repository
        ),
    )

    return OfficialIndexRefreshRuntime(
        job=job
    )
