from __future__ import annotations

import json

from datetime import (
    datetime,
    timezone,
)
from typing import Any
from uuid import uuid4

from app.data.index_canonical_store import (
    CanonicalIndexArtifactManifest,
)
from app.storage.database import (
    Database,
)


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


class CanonicalArtifactRepository:
    """
    Atomic canonical validation ledger.

    The canonical artifact record,
    its raw-source provenance links,
    and promotion of every source
    ingestion from RECEIVED to VALIDATED
    happen in one SQLite transaction.

    Partial validation is not allowed.
    """

    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    @staticmethod
    def _artifact_values(
        manifest: CanonicalIndexArtifactManifest,
    ) -> dict[str, Any]:
        return {
            "provider": (
                manifest.provider
            ),
            "asset_type": (
                manifest.asset_type.value
            ),
            "granularity": (
                manifest.granularity.value
            ),
            "symbol": (
                manifest.index_name
            ),
            "source_snapshot_date": (
                manifest
                .source_snapshot_date
                .isoformat()
            ),
            "canonical_path": (
                manifest.relative_path
            ),
            "sha256": (
                manifest.sha256
            ),
            "byte_size": (
                manifest.byte_size
            ),
            "record_count": (
                manifest.record_count
            ),
            "oldest_market_date": (
                manifest
                .oldest_market_date
                .isoformat()
            ),
            "newest_market_date": (
                manifest
                .newest_market_date
                .isoformat()
            ),
            "semantic_contract_version": (
                manifest
                .semantic_contract_version
            ),
            "serialization_format": (
                manifest
                .serialization_format
            ),
            "full_ohlc_valid_count": (
                manifest
                .full_ohlc_valid_count
            ),
            "legacy_close_reference_count": (
                manifest
                .legacy_close_reference_count
            ),
            "quarantined_anomaly_count": (
                manifest
                .quarantined_anomaly_count
            ),
            "full_ohlc_usable_count": (
                manifest
                .full_ohlc_usable_count
            ),
            "close_history_usable_count": (
                manifest
                .close_history_usable_count
            ),
        }

    @staticmethod
    def _validate_manifest_counts(
        values: dict[str, Any],
    ) -> None:
        semantic_total = (
            int(
                values[
                    "full_ohlc_valid_count"
                ]
            )
            + int(
                values[
                    "legacy_close_reference_count"
                ]
            )
            + int(
                values[
                    "quarantined_anomaly_count"
                ]
            )
        )

        if semantic_total != int(
            values["record_count"]
        ):
            raise ValueError(
                "semantic class total "
                "does not match record_count"
            )

        if int(
            values[
                "full_ohlc_usable_count"
            ]
        ) > int(
            values["record_count"]
        ):
            raise ValueError(
                "full OHLC usable count "
                "exceeds record_count"
            )

        if int(
            values[
                "close_history_usable_count"
            ]
        ) > int(
            values["record_count"]
        ):
            raise ValueError(
                "close-history usable count "
                "exceeds record_count"
            )

    @staticmethod
    def _compare_existing(
        existing: Any,
        expected: dict[str, Any],
    ) -> None:
        fields = (
            "provider",
            "asset_type",
            "granularity",
            "symbol",
            "source_snapshot_date",
            "canonical_path",
            "sha256",
            "byte_size",
            "record_count",
            "oldest_market_date",
            "newest_market_date",
            "semantic_contract_version",
            "serialization_format",
            "full_ohlc_valid_count",
            "legacy_close_reference_count",
            "quarantined_anomaly_count",
            "full_ohlc_usable_count",
            "close_history_usable_count",
        )

        for field in fields:
            if (
                existing[field]
                != expected[field]
            ):
                raise ValueError(
                    "existing canonical "
                    "artifact conflicts "
                    f"on {field}"
                )

    def promote_validated_artifact(
        self,
        *,
        manifest: (
            CanonicalIndexArtifactManifest
        ),
        source_ingestion_ids: list[str],
        metadata: (
            dict[str, Any] | None
        ) = None,
    ) -> str:
        if not source_ingestion_ids:
            raise ValueError(
                "source_ingestion_ids "
                "cannot be empty"
            )

        if len(
            source_ingestion_ids
        ) != len(
            set(source_ingestion_ids)
        ):
            raise ValueError(
                "source_ingestion_ids "
                "must be unique"
            )

        expected = (
            self._artifact_values(
                manifest
            )
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

            placeholders = ",".join(
                "?"
                for _ in source_ingestion_ids
            )

            source_rows = (
                connection.execute(
                    f"""
                    SELECT
                        ingestion_id,
                        provider,
                        asset_type,
                        granularity,
                        symbol,
                        market_date,
                        status,
                        record_count
                    FROM data_ingestions
                    WHERE ingestion_id
                    IN ({placeholders})
                    """,
                    tuple(
                        source_ingestion_ids
                    ),
                )
                .fetchall()
            )

            if len(source_rows) != len(
                source_ingestion_ids
            ):
                raise ValueError(
                    "one or more source "
                    "ingestions do not exist"
                )

            source_by_id = {
                row["ingestion_id"]: row
                for row in source_rows
            }

            ordered_sources = [
                source_by_id[
                    ingestion_id
                ]
                for ingestion_id
                in source_ingestion_ids
            ]

            existing_by_path = (
                connection.execute(
                    """
                    SELECT *
                    FROM canonical_data_artifacts
                    WHERE canonical_path = ?
                    """,
                    (
                        expected[
                            "canonical_path"
                        ],
                    ),
                )
                .fetchone()
            )

            existing_by_contract = (
                connection.execute(
                    """
                    SELECT *
                    FROM canonical_data_artifacts
                    WHERE provider = ?
                      AND asset_type = ?
                      AND granularity = ?
                      AND symbol = ?
                      AND source_snapshot_date = ?
                      AND semantic_contract_version = ?
                      AND serialization_format = ?
                    """,
                    (
                        expected["provider"],
                        expected["asset_type"],
                        expected["granularity"],
                        expected["symbol"],
                        expected[
                            "source_snapshot_date"
                        ],
                        expected[
                            "semantic_contract_version"
                        ],
                        expected[
                            "serialization_format"
                        ],
                    ),
                )
                .fetchone()
            )

            if (
                existing_by_path is not None
                and existing_by_contract
                is not None
                and existing_by_path[
                    "artifact_id"
                ]
                != existing_by_contract[
                    "artifact_id"
                ]
            ):
                raise ValueError(
                    "canonical artifact "
                    "identity conflict"
                )

            existing = (
                existing_by_path
                or existing_by_contract
            )

            if existing is not None:
                self._compare_existing(
                    existing,
                    expected,
                )

                if (
                    existing["status"]
                    != "VALIDATED"
                ):
                    raise ValueError(
                        "existing canonical "
                        "artifact is not "
                        "VALIDATED"
                    )

                links = (
                    connection.execute(
                        """
                        SELECT
                            ingestion_id,
                            source_ordinal
                        FROM canonical_artifact_sources
                        WHERE artifact_id = ?
                        ORDER BY source_ordinal
                        """,
                        (
                            existing[
                                "artifact_id"
                            ],
                        ),
                    )
                    .fetchall()
                )

                linked_ids = [
                    row["ingestion_id"]
                    for row in links
                ]

                if linked_ids != (
                    source_ingestion_ids
                ):
                    raise ValueError(
                        "existing canonical "
                        "artifact source links "
                        "do not match"
                    )

                if any(
                    source["status"]
                    != "VALIDATED"
                    for source
                    in ordered_sources
                ):
                    raise ValueError(
                        "existing artifact "
                        "sources are not all "
                        "VALIDATED"
                    )

                return str(
                    existing[
                        "artifact_id"
                    ]
                )

            for source in ordered_sources:
                if (
                    source["provider"]
                    != expected["provider"]
                ):
                    raise ValueError(
                        "source provider "
                        "does not match artifact"
                    )

                if (
                    source["asset_type"]
                    != expected["asset_type"]
                ):
                    raise ValueError(
                        "source asset_type "
                        "does not match artifact"
                    )

                if (
                    source["granularity"]
                    != expected["granularity"]
                ):
                    raise ValueError(
                        "source granularity "
                        "does not match artifact"
                    )

                if (
                    source["symbol"]
                    != expected["symbol"]
                ):
                    raise ValueError(
                        "source symbol "
                        "does not match artifact"
                    )

                if (
                    source["market_date"]
                    != expected[
                        "source_snapshot_date"
                    ]
                ):
                    raise ValueError(
                        "source snapshot date "
                        "does not match artifact"
                    )

                if (
                    source["status"]
                    != "RECEIVED"
                ):
                    raise ValueError(
                        "all source ingestions "
                        "must be RECEIVED"
                    )

            source_record_count = sum(
                int(
                    source[
                        "record_count"
                    ]
                    or 0
                )
                for source
                in ordered_sources
            )

            if source_record_count != int(
                expected[
                    "record_count"
                ]
            ):
                raise ValueError(
                    "source record total "
                    "does not match "
                    "canonical record_count"
                )

            artifact_id = str(
                uuid4()
            )

            connection.execute(
                """
                INSERT INTO canonical_data_artifacts (
                    artifact_id,
                    provider,
                    asset_type,
                    granularity,
                    symbol,
                    source_snapshot_date,
                    canonical_path,
                    sha256,
                    byte_size,
                    record_count,
                    oldest_market_date,
                    newest_market_date,
                    semantic_contract_version,
                    serialization_format,
                    full_ohlc_valid_count,
                    legacy_close_reference_count,
                    quarantined_anomaly_count,
                    full_ohlc_usable_count,
                    close_history_usable_count,
                    status,
                    created_at,
                    validated_at,
                    metadata_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    artifact_id,
                    expected["provider"],
                    expected["asset_type"],
                    expected["granularity"],
                    expected["symbol"],
                    expected[
                        "source_snapshot_date"
                    ],
                    expected[
                        "canonical_path"
                    ],
                    expected["sha256"],
                    expected["byte_size"],
                    expected["record_count"],
                    expected[
                        "oldest_market_date"
                    ],
                    expected[
                        "newest_market_date"
                    ],
                    expected[
                        "semantic_contract_version"
                    ],
                    expected[
                        "serialization_format"
                    ],
                    expected[
                        "full_ohlc_valid_count"
                    ],
                    expected[
                        "legacy_close_reference_count"
                    ],
                    expected[
                        "quarantined_anomaly_count"
                    ],
                    expected[
                        "full_ohlc_usable_count"
                    ],
                    expected[
                        "close_history_usable_count"
                    ],
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
                    INSERT INTO canonical_artifact_sources (
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

            cursor = connection.execute(
                f"""
                UPDATE data_ingestions
                SET
                    status = 'VALIDATED',
                    completed_at = ?
                WHERE ingestion_id
                IN ({placeholders})
                  AND status = 'RECEIVED'
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
                    "atomic validation "
                    "promotion updated "
                    "unexpected source count"
                )

            return artifact_id
