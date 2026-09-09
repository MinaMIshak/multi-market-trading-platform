from __future__ import annotations

import hashlib
import json
import os
import re

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from app.data.index_canonical import (
    CanonicalIndexDailyBar,
    IndexBarSemanticClass,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
)


SAFE_TOKEN = re.compile(
    r"^[A-Za-z0-9._-]+$"
)


SEMANTIC_CONTRACT_VERSION = (
    "egx-index-semantic-v1"
)

SERIALIZATION_FORMAT = (
    "canonical-json-v1"
)


def _safe_token(
    value: str,
    *,
    field_name: str,
) -> str:
    value = value.strip()

    if not value:
        raise ValueError(
            f"{field_name} cannot be empty"
        )

    if not SAFE_TOKEN.fullmatch(
        value
    ):
        raise ValueError(
            f"unsafe {field_name}: "
            f"{value!r}"
        )

    if value in {
        ".",
        "..",
    }:
        raise ValueError(
            f"unsafe {field_name}: "
            f"{value!r}"
        )

    return value


def _sha256_bytes(
    payload: bytes,
) -> str:
    return hashlib.sha256(
        payload
    ).hexdigest()


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(
                chunk
            )

    return digest.hexdigest()


@dataclass(frozen=True)
class CanonicalIndexArtifactManifest:
    provider: str
    asset_type: DataAssetType
    granularity: BarGranularity

    index_name: str
    source_snapshot_date: date

    oldest_market_date: date
    newest_market_date: date

    relative_path: str

    sha256: str
    byte_size: int
    record_count: int

    full_ohlc_valid_count: int
    legacy_close_reference_count: int
    quarantined_anomaly_count: int

    full_ohlc_usable_count: int
    close_history_usable_count: int

    semantic_contract_version: str
    serialization_format: str


