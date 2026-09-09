from __future__ import annotations

import json
import os
import signal
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import (
    CalendarTruth,
    MarketSessionOrchestrator,
)
from app.storage import Database
from app.storage.scheduler_repository import (
    SchedulerRepository,
)
from app.core.scheduler_execution_context import (
    SchedulerMode,
    build_scheduler_execution_context,
)


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

    calendar_truth = CalendarTruth(
        os.getenv(
            "EGX_CALENDAR_TRUTH",
            CalendarTruth.UNVERIFIED.value,
        )
    )

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

    timezone = ZoneInfo(
        orchestrator.policy.timezone
    )

    print(
        "SCHEDULER_WORKER_STARTED "
        f"mode={mode.value} "
        f"poll_seconds={poll_seconds} "
        f"calendar_truth={calendar_truth.value} "
        "execution_enabled="
        f"{'yes' if execution_context.execution_enabled else 'no'}",
        flush=True,
    )

    last_signature: str | None = None

    while not stop_event.is_set():
        now = datetime.now(timezone)
        market_date = now.date()

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

        stop_event.wait(
            poll_seconds
        )

    print(
        "SCHEDULER_WORKER_STOPPED",
        flush=True,
    )


if __name__ == "__main__":
    main()
