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
from app.core.runtime_secrets import (
    read_runtime_secret,
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

    mode = os.getenv(
        "EGX_SCHEDULER_MODE",
        "observe",
    ).strip().lower()

    if mode != "observe":
        raise RuntimeError(
            "Only observe scheduler mode "
            "is allowed at this stage"
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

    eodhd_api_token = read_runtime_secret(
        secret_path
    )

    database = Database(
        os.getenv(
            "EGX_DB_PATH",
            "/app/data/platform.db",
        )
    )

    database.initialize()

    repository = SchedulerRepository(
        database
    )

    orchestrator = (
        MarketSessionOrchestrator()
    )

    timezone = ZoneInfo(
        orchestrator.policy.timezone
    )

    print(
        "SCHEDULER_WORKER_STARTED "
        f"mode={mode} "
        f"poll_seconds={poll_seconds} "
        f"calendar_truth={calendar_truth.value} "
        "eodhd_secret=available",
        flush=True,
    )

    del eodhd_api_token

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
