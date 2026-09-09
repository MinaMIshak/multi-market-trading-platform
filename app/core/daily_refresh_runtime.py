from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.daily_refresh_execution import (
    DailyRefreshExecutionAdapter,
)
from app.data.daily_canonical_pipeline import (
    DailyCanonicalPipeline,
)
from app.data.daily_canonical_store import (
    DailyCanonicalStore,
)
from app.data.daily_ingestion import (
    DailyBarIngestor,
)
from app.data.daily_refresh_job import (
    DailyRefreshJob,
    DailyRefreshTarget,
)
from app.data.daily_refresh_admission import (
    DailyRefreshAdmissionPolicy,
)
from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.provider import MarketDataProvider
from app.data.providers.eodhd import (
    EODHDProvider,
)
from app.data.raw_store import ImmutableRawStore
from app.storage import Database
from app.storage.daily_canonical_artifact_repository import (
    DailyCanonicalArtifactRepository,
)
from app.storage.scheduler_repository import (
    SchedulerRepository,
)
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


DEFAULT_EODHD_TARGETS = (
    DailyRefreshTarget(
        "COMI",
        "COMI.EGX",
    ),
    DailyRefreshTarget(
        "EAST",
        "EAST.EGX",
    ),
    DailyRefreshTarget(
        "FWRY",
        "FWRY.EGX",
    ),
    DailyRefreshTarget(
        "ORAS",
        "ORAS.EGX",
    ),
    DailyRefreshTarget(
        "SWDY",
        "SWDY.EGX",
    ),
)


@dataclass(frozen=True)
class DailyRefreshRuntime:
    provider: MarketDataProvider
    admission_policy: DailyRefreshAdmissionPolicy
    refresh_job: DailyRefreshJob
    execution_adapter: (
        DailyRefreshExecutionAdapter
    )


def build_daily_refresh_runtime(
    *,
    database: Database,
    scheduler_repository: (
        SchedulerRepository
    ),
    data_root: str | Path,
    api_token: str | None = None,
    provider: (
        MarketDataProvider | None
    ) = None,
    targets: tuple[
        DailyRefreshTarget,
        ...,
    ] = DEFAULT_EODHD_TARGETS,
    lookback_days: int = 400,
    minimum_valid_bars: int = 260,
) -> DailyRefreshRuntime:
    """
    Compose the production daily-refresh
    components without executing a request.

    Tests may inject a provider. Production
    leaves provider=None and supplies the
    runtime EODHD token.
    """

    root = Path(data_root)

    if provider is None:
        token = (
            api_token.strip()
            if api_token
            else ""
        )

        if not token:
            raise ValueError(
                "EODHD runtime token "
                "is required"
            )

        selected_provider = EODHDProvider(
            api_token=token
        )

    else:
        selected_provider = provider

    raw_store = ImmutableRawStore(
        root / "raw"
    )

    admission_policy = (
        DailyRefreshAdmissionPolicy(
            minimum_valid_bars=(
                minimum_valid_bars
            )
        )
    )

    resolver = SecurityMasterRepository(
        database
    )

    ingestor = DailyBarIngestor(
        raw_store=raw_store,
        repository=(
            DataIngestionRepository(
                database
            )
        ),
        resolver=resolver,
        admission_policy=(
            admission_policy
        ),
    )

    refresh_job = DailyRefreshJob(
        ingestor=ingestor,
        pipeline=DailyCanonicalPipeline(
            raw_store=raw_store
        ),
        canonical_store=(
            DailyCanonicalStore(
                root / "canonical"
            )
        ),
        artifact_repository=(
            DailyCanonicalArtifactRepository(
                database
            )
        ),
        targets=targets,
    )

    execution_adapter = (
        DailyRefreshExecutionAdapter(
            scheduler_repository=(
                scheduler_repository
            ),
            refresh_job=refresh_job,
            lookback_days=lookback_days,
        )
    )

    return DailyRefreshRuntime(
        provider=selected_provider,
        admission_policy=(
            admission_policy
        ),
        refresh_job=refresh_job,
        execution_adapter=(
            execution_adapter
        ),
    )