class CanonicalIndexStore:
    """
    Immutable deterministic store for
    canonical index-history artifacts.

    Serialization deliberately matches
    the canonical dry-run contract:

    - sort rows by market_date;
    - model_dump(mode="json");
    - JSON array;
    - ensure_ascii=False;
    - sort_keys=True;
    - separators=(",", ":").

    Existing artifacts are never
    overwritten.
    """

    def __init__(
        self,
        root: str | Path,
    ) -> None:
        self.root = Path(root)

    @staticmethod
    def serialize_rows(
        rows: Iterable[
            CanonicalIndexDailyBar
        ],
    ) -> bytes:
        materialized = list(rows)

        if not materialized:
            raise ValueError(
                "canonical index rows "
                "cannot be empty"
            )

        ordered = sorted(
            materialized,
            key=lambda item: (
                item.market_date
            ),
        )

        dates = [
            row.market_date
            for row in ordered
        ]

        if len(dates) != len(
            set(dates)
        ):
            raise ValueError(
                "canonical index rows "
                "contain duplicate "
                "market_date values"
            )

        serialized = [
            row.model_dump(
                mode="json"
            )
            for row in ordered
        ]

        return json.dumps(
            serialized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _validate_uniform_source(
        rows: list[
            CanonicalIndexDailyBar
        ],
    ) -> tuple[
        str,
        str,
        date,
    ]:
        if not rows:
            raise ValueError(
                "canonical index rows "
                "cannot be empty"
            )

        index_names = {
            row.index_name
            for row in rows
        }

        providers = {
            row.source_provider
            for row in rows
        }

        snapshot_dates = {
            row.source_snapshot_date
            for row in rows
        }

        if len(index_names) != 1:
            raise ValueError(
                "canonical artifact "
                "cannot mix index names"
            )

        if len(providers) != 1:
            raise ValueError(
                "canonical artifact "
                "cannot mix providers"
            )

        if len(snapshot_dates) != 1:
            raise ValueError(
                "canonical artifact "
                "cannot mix source "
                "snapshot dates"
            )

        return (
            next(iter(index_names)),
            next(iter(providers)),
            next(iter(snapshot_dates)),
        )

    def store(
        self,
        rows: Iterable[
            CanonicalIndexDailyBar
        ],
    ) -> CanonicalIndexArtifactManifest:
        materialized = list(rows)

        (
            index_name,
            provider,
            snapshot_date,
        ) = self._validate_uniform_source(
            materialized
        )

        payload = self.serialize_rows(
            materialized
        )

        ordered = sorted(
            materialized,
            key=lambda item: (
                item.market_date
            ),
        )

        oldest_date = (
            ordered[0].market_date
        )

        newest_date = (
            ordered[-1].market_date
        )

        counts = Counter(
            row.semantic_class
            for row in ordered
        )

        full_ohlc_usable = sum(
            row.usable_for_full_ohlc
            for row in ordered
        )

        close_history_usable = sum(
            row.usable_for_close_history
            for row in ordered
        )

        provider_token = _safe_token(
            provider.lower(),
            field_name="provider",
        )

        index_token = _safe_token(
            index_name.upper(),
            field_name="index_name",
        )

        filename = (
            f"{index_token}-"
            f"{oldest_date.isoformat()}-"
            f"{newest_date.isoformat()}-"
            "D1-canonical-v1.json"
        )

        relative = Path(
            provider_token,
            DataAssetType
            .INDEX_BARS
            .value
            .lower(),
            f"{snapshot_date.year:04d}",
            f"{snapshot_date.month:02d}",
            f"{snapshot_date.day:02d}",
            index_token,
            filename,
        )

        target = (
            self.root
            / relative
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        expected_hash = (
            _sha256_bytes(
                payload
            )
        )

        if target.exists():
            existing_hash = (
                _sha256_file(
                    target
                )
            )

            if (
                existing_hash
                != expected_hash
            ):
                raise FileExistsError(
                    "immutable canonical "
                    "artifact conflict: "
                    f"{relative}"
                )

            return self._manifest(
                rows=ordered,
                relative=relative,
                sha256=expected_hash,
                byte_size=len(payload),
            )

        temp = target.parent / (
            "."
            + target.name
            + "."
            + uuid4().hex
            + ".tmp"
        )

        try:
            with temp.open(
                "xb"
            ) as handle:
                handle.write(
                    payload
                )

                handle.flush()

                os.fsync(
                    handle.fileno()
                )

            try:
                os.link(
                    temp,
                    target,
                )

            except FileExistsError:
                existing_hash = (
                    _sha256_file(
                        target
                    )
                )

                if (
                    existing_hash
                    != expected_hash
                ):
                    raise FileExistsError(
                        "immutable canonical "
                        "artifact conflict "
                        "after race: "
                        f"{relative}"
                    )

        finally:
            temp.unlink(
                missing_ok=True
            )

        actual_hash = (
            _sha256_file(
                target
            )
        )

        if actual_hash != expected_hash:
            raise RuntimeError(
                "canonical artifact "
                "post-write hash mismatch"
            )

        return self._manifest(
            rows=ordered,
            relative=relative,
            sha256=expected_hash,
            byte_size=len(payload),
        )

    @staticmethod
    def _manifest(
        *,
        rows: list[
            CanonicalIndexDailyBar
        ],
        relative: Path,
        sha256: str,
        byte_size: int,
    ) -> CanonicalIndexArtifactManifest:
        counts = Counter(
            row.semantic_class
            for row in rows
        )

        return (
            CanonicalIndexArtifactManifest(
                provider=(
                    rows[0]
                    .source_provider
                ),
                asset_type=(
                    DataAssetType
                    .INDEX_BARS
                ),
                granularity=(
                    BarGranularity.D1
                ),
                index_name=(
                    rows[0]
                    .index_name
                ),
                source_snapshot_date=(
                    rows[0]
                    .source_snapshot_date
                ),
                oldest_market_date=(
                    rows[0]
                    .market_date
                ),
                newest_market_date=(
                    rows[-1]
                    .market_date
                ),
                relative_path=str(
                    relative
                ),
                sha256=sha256,
                byte_size=byte_size,
                record_count=len(rows),
                full_ohlc_valid_count=(
                    counts[
                        IndexBarSemanticClass
                        .FULL_OHLC_VALID
                    ]
                ),
                legacy_close_reference_count=(
                    counts[
                        IndexBarSemanticClass
                        .LEGACY_CLOSE_REFERENCE
                    ]
                ),
                quarantined_anomaly_count=(
                    counts[
                        IndexBarSemanticClass
                        .QUARANTINED_ANOMALY
                    ]
                ),
                full_ohlc_usable_count=sum(
                    row.usable_for_full_ohlc
                    for row in rows
                ),
                close_history_usable_count=sum(
                    row.usable_for_close_history
                    for row in rows
                ),
                semantic_contract_version=(
                    SEMANTIC_CONTRACT_VERSION
                ),
                serialization_format=(
                    SERIALIZATION_FORMAT
                ),
            )
        )
