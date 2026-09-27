from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.daily_refresh_dispatcher import (
    DailyRefreshDispatcher,
)
from app.core.daily_refresh_runtime import (
    build_daily_refresh_runtime,
)
from app.data.quota import DailyQuotaCostContracts

from app.core.runtime_secrets import (
    read_runtime_secret,
)


class SchedulerMode(StrEnum):
    OBSERVE = "observe"
    PAPER_REFRESH = "paper_refresh"


@dataclass(frozen=True)
class SchedulerExecutionContext:
    mode: SchedulerMode
    provider: Any | None = None
    dispatcher: DailyRefreshDispatcher | None = None

    @property
    def execution_enabled(self) -> bool:
        return (
            self.mode
            == SchedulerMode.PAPER_REFRESH
        )


def build_scheduler_execution_context(
    *,
    mode: SchedulerMode | str,
    database: Any,
    scheduler_repository: Any,
    db_path: str | Path,
    secret_path: str | Path,
    provider: Any | None = None,
    targets: Any | None = None,
    quota_cost_contract: DailyQuotaCostContracts | None = None,
    lookback_days: int = 400,
    secret_reader: Any = read_runtime_secret,
    runtime_builder: Any = (
        build_daily_refresh_runtime
    ),
) -> SchedulerExecutionContext:
    selected_mode = (
        mode
        if isinstance(mode, SchedulerMode)
        else SchedulerMode(
            str(mode).strip().lower()
        )
    )

    if selected_mode == SchedulerMode.OBSERVE:
        return SchedulerExecutionContext(
            mode=selected_mode
        )

    # Explicit free-provider composition never reads the legacy token. Cost
    # evidence remains caller supplied, finite and exact for each dated request.
    options = {}
    if provider is not None:
        if targets is None or type(quota_cost_contract) is not DailyQuotaCostContracts:
            raise ValueError("explicit targets and exact batch cost contracts required")
        targets = tuple(targets)
        if not targets:
            raise ValueError("explicit targets required")
        options.update(provider=provider, targets=targets,
                       quota_cost_contract=quota_cost_contract)
    else:
        if targets is not None or quota_cost_contract is not None:
            raise ValueError("explicit provider required for batch acquisition")
        options['api_token'] = secret_reader(secret_path)

    runtime = runtime_builder(
        database=database,
        scheduler_repository=scheduler_repository,
        data_root=Path(db_path).parent,
        lookback_days=lookback_days,
        **options,
    )

    return SchedulerExecutionContext(
        mode=selected_mode,
        provider=runtime.provider,
        dispatcher=DailyRefreshDispatcher(
            scheduler_repository=(
                scheduler_repository
            ),
            execution_adapter=(
                runtime.execution_adapter
            ),
        ),
    )
