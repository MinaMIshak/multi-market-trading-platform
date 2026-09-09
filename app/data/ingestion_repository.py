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
    ) -> RawArtifactManifest:
        metadata_json = json.dumps(
            metadata or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        expected = {
            "provider": manifest.provider,
            "asset_type": manifest.asset_type.value,
            "granularity": (
                manifest.granularity.value
                if manifest.granularity
                else None
            ),
            "symbol": manifest.symbol,
            "market_date": (
                manifest.market_date.isoformat()
                if manifest.market_date
                else None
            ),
            "sha256": manifest.sha256,
            "byte_size": manifest.byte_size,
            "record_count": manifest.record_count,
            "source_uri": source_uri,
            "metadata_json": metadata_json,
        }

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT *
                FROM data_ingestions
                WHERE raw_path = ?
                """,
                (manifest.raw_path,),
            ).fetchone()

            if existing is not None:
                for field, wanted in expected.items():
                    if existing[field] != wanted:
                        raise ValueError(
                            "immutable ingestion conflict "
                            f"on {field}"
                        )

                old_status = IngestionStatus(
                    existing["status"]
                )

                if (
                    old_status
                    in {
                        IngestionStatus.VALIDATED,
                        IngestionStatus.REJECTED,
                    }
                    and status
                    == IngestionStatus.RECEIVED
                ):
                    pass

                elif (
                    old_status
                    == IngestionStatus.RECEIVED
                    and status
                    in {
                        IngestionStatus.VALIDATED,
                        IngestionStatus.REJECTED,
                    }
                ):
                    connection.execute(
                        """
                        UPDATE data_ingestions
                        SET status = ?,
                            completed_at = ?
                        WHERE ingestion_id = ?
                        """,
                        (
                            status.value,
                            _utc_now(),
                            existing["ingestion_id"],
                        ),
                    )

                elif old_status != status:
                    raise ValueError(
                        "invalid ingestion status transition"
                    )

                persisted_id = existing["ingestion_id"]

            else:
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
                    """,
                    (
                        str(manifest.ingestion_id),
                        expected["provider"],
                        expected["asset_type"],
                        expected["granularity"],
                        expected["symbol"],
                        expected["market_date"],
                        manifest.raw_path,
                        expected["sha256"],
                        expected["byte_size"],
                        expected["record_count"],
                        status.value,
                        source_uri,
                        manifest.received_at.isoformat(),
                        (
                            _utc_now()
                            if status
                            in {
                                IngestionStatus.VALIDATED,
                                IngestionStatus.REJECTED,
                            }
                            else None
                        ),
                        metadata_json,
                    ),
                )

                persisted_id = str(
                    manifest.ingestion_id
                )

        persisted = self.get_manifest_by_raw_path(
            manifest.raw_path
        )

        if persisted is None:
            raise RuntimeError(
                "persisted ingestion disappeared"
            )

        if str(persisted.ingestion_id) != persisted_id:
            raise RuntimeError(
                "persisted ingestion identity mismatch"
            )

        return persisted

    def get_manifest_by_raw_path(
        self,
        raw_path: str,
    ) -> RawArtifactManifest | None:
        with self.database.connect() as connection:
            row = connection.execute(
                '''
                SELECT
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
                    received_at
                FROM data_ingestions
                WHERE raw_path = ?
                ''',
                (raw_path,),
            ).fetchone()

        if row is None:
            return None

        return RawArtifactManifest(
            ingestion_id=row["ingestion_id"],
            provider=row["provider"],
            asset_type=row["asset_type"],
            granularity=row["granularity"],
            symbol=row["symbol"],
            market_date=row["market_date"],
            raw_path=row["raw_path"],
            sha256=row["sha256"],
            byte_size=row["byte_size"],
            record_count=row["record_count"],
            received_at=row["received_at"],
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
