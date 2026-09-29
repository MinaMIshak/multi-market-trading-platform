from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.raw_store import (
    ImmutableRawStore,
)
from app.data.security_master_ingestion import (
    SecurityMasterIngestor,
)
from app.data.security_master_refresh_job import (
    SecurityMasterRefreshJob,
)
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


@dataclass(frozen=True)
class SecurityMasterRefreshRuntime:
    job: SecurityMasterRefreshJob


def build_security_master_refresh_runtime(
    *,
    database,
    root: str | Path,
) -> SecurityMasterRefreshRuntime:
    """
    Compose the security-master refresh
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

    ingestor = SecurityMasterIngestor(
        raw_store=raw_store,
        repository=(
            DataIngestionRepository(
                database
            )
        ),
    )

    security_master_repository = (
        SecurityMasterRepository(
            database
        )
    )

    job = SecurityMasterRefreshJob(
        ingestor=ingestor,
        raw_store=raw_store,
        security_master_repository=(
            security_master_repository
        ),
    )

    return SecurityMasterRefreshRuntime(
        job=job
    )
