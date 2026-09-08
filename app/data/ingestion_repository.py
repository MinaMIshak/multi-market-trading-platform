from __future__ import annotations

import json
from datetime import (
    datetime,
    timezone,
)
from typing import Any

from app.data.models import (
    DataQualityIssue,
    IngestionStatus,
    RawArtifactManifest,
)
from app.storage import Database


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


class DataIngestionRepository:
    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def save_manifest(
        self,
        manifest: RawArtifactManifest,
        *,
        status: IngestionStatus = (
            IngestionStatus.RECEIVED
        ),
        source_uri: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_json = json.dumps(
            metadata or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO data_ingestions (
                    ingestion_id,
                    provider,
                    asset_type,
                    granularity,
                    symbol,
                    market_date,
                    raw_path,
                    sha256,
                    byte_size,
                    record_count,
                    status,
                    source_uri,
                    received_at,
                    completed_at,
                    metadata_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )

                ON CONFLICT(raw_path)
                DO UPDATE SET
                    sha256 = excluded.sha256,
                    byte_size = excluded.byte_size,
                    record_count =
                        excluded.record_count,
                    status = excluded.status,
                    source_uri =
                        excluded.source_uri,
                    metadata_json =
                        excluded.metadata_json
                """,
                (
                    str(
                        manifest.ingestion_id
                    ),
                    manifest.provider,
                    manifest.asset_type.value,
                    (
                        manifest
                        .granularity
                        .value
                        if manifest.granularity
                        else None
                    ),
                    manifest.symbol,
                    (
                        manifest
                        .market_date
                        .isoformat()
                        if manifest.market_date
                        else None
                    ),
                    manifest.raw_path,
                    manifest.sha256,
                    manifest.byte_size,
                    manifest.record_count,
                    status.value,
                    source_uri,
                    (
                        manifest
                        .received_at
                        .isoformat()
                    ),
                    (
                        _utc_now()
                        if status
                        in {
                            IngestionStatus
                            .VALIDATED,
                            IngestionStatus
                            .REJECTED,
                        }
                        else None
                    ),
                    metadata_json,
                ),
            )

    def record_issue(
        self,
        issue: DataQualityIssue,
    ) -> None:
        payload_json = json.dumps(
            issue.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO data_quality_issues (
                    issue_id,
                    ingestion_id,
                    severity,
                    code,
                    symbol,
                    market_date,
                    message,
                    payload_json,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    str(issue.issue_id),
                    (
                        str(
                            issue.ingestion_id
                        )
                        if issue.ingestion_id
                        else None
                    ),
                    issue.severity.value,
                    issue.code,
                    issue.symbol,
                    (
                        issue.market_date
                        .isoformat()
                        if issue.market_date
                        else None
                    ),
                    issue.message,
                    payload_json,
                    _utc_now(),
                ),
            )

    def count_ingestions(
        self,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM data_ingestions
                """
            ).fetchone()

        return int(row["count"])

    def count_issues(
        self,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM data_quality_issues
                """
            ).fetchone()

        return int(row["count"])
