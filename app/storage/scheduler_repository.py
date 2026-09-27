from __future__ import annotations

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

from app.core import (
    CheckpointName,
    ScheduleEvaluation,
)
from app.core.job_state import (
    SchedulerJobStatus,
)
from app.storage.database import Database


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(
    value: datetime | None = None,
) -> str:
    return (
        value or _utc_now()
    ).isoformat()


class SchedulerRepository:
    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def _upsert_window(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
        status: SchedulerJobStatus,
        scheduled_at: datetime,
        expires_at: datetime,
        calendar_truth: str,
    ) -> None:
        now = _utc_iso()

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_jobs (
                    market_date,
                    checkpoint_name,
                    status,
                    scheduled_at,
                    expires_at,
                    calendar_truth,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(
                    market_date,
                    checkpoint_name
                )
                DO UPDATE SET
                    status =
                        CASE
                            WHEN scheduled_jobs.status
                                 IN (
                                     'RUNNING',
                                     'SUCCEEDED',
                                     'FAILED'
                                 )
                            THEN scheduled_jobs.status
                            ELSE excluded.status
                        END,

                    scheduled_at =
                        excluded.scheduled_at,

                    expires_at =
                        excluded.expires_at,

                    calendar_truth =
                        excluded.calendar_truth,

                    last_seen_at =
                        excluded.last_seen_at
                """,
                (
                    market_date.isoformat(),
                    checkpoint_name.value,
                    status.value,
                    scheduled_at.isoformat(),
                    expires_at.isoformat(),
                    calendar_truth,
                    now,
                    now,
                ),
            )

    def sync_evaluation(
        self,
        evaluation: ScheduleEvaluation,
    ) -> None:
        mappings = (
            (
                evaluation.due,
                SchedulerJobStatus.PENDING,
            ),
            (
                evaluation.blocked,
                SchedulerJobStatus.BLOCKED,
            ),
            (
                evaluation.missed,
                SchedulerJobStatus.MISSED,
            ),
            (
                evaluation.skipped,
                SchedulerJobStatus.SKIPPED,
            ),
        )

        for windows, status in mappings:
            for window in windows:
                self._upsert_window(
                    market_date=(
                        evaluation.market_date
                    ),
                    checkpoint_name=window.name,
                    status=status,
                    scheduled_at=(
                        window.scheduled_at
                    ),
                    expires_at=(
                        window.expires_at
                    ),
                    calendar_truth=(
                        evaluation
                        .calendar_truth
                        .value
                    ),
                )

    def successful_checkpoints(
        self,
        market_date: date,
    ) -> set[CheckpointName]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT checkpoint_name
                FROM scheduled_jobs
                WHERE market_date = ?
                  AND status = 'SUCCEEDED'
                """,
                (
                    market_date.isoformat(),
                ),
            ).fetchall()

        return {
            CheckpointName(
                row["checkpoint_name"]
            )
            for row in rows
        }

    def claim_job(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
        stale_after_seconds: int = 300,
    ) -> bool:
        now = _utc_now()

        stale_before = (
            now
            - timedelta(
                seconds=stale_after_seconds
            )
        )

        connection = self.database.connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            cursor = connection.execute(
                """
                UPDATE scheduled_jobs

                SET
                    status = 'RUNNING',
                    attempt_count =
                        attempt_count + 1,
                    started_at = ?,
                    finished_at = NULL,
                    last_error = NULL,
                    last_seen_at = ?

                WHERE market_date = ?
                  AND checkpoint_name = ?
                  AND (
                        status IN (
                            'PENDING',
                            'FAILED'
                        )
                        OR (
                            status = 'RUNNING'
                            AND started_at IS NOT NULL
                            AND started_at <= ?
                        )
                  )
                """,
                (
                    now.isoformat(),
                    now.isoformat(),
                    market_date.isoformat(),
                    checkpoint_name.value,
                    stale_before.isoformat(),
                ),
            )

            connection.commit()

            return cursor.rowcount == 1

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def mark_succeeded(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
        expected_attempt: tuple[int, str] | None = None,
    ) -> bool:
        now = _utc_iso()

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scheduled_jobs

                SET
                    status = 'SUCCEEDED',
                    finished_at = ?,
                    last_seen_at = ?,
                    last_error = NULL

                WHERE market_date = ?
                  AND checkpoint_name = ?
                  AND status = 'RUNNING'
                  AND (? IS NULL OR (attempt_count = ? AND started_at = ?))
                """,
                (
                    now,
                    now,
                    market_date.isoformat(),
                    checkpoint_name.value,
                    expected_attempt[0] if expected_attempt else None,
                    expected_attempt[0] if expected_attempt else None,
                    expected_attempt[1] if expected_attempt else None,
                ),
            )

        return cursor.rowcount == 1

    def mark_failed(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
        error: str,
        expected_attempt: tuple[int, str] | None = None,
    ) -> bool:
        now = _utc_iso()

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scheduled_jobs

                SET
                    status = 'FAILED',
                    finished_at = ?,
                    last_seen_at = ?,
                    last_error = ?

                WHERE market_date = ?
                  AND checkpoint_name = ?
                  AND status = 'RUNNING'
                  AND (? IS NULL OR (attempt_count = ? AND started_at = ?))
                """,
                (
                    now,
                    now,
                    error[:2000],
                    market_date.isoformat(),
                    checkpoint_name.value,
                    expected_attempt[0] if expected_attempt else None,
                    expected_attempt[0] if expected_attempt else None,
                    expected_attempt[1] if expected_attempt else None,
                ),
            )

        return cursor.rowcount == 1

    def recover_stale_running_jobs(
        self,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        stale_before = (
            _utc_now()
            - timedelta(
                seconds=stale_after_seconds
            )
        ).isoformat()

        now = _utc_iso()

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scheduled_jobs

                SET
                    status = 'FAILED',
                    finished_at = ?,
                    last_seen_at = ?,
                    last_error =
                        'STALE_RUNNING_JOB_RECOVERED'

                WHERE status = 'RUNNING'
                  AND started_at IS NOT NULL
                  AND started_at <= ?
                """,
                (
                    now,
                    now,
                    stale_before,
                ),
            )

        return cursor.rowcount

    def get_job(
        self,
        *,
        market_date: date,
        checkpoint_name: CheckpointName,
    ) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM scheduled_jobs
                WHERE market_date = ?
                  AND checkpoint_name = ?
                """,
                (
                    market_date.isoformat(),
                    checkpoint_name.value,
                ),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def count_jobs(
        self,
        market_date: date,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM scheduled_jobs
                WHERE market_date = ?
                """,
                (
                    market_date.isoformat(),
                ),
            ).fetchone()

        return int(row["count"])

    def status_summary(
        self,
        market_date: date,
    ) -> dict[str, int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS count

                FROM scheduled_jobs

                WHERE market_date = ?

                GROUP BY status

                ORDER BY status
                """,
                (
                    market_date.isoformat(),
                ),
            ).fetchall()

        return {
            row["status"]: int(
                row["count"]
            )
            for row in rows
        }
