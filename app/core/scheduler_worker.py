from __future__ import annotations

import json
import os
import signal
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import (
    MarketSessionOrchestrator,
)
from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.calendar_maintenance_runtime import (
    build_calendar_maintenance_runtime,
)
from app.core.calendar_live_runtime import (
    build_calendar_live_runtime,
)
from app.storage import (
    Database,
    TradingRepository,
)
from app.storage.scheduler_repository import (
    SchedulerRepository,
)
from app.core.scheduler_execution_context import (
    SchedulerMode,
    build_scheduler_execution_context,
)


from app.scheduler_heartbeat import write_heartbeat


stop_event = threading.Event()


def _handle_stop(
    signum,
    frame,
) -> None:
    del signum
    del frame

    stop_event.set()


def main() -> None:
    signal.signal(
        signal.SIGTERM,
        _handle_stop,
    )

    signal.signal(
        signal.SIGINT,
        _handle_stop,
    )

    mode = SchedulerMode(
        os.getenv(
            "EGX_SCHEDULER_MODE",
            SchedulerMode.OBSERVE.value,
        ).strip().lower()
    )

    poll_seconds = int(
        os.getenv(
            "EGX_SCHEDULER_POLL_SECONDS",
            "30",
        )
    )

    poll_seconds = max(
        10,
        poll_seconds,
    )

    calendar_maintenance_mode = os.getenv(
        "EGX_CALENDAR_MAINTENANCE_MODE",
        "disabled",
    ).strip().lower()

    if calendar_maintenance_mode not in {
        "disabled",
        "local",
    }:
        raise ValueError(
            "unsupported calendar maintenance mode"
        )

    calendar_live_mode = os.getenv(
        "EGX_CALENDAR_LIVE_MODE",
        "disabled",
    ).strip().lower()

    if calendar_live_mode not in {
        "disabled",
        "local",
    }:
        raise ValueError(
            "unsupported calendar live mode"
        )


    scan_mode = os.getenv("EGX_SCAN_MODE", "disabled").strip().lower()
    if scan_mode not in {"disabled", "local"}:
        raise ValueError("unsupported EGX scan mode")
    scan_config_path = os.getenv("EGX_SCAN_CONFIG_PATH", "")
    scan_history_path = os.getenv("EGX_SCAN_HISTORY_PATH", "")
    if scan_mode == "local":
        if not all(Path(p).is_absolute() for p in (scan_config_path, scan_history_path)):
            raise ValueError("local scan requires absolute config and history paths")

    secret_path = os.getenv(
        "EODHD_API_TOKEN_FILE",
        "/run/secrets/eodhd_api_token",
    )

    db_path = os.getenv(
        "EGX_DB_PATH",
        "/app/data/platform.db",
    )

    database = Database(
        db_path
    )

    database.initialize()

    repository = SchedulerRepository(
        database
    )

    trading_repository = TradingRepository(
        database
    )

    calendar_truth_resolver = (
        CalendarTruthResolver(
            trading_repository
        )
    )

    calendar_maintenance_runtime = None

    if calendar_maintenance_mode == "local":
        calendar_maintenance_runtime = (
            build_calendar_maintenance_runtime(
                database=database,
                scheduler_repository=repository,
            )
        )

    calendar_live_runtime = None

    if calendar_live_mode == "local":
        calendar_live_runtime = (
            build_calendar_live_runtime(
                database=database,
                scheduler_repository=repository,
            )
        )

    execution_context = (
        build_scheduler_execution_context(
            mode=mode,
            database=database,
            scheduler_repository=repository,
            db_path=db_path,
            secret_path=secret_path,
        )
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    if scan_mode == "local":
        from datetime import time
        from app.core.schedule import CheckpointName, ScheduledCheckpoint
        orchestrator.policy.checkpoints.extend([
            ScheduledCheckpoint(name=CheckpointName.EGX_SCAN_PRIMARY,
                                at=time(18, 30), max_lateness_minutes=15,
                                requires_verified_trading_day=True),
            ScheduledCheckpoint(name=CheckpointName.EGX_SCAN_FALLBACK,
                                at=time(19, 0), max_lateness_minutes=15,
                                requires_verified_trading_day=True,
                                fallback_for=CheckpointName.EGX_SCAN_PRIMARY),
        ])

    timezone = ZoneInfo(
        orchestrator.policy.timezone
    )

    print(
        "SCHEDULER_WORKER_STARTED "
        f"mode={mode.value} "
        f"poll_seconds={poll_seconds} "
        "calendar_truth_source=market_sessions "
        f"calendar_maintenance={calendar_maintenance_mode} "
        f"calendar_live={calendar_live_mode} "
        f"local_scan={scan_mode} "
        "execution_enabled="
        f"{'yes' if execution_context.execution_enabled else 'no'}",
        flush=True,
    )

    last_signature: str | None = None

    while not stop_event.is_set():
        now = datetime.now(timezone)
        market_date = now.date()

        calendar_truth = (
            calendar_truth_resolver.resolve(
                market_date
            )
        )

        recovered = (
            repository
            .recover_stale_running_jobs(
                stale_after_seconds=300
            )
        )

        completed = (
            repository
            .successful_checkpoints(
                market_date
            )
        )

        evaluation = (
            orchestrator.evaluate(
                now=now,
                market_date=market_date,
                calendar_truth=calendar_truth,
                completed_jobs=completed,
            )
        )

        repository.sync_evaluation(
            evaluation
        )

        if calendar_maintenance_runtime is not None:
            calendar_outcomes = (
                calendar_maintenance_runtime
                .dispatcher
                .dispatch(
                    evaluation=evaluation
                )
            )

            for outcome in calendar_outcomes:
                event = {
                    "checkpoint": (
                        outcome.checkpoint_name.value
                    ),
                    "claimed": outcome.claimed,
                    "succeeded": outcome.succeeded,
                    "calendar_truth": (
                        outcome.calendar_truth.value
                        if outcome.calendar_truth is not None
                        else None
                    ),
                    "base_status": (
                        outcome.base_status.value
                        if outcome.base_status is not None
                        else None
                    ),
                    "holiday_status": (
                        outcome.holiday_status.value
                        if outcome.holiday_status is not None
                        else None
                    ),
                    "error_type": outcome.error_type,
                }

                print(
                    "CALENDAR_MAINTENANCE_DISPATCH "
                    + json.dumps(event, sort_keys=True),
                    flush=True,
                )

        if calendar_live_runtime is not None:
            calendar_live_outcomes = (
                calendar_live_runtime
                .dispatcher
                .dispatch(
                    evaluation=evaluation
                )
            )

            for outcome in calendar_live_outcomes:
                event = {
                    "checkpoint": (
                        outcome.checkpoint_name.value
                    ),
                    "claimed": outcome.claimed,
                    "succeeded": outcome.succeeded,
                    "verification_status": (
                        outcome.verification_status.value
                        if outcome.verification_status
                        is not None
                        else None
                    ),
                    "error_type": outcome.error_type,
                }

                print(
                    "CALENDAR_LIVE_DISPATCH "
                    + json.dumps(
                        event,
                        sort_keys=True,
                    ),
                    flush=True,
                )

        if execution_context.execution_enabled:
            if (
                execution_context.dispatcher is None
                or execution_context.provider is None
            ):
                raise RuntimeError(
                    "scheduler execution context "
                    "is incomplete"
                )

            outcomes = (
                execution_context.dispatcher
                .dispatch(
                    evaluation=evaluation,
                    provider=(
                        execution_context.provider
                    ),
                )
            )

            for outcome in outcomes:
                event = {
                    "checkpoint": (
                        outcome
                        .checkpoint_name
                        .value
                    ),
                    "claimed": outcome.claimed,
                    "succeeded": outcome.succeeded,
                    "item_count": outcome.item_count,
                    "error_type": (
                        outcome.error_type
                    ),
                }

                print(
                    "DAILY_REFRESH_DISPATCH "
                    + json.dumps(
                        event,
                        sort_keys=True,
                    ),
                    flush=True,
                )

        if scan_mode == "local":
            from app.egx_scan_dispatch import dispatch_scan
            scan_outcome = dispatch_scan(
                evaluation=evaluation, repository=repository, database=database,
                data_root=Path(db_path).parent,
                config_path=scan_config_path, history_path=scan_history_path)
            if scan_outcome is not None:
                print("EGX_SCAN_DISPATCH " + json.dumps(scan_outcome, sort_keys=True),
                      flush=True)

        summary = (
            repository.status_summary(
                market_date
            )
        )

        snapshot = {
            "market_date": (
                market_date.isoformat()
            ),
            "phase": (
                evaluation
                .session_phase
                .value
            ),
            "calendar_truth": (
                calendar_truth.value
            ),
            "jobs": summary,
            "recovered_stale_jobs": (
                recovered
            ),
        }

        signature = json.dumps(
            snapshot,
            sort_keys=True,
        )

        if signature != last_signature:
            print(
                "SCHEDULER_STATE "
                + signature,
                flush=True,
            )

            last_signature = signature

        write_heartbeat(mode=mode.value, poll_seconds=poll_seconds)

        stop_event.wait(
            poll_seconds
        )

    print(
        "SCHEDULER_WORKER_STOPPED",
        flush=True,
    )


if __name__ == "__main__":
    main()
