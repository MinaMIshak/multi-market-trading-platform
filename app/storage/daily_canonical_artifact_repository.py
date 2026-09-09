from __future__ import annotations

import json

from datetime import (
    datetime,
    timezone,
)
from typing import Any

from app.data.daily_canonical_store import (
    CanonicalDailyArtifactManifest,
)
from app.storage.database import Database


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


class DailyCanonicalArtifactRepository:
    """
    Atomic daily canonical validation ledger.

    Artifact registration, raw-source links,
    and source promotion to VALIDATED must
    succeed or fail in one SQLite transaction.
    """

    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    @staticmethod
    def _artifact_values(
        manifest: CanonicalDailyArtifactManifest,
    ) -> dict[str, Any]:
        return {
            "instrument_id": str(
                manifest.instrument_id
            ),
            "canonical_symbol": (
                manifest.canonical_symbol
            ),
            "provider": manifest.provider,
            "provider_symbol": (
                manifest.provider_symbol
            ),
            "asset_type": (
                manifest.asset_type.value
            ),
            "granularity": (
                manifest.granularity.value
            ),
            "source_snapshot_date": (
                manifest.source_snapshot_date
                .isoformat()
            ),
            "canonical_path": (
                manifest.relative_path
            ),
            "sha256": manifest.sha256,
            "byte_size": manifest.byte_size,
            "record_count": (
                manifest.record_count
            ),
            "oldest_market_date": (
                manifest.oldest_market_date
                .isoformat()
            ),
            "newest_market_date": (
                manifest.newest_market_date
                .isoformat()
            ),
            "valid_bar_count": (
                manifest.valid_bar_count
            ),
            "quarantined_bar_count": (
                manifest.quarantined_bar_count
            ),
            "semantic_contract_version": (
                manifest.semantic_contract_version
            ),
            "serialization_format": (
                manifest.serialization_format
            ),
        }

    @staticmethod
    def _validate_manifest_counts(
        values: dict[str, Any],
    ) -> None:
        valid = int(
            values["valid_bar_count"]
        )
        quarantined = int(
            values["quarantined_bar_count"]
        )
        total = int(
            values["record_count"]
        )

        if valid < 0 or quarantined < 0:
            raise ValueError(
                "daily semantic counts "
                "cannot be negative"
            )

        if valid + quarantined != total:
            raise ValueError(
                "daily semantic counts "
                "do not equal record_count"
            )

        if total <= 0:
            raise ValueError(
                "daily canonical artifact "
                "cannot be empty"
            )

    @staticmethod
    def _compare_existing(
        existing,
        expected: dict[str, Any],
    ) -> None:
        fields = (
            "instrument_id",
            "canonical_symbol",
            "provider",
            "provider_symbol",
            "source_snapshot_date",
            "canonical_path",
            "sha256",
            "byte_size",
            "record_count",
            "oldest_market_date",
            "newest_market_date",
            "valid_bar_count",
            "quarantined_bar_count",
            "semantic_contract_version",
            "serialization_format",
        )

        for field in fields:
            if existing[field] != expected[field]:
                raise ValueError(
                    "existing daily canonical "
                    "artifact conflicts "
                    f"on {field}"
                )

    @staticmethod
    def _load_and_validate_sources(
        connection,
        source_ingestion_ids: list[str],
        expected: dict[str, Any],
    ):
        placeholders = ",".join(
            "?" for _ in source_ingestion_ids
        )

        rows = connection.execute(
            f"""
            SELECT
                ingestion_id,
                provider,
                asset_type,
                granularity,
                symbol,
                market_date,
                status,
                record_count,
                metadata_json
            FROM data_ingestions
            WHERE ingestion_id
            IN ({placeholders})
            """,
            tuple(source_ingestion_ids),
        ).fetchall()

        if len(rows) != len(
            source_ingestion_ids
        ):
            raise ValueError(
                "one or more source "
                "ingestions do not exist"
            )

        by_id = {
            row["ingestion_id"]: row
            for row in rows
        }

        ordered = [
            by_id[ingestion_id]
            for ingestion_id
            in source_ingestion_ids
        ]

        for source in ordered:
            checks = (
                (
                    "provider",
                    source["provider"],
                    expected["provider"],
                ),
                (
                    "asset_type",
                    source["asset_type"],
                    expected["asset_type"],
                ),
                (
                    "granularity",
                    source["granularity"],
                    expected["granularity"],
                ),
                (
                    "symbol",
                    source["symbol"],
                    expected["canonical_symbol"],
                ),
                (
                    "snapshot date",
                    source["market_date"],
                    expected["source_snapshot_date"],
                ),
            )

            for name, actual, wanted in checks:
                if actual != wanted:
                    raise ValueError(
                        f"source {name} "
                        "does not match artifact"
                    )

            if source["status"] != "RECEIVED":
                raise ValueError(
                    "all source ingestions "
                    "must be RECEIVED"
                )

            try:
                metadata = json.loads(
                    source["metadata_json"]
                )
            except (
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                raise ValueError(
                    "invalid source metadata_json"
                ) from exc

            if not isinstance(metadata, dict):
                raise ValueError(
                    "source metadata must be object"
                )

            if (
                metadata.get("provider_symbol")
                != expected["provider_symbol"]
            ):
                raise ValueError(
                    "source provider_symbol "
                    "does not match artifact"
                )

            if (
                str(metadata.get("instrument_id"))
                != expected["instrument_id"]
            ):
                raise ValueError(
                    "source instrument_id "
                    "does not match artifact"
                )

        total = sum(
            int(row["record_count"] or 0)
            for row in ordered
        )

        if total != int(
            expected["record_count"]
        ):
            raise ValueError(
                "source record total "
                "does not match canonical "
                "record_count"
            )

        return ordered

    @staticmethod
    def _find_existing_artifact(
        connection,
        expected: dict[str, Any],
    ):
        by_path = connection.execute(
            """
            SELECT *
            FROM daily_canonical_artifacts
            WHERE canonical_path = ?
            """,
            (
                expected["canonical_path"],
            ),
        ).fetchone()

        by_contract = connection.execute(
            """
            SELECT *
            FROM daily_canonical_artifacts
            WHERE provider = ?
              AND instrument_id = ?
              AND provider_symbol = ?
              AND source_snapshot_date = ?
              AND semantic_contract_version = ?
              AND serialization_format = ?
            """,
            (
                expected["provider"],
                expected["instrument_id"],
                expected["provider_symbol"],
                expected["source_snapshot_date"],
                expected[
                    "semantic_contract_version"
                ],
                expected[
                    "serialization_format"
                ],
            ),
        ).fetchone()

        if (
            by_path is not None
            and by_contract is not None
            and by_path["artifact_id"]
            != by_contract["artifact_id"]
        ):
            raise ValueError(
                "daily canonical artifact "
                "identity conflict"
            )

        return by_path or by_contract

    def _validate_existing_replay(
        self,
        connection,
        existing,
        expected: dict[str, Any],
        source_ingestion_ids: list[str],
    ) -> str:
        self._compare_existing(
            existing,
            expected,
        )

        if existing["status"] != "VALIDATED":
            raise ValueError(
                "existing daily canonical "
                "artifact is not VALIDATED"
            )

        links = connection.execute(
            """
            SELECT
                ingestion_id,
                source_ordinal
            FROM daily_canonical_sources
            WHERE artifact_id = ?
            ORDER BY source_ordinal
            """,
            (
                existing["artifact_id"],
            ),
        ).fetchall()

        linked_ids = [
            row["ingestion_id"]
            for row in links
        ]

        if linked_ids != source_ingestion_ids:
            raise ValueError(
                "existing daily canonical "
                "artifact source links "
                "do not match"
            )

        placeholders = ",".join(
            "?" for _ in source_ingestion_ids
        )

        source_rows = connection.execute(
            f"""
            SELECT
                ingestion_id,
                status
            FROM data_ingestions
            WHERE ingestion_id
            IN ({placeholders})
            """,
            tuple(source_ingestion_ids),
        ).fetchall()

        if len(source_rows) != len(
            source_ingestion_ids
        ):
            raise ValueError(
                "existing daily artifact "
                "source ingestion missing"
            )

        status_by_id = {
            row["ingestion_id"]: row["status"]
            for row in source_rows
        }

        if any(
            status_by_id[ingestion_id]
            != "VALIDATED"
            for ingestion_id
            in source_ingestion_ids
        ):
            raise ValueError(
                "existing daily artifact "
                "sources are not all VALIDATED"
            )

        return str(
            existing["artifact_id"]
        )

    def promote_validated_artifact(
        self,
        *,
        manifest: CanonicalDailyArtifactManifest,
        source_ingestion_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if not source_ingestion_ids:
            raise ValueError(
                "source_ingestion_ids cannot be empty"
            )

        if len(source_ingestion_ids) != len(
            set(source_ingestion_ids)
        ):
            raise ValueError(
                "source_ingestion_ids must be unique"
            )

        expected = self._artifact_values(
            manifest
        )
        self._validate_manifest_counts(
            expected
        )

        metadata_json = json.dumps(
            metadata or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        now = _utc_now()

        with self.database.connect() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            existing = (
                self._find_existing_artifact(
                    connection,
                    expected,
                )
            )

            if existing is not None:
                return (
                    self._validate_existing_replay(
                        connection,
                        existing,
                        expected,
                        source_ingestion_ids,
                    )
                )

            self._load_and_validate_sources(
                connection,
                source_ingestion_ids,
                expected,
            )

            artifact_id = str(
                __import__("uuid").uuid4()
            )

            connection.execute(
                """
                INSERT INTO daily_canonical_artifacts (
                    artifact_id,
                    instrument_id,
                    canonical_symbol,
                    provider,
                    provider_symbol,
                    source_snapshot_date,
                    canonical_path,
                    sha256,
                    byte_size,
                    record_count,
                    oldest_market_date,
                    newest_market_date,
                    valid_bar_count,
                    quarantined_bar_count,
                    semantic_contract_version,
                    serialization_format,
                    status,
                    created_at,
                    validated_at,
                    metadata_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    artifact_id,
                    expected["instrument_id"],
                    expected["canonical_symbol"],
                    expected["provider"],
                    expected["provider_symbol"],
                    expected["source_snapshot_date"],
                    expected["canonical_path"],
                    expected["sha256"],
                    expected["byte_size"],
                    expected["record_count"],
                    expected["oldest_market_date"],
                    expected["newest_market_date"],
                    expected["valid_bar_count"],
                    expected["quarantined_bar_count"],
                    expected["semantic_contract_version"],
                    expected["serialization_format"],
                    "VALIDATED",
                    now,
                    now,
                    metadata_json,
                ),
            )

            for ordinal, ingestion_id in enumerate(
                source_ingestion_ids,
                start=1,
            ):
                connection.execute(
                    """
                    INSERT INTO daily_canonical_sources (
                        artifact_id,
                        ingestion_id,
                        source_ordinal
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        artifact_id,
                        ingestion_id,
                        ordinal,
                    ),
                )

            placeholders = ",".join(
                "?" for _ in source_ingestion_ids
            )

            cursor = connection.execute(
                f"""
                UPDATE data_ingestions
                SET
                    status='VALIDATED',
                    completed_at=?
                WHERE ingestion_id
                IN ({placeholders})
                  AND status='RECEIVED'
                """,
                (
                    now,
                    *source_ingestion_ids,
                ),
            )

            if cursor.rowcount != len(
                source_ingestion_ids
            ):
                raise RuntimeError(
                    "atomic daily validation "
                    "updated unexpected source count"
                )

            return artifact_id
