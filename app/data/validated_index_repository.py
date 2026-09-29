from __future__ import annotations

import hashlib
import json

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.data.index_canonical import (
    CanonicalIndexDailyBar,
    IndexBarSemanticClass,
)
from app.data.index_canonical_store import (
    SEMANTIC_CONTRACT_VERSION,
    SERIALIZATION_FORMAT,
)
from app.data.models import (
    DataAssetType,
)
from app.storage.database import (
    Database,
)


class ValidatedCanonicalIndexError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class ValidatedCanonicalIndexDataset:
    artifact_id: str
    provider: str
    index_name: str
    source_snapshot_date: date

    canonical_path: str
    sha256: str

    semantic_contract_version: str
    serialization_format: str

    rows: tuple[
        CanonicalIndexDailyBar,
        ...,
    ]

    @property
    def full_ohlc_rows(
        self,
    ) -> tuple[
        CanonicalIndexDailyBar,
        ...,
    ]:
        return tuple(
            row
            for row in self.rows
            if row.usable_for_full_ohlc
        )

    @property
    def close_history_rows(
        self,
    ) -> tuple[
        CanonicalIndexDailyBar,
        ...,
    ]:
        return tuple(
            row
            for row in self.rows
            if row.usable_for_close_history
        )


class ValidatedCanonicalIndexRepository:
    """
    Approved read path for canonical
    daily index history.

    Consumers receive data only when:

    - catalog artifact is VALIDATED;
    - semantic/serialization contracts match;
    - canonical file exists inside the
      configured canonical root;
    - file size and SHA256 match catalog;
    - JSON parses into typed canonical rows;
    - row counts/date bounds/semantic counts
      match catalog;
    - every provenance link exists and its
      raw ingestion is VALIDATED;
    - row-level source page/hash/ordinal
      provenance matches the linked source
      manifests.

    No data repair or fallback is performed.
    """

    def __init__(
        self,
        *,
        database: Database,
        canonical_root: str | Path,
    ) -> None:
        self.database = database

        self.canonical_root = Path(
            canonical_root
        ).resolve()

    @staticmethod
    def _sha256(
        payload: bytes,
    ) -> str:
        return hashlib.sha256(
            payload
        ).hexdigest()

    def _resolve_artifact_path(
        self,
        relative_path: str,
    ) -> Path:
        candidate = (
            self.canonical_root
            / relative_path
        ).resolve()

        if not candidate.is_relative_to(
            self.canonical_root
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact path "
                    "escapes canonical root"
                )
            )

        return candidate

    @classmethod
    def from_data_root(
        cls,
        *,
        database: Database,
        data_root: str | Path,
    ) -> "ValidatedCanonicalIndexRepository":
        """
        Construct the validated reader from the
        platform data root.

        Canonical path conventions stay inside
        this approved read boundary.
        """
        return cls(
            database=database,
            canonical_root=(
                Path(data_root) / "canonical"
            ),
        )

    def covering_snapshot_dates(
        self,
        *,
        index_name: str,
        market_date: date,
        provider: str = (
            "egx_official_public"
        ),
    ) -> tuple[date, ...]:
        """
        Snapshot dates, ascending, of VALIDATED
        catalog artifacts taken strictly after
        market_date whose dated range covers it.

        Catalog candidates only: callers must
        still load() each one, which performs
        the full integrity checks.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT source_snapshot_date
                FROM canonical_data_artifacts
                WHERE provider = ?
                  AND asset_type = ?
                  AND granularity = 'D1'
                  AND symbol = ?
                  AND status = 'VALIDATED'
                  AND semantic_contract_version = ?
                  AND serialization_format = ?
                  AND source_snapshot_date > ?
                  AND oldest_market_date <= ?
                  AND newest_market_date >= ?
                ORDER BY source_snapshot_date
                """,
                (
                    provider.strip().lower(),
                    DataAssetType
                    .INDEX_BARS
                    .value,
                    index_name.strip().upper(),
                    SEMANTIC_CONTRACT_VERSION,
                    SERIALIZATION_FORMAT,
                    market_date.isoformat(),
                    market_date.isoformat(),
                    market_date.isoformat(),
                ),
            ).fetchall()

        return tuple(
            date.fromisoformat(
                row["source_snapshot_date"]
            )
            for row in rows
        )

    def load(
        self,
        *,
        index_name: str,
        source_snapshot_date: date,
        provider: str = (
            "egx_official_public"
        ),
    ) -> ValidatedCanonicalIndexDataset:
        normalized_index = (
            index_name.strip().upper()
        )

        normalized_provider = (
            provider.strip().lower()
        )

        if not normalized_index:
            raise ValueError(
                "index_name cannot be empty"
            )

        if not normalized_provider:
            raise ValueError(
                "provider cannot be empty"
            )

        with self.database.connect() as connection:
            artifacts = connection.execute(
                """
                SELECT *
                FROM canonical_data_artifacts
                WHERE provider = ?
                  AND asset_type = ?
                  AND granularity = 'D1'
                  AND symbol = ?
                  AND source_snapshot_date = ?
                  AND status = 'VALIDATED'
                  AND semantic_contract_version = ?
                  AND serialization_format = ?
                """,
                (
                    normalized_provider,
                    DataAssetType
                    .INDEX_BARS
                    .value,
                    normalized_index,
                    source_snapshot_date
                    .isoformat(),
                    SEMANTIC_CONTRACT_VERSION,
                    SERIALIZATION_FORMAT,
                ),
            ).fetchall()

            if len(artifacts) != 1:
                raise (
                    ValidatedCanonicalIndexError(
                        "expected exactly one "
                        "validated canonical "
                        "index artifact; found "
                        f"{len(artifacts)}"
                    )
                )

            artifact = artifacts[0]

            sources = connection.execute(
                """
                SELECT
                    s.source_ordinal,
                    i.ingestion_id,
                    i.provider,
                    i.asset_type,
                    i.granularity,
                    i.symbol,
                    i.market_date,
                    i.raw_path,
                    i.sha256,
                    i.record_count,
                    i.status
                FROM canonical_artifact_sources s
                JOIN data_ingestions i
                  ON i.ingestion_id =
                     s.ingestion_id
                WHERE s.artifact_id = ?
                ORDER BY s.source_ordinal
                """,
                (
                    artifact[
                        "artifact_id"
                    ],
                ),
            ).fetchall()

        if not sources:
            raise (
                ValidatedCanonicalIndexError(
                    "validated artifact has "
                    "no provenance sources"
                )
            )

        ordinals = [
            int(
                source[
                    "source_ordinal"
                ]
            )
            for source in sources
        ]

        if ordinals != list(
            range(
                1,
                len(sources) + 1,
            )
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "source ordinals are not "
                    "contiguous from 1..N"
                )
            )

        for source in sources:
            if source["status"] != (
                "VALIDATED"
            ):
                raise (
                    ValidatedCanonicalIndexError(
                        "canonical provenance "
                        "contains non-validated "
                        "source ingestion"
                    )
                )

            if (
                source["provider"]
                != normalized_provider
                or source["asset_type"]
                != DataAssetType
                .INDEX_BARS
                .value
                or source["granularity"]
                != "D1"
                or source["symbol"]
                != normalized_index
                or source["market_date"]
                != source_snapshot_date
                .isoformat()
            ):
                raise (
                    ValidatedCanonicalIndexError(
                        "canonical provenance "
                        "source metadata mismatch"
                    )
                )

        artifact_path = (
            self._resolve_artifact_path(
                artifact[
                    "canonical_path"
                ]
            )
        )

        if not artifact_path.is_file():
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact file "
                    "does not exist"
                )
            )

        payload = artifact_path.read_bytes()

        if len(payload) != int(
            artifact["byte_size"]
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact byte "
                    "size does not match catalog"
                )
            )

        digest = self._sha256(
            payload
        )

        if digest != artifact["sha256"]:
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact SHA256 "
                    "does not match catalog"
                )
            )

        try:
            document = json.loads(
                payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact is not "
                    "valid UTF-8 JSON"
                )
            ) from exc

        if not isinstance(
            document,
            list,
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact root "
                    "must be a JSON array"
                )
            )

        try:
            rows = tuple(
                CanonicalIndexDailyBar
                .model_validate(row)
                for row in document
            )
        except Exception as exc:
            raise (
                ValidatedCanonicalIndexError(
                    "canonical artifact failed "
                    "typed row validation"
                )
            ) from exc

        if len(rows) != int(
            artifact["record_count"]
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical record count "
                    "does not match catalog"
                )
            )

        market_dates = [
            row.market_date
            for row in rows
        ]

        if market_dates != sorted(
            market_dates
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical rows are not "
                    "sorted by market_date"
                )
            )

        if len(
            set(market_dates)
        ) != len(
            market_dates
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical rows contain "
                    "duplicate market dates"
                )
            )

        if (
            market_dates[0].isoformat()
            != artifact[
                "oldest_market_date"
            ]
            or market_dates[-1].isoformat()
            != artifact[
                "newest_market_date"
            ]
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical date bounds "
                    "do not match catalog"
                )
            )

        if any(
            row.index_name
            != normalized_index
            or row.source_provider
            != normalized_provider
            or row.source_snapshot_date
            != source_snapshot_date
            for row in rows
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical row source "
                    "identity mismatch"
                )
            )

        counts = Counter(
            row.semantic_class
            for row in rows
        )

        expected_counts = {
            IndexBarSemanticClass
            .FULL_OHLC_VALID: int(
                artifact[
                    "full_ohlc_valid_count"
                ]
            ),
            IndexBarSemanticClass
            .LEGACY_CLOSE_REFERENCE: int(
                artifact[
                    "legacy_close_reference_count"
                ]
            ),
            IndexBarSemanticClass
            .QUARANTINED_ANOMALY: int(
                artifact[
                    "quarantined_anomaly_count"
                ]
            ),
        }

        if any(
            counts[semantic_class]
            != expected_count
            for (
                semantic_class,
                expected_count,
            ) in expected_counts.items()
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical semantic counts "
                    "do not match catalog"
                )
            )

        full_usable = sum(
            row.usable_for_full_ohlc
            for row in rows
        )

        close_usable = sum(
            row.usable_for_close_history
            for row in rows
        )

        if full_usable != int(
            artifact[
                "full_ohlc_usable_count"
            ]
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "full-OHLC usable count "
                    "does not match catalog"
                )
            )

        if close_usable != int(
            artifact[
                "close_history_usable_count"
            ]
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "close-history usable count "
                    "does not match catalog"
                )
            )

        rows_by_page: dict[
            int,
            list[
                CanonicalIndexDailyBar
            ],
        ] = {}

        for row in rows:
            rows_by_page.setdefault(
                row.source_page_number,
                [],
            ).append(
                row
            )

        if set(
            rows_by_page
        ) != set(
            ordinals
        ):
            raise (
                ValidatedCanonicalIndexError(
                    "canonical source pages "
                    "do not match provenance "
                    "source ordinals"
                )
            )

        for source in sources:
            ordinal = int(
                source[
                    "source_ordinal"
                ]
            )

            page_rows = rows_by_page[
                ordinal
            ]

            expected_record_count = (
                int(
                    source[
                        "record_count"
                    ]
                )
            )

            if len(
                page_rows
            ) != expected_record_count:
                raise (
                    ValidatedCanonicalIndexError(
                        "canonical source page "
                        "record count mismatch"
                    )
                )

            if any(
                row.source_sha256
                != source["sha256"]
                for row in page_rows
            ):
                raise (
                    ValidatedCanonicalIndexError(
                        "canonical row source "
                        "SHA256 mismatch"
                    )
                )

            row_ordinals = {
                row.source_row_number
                for row in page_rows
            }

            if row_ordinals != set(
                range(
                    1,
                    expected_record_count
                    + 1,
                )
            ):
                raise (
                    ValidatedCanonicalIndexError(
                        "canonical source row "
                        "ordinals are incomplete"
                    )
                )

        return (
            ValidatedCanonicalIndexDataset(
                artifact_id=str(
                    artifact[
                        "artifact_id"
                    ]
                ),
                provider=(
                    normalized_provider
                ),
                index_name=(
                    normalized_index
                ),
                source_snapshot_date=(
                    source_snapshot_date
                ),
                canonical_path=(
                    artifact[
                        "canonical_path"
                    ]
                ),
                sha256=digest,
                semantic_contract_version=(
                    artifact[
                        "semantic_contract_version"
                    ]
                ),
                serialization_format=(
                    artifact[
                        "serialization_format"
                    ]
                ),
                rows=rows,
            )
        )
