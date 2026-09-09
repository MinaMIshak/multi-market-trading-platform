from __future__ import annotations

import hashlib
import os
import re

from collections import Counter

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from app.data.daily_canonical import (
    DAILY_SEMANTIC_CONTRACT_VERSION,
    DAILY_SERIALIZATION_FORMAT,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
)


SAFE_TOKEN = re.compile(
    r"^[A-Za-z0-9._-]+$"
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

    if (
        not SAFE_TOKEN.fullmatch(value)
        or value in {".", ".."}
    ):
        raise ValueError(
            f"unsafe {field_name}: {value!r}"
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

            digest.update(chunk)

    return digest.hexdigest()


@dataclass(frozen=True)
class CanonicalDailyArtifactManifest:
    instrument_id: UUID

    provider: str
    provider_symbol: str
    canonical_symbol: str

    asset_type: DataAssetType
    granularity: BarGranularity

    source_snapshot_date: date

    oldest_market_date: date
    newest_market_date: date

    relative_path: str

    sha256: str
    byte_size: int
    record_count: int

    valid_bar_count: int
    quarantined_bar_count: int

    semantic_contract_version: str = (
        DAILY_SEMANTIC_CONTRACT_VERSION
    )

    serialization_format: str = (
        DAILY_SERIALIZATION_FORMAT
    )


from app.data.daily_canonical import (
    CanonicalDailyBar,
    DailyBarSemanticClass,
    serialize_daily_rows,
)


class DailyCanonicalStore:
    def __init__(
        self,
        root: str | Path,
    ) -> None:
        self.root = Path(root)

    @staticmethod
    def _validate_uniform_source(
        rows: list[CanonicalDailyBar],
    ) -> tuple[
        UUID,
        str,
        str,
        str,
        date,
    ]:
        if not rows:
            raise ValueError(
                "canonical daily rows cannot be empty"
            )

        instrument_ids = {
            row.instrument_id for row in rows
        }
        symbols = {
            row.canonical_symbol for row in rows
        }
        provider_symbols = {
            row.provider_symbol for row in rows
        }
        providers = {
            row.source_provider for row in rows
        }
        snapshots = {
            row.source_snapshot_date for row in rows
        }
        checks = (
            ("instrument_id", instrument_ids),
            ("canonical_symbol", symbols),
            ("provider_symbol", provider_symbols),
            ("source_provider", providers),
            ("source_snapshot_date", snapshots),
        )

        for name, values in checks:
            if len(values) != 1:
                raise ValueError(
                    "canonical daily artifact cannot "
                    f"mix {name} values"
                )

        return (
            next(iter(instrument_ids)),
            next(iter(symbols)),
            next(iter(provider_symbols)),
            next(iter(providers)),
            next(iter(snapshots)),
        )

    def store(
        self,
        rows,
    ) -> CanonicalDailyArtifactManifest:
        materialized = list(rows)

        (
            instrument_id,
            canonical_symbol,
            provider_symbol,
            provider,
            snapshot_date,
        ) = self._validate_uniform_source(
            materialized
        )

        payload = serialize_daily_rows(
            materialized
        )

        ordered = sorted(
            materialized,
            key=lambda row: row.market_date,
        )

        oldest = ordered[0].market_date
        newest = ordered[-1].market_date

        provider_token = _safe_token(
            provider.lower(),
            field_name="provider",
        )
        symbol_token = _safe_token(
            canonical_symbol.upper(),
            field_name="canonical_symbol",
        )

        filename = (
            f"{symbol_token}-"
            f"{oldest.isoformat()}-"
            f"{newest.isoformat()}-"
            "D1-canonical-v1.json"
        )

        relative = Path(
            provider_token,
            DataAssetType.DAILY_BARS.value.lower(),
            f"{snapshot_date.year:04d}",
            f"{snapshot_date.month:02d}",
            f"{snapshot_date.day:02d}",
            symbol_token,
            filename,
        )

        target = self.root / relative

        current = self.root
        current.mkdir(
            parents=True,
            exist_ok=True,
        )
        current.chmod(0o2770)

        for part in relative.parent.parts:
            current = current / part
            current.mkdir(
                exist_ok=True,
            )
            current.chmod(0o2770)

        expected_hash = _sha256_bytes(
            payload
        )

        if target.exists():
            if _sha256_file(target) != expected_hash:
                raise FileExistsError(
                    "immutable daily canonical "
                    f"artifact conflict: {relative}"
                )
            target.chmod(0o660)
        else:
            temp = target.parent / (
                "."
                + target.name
                + "."
                + uuid4().hex
                + ".tmp"
            )

            try:
                with temp.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())

                temp.chmod(0o660)

                try:
                    os.link(temp, target)
                except FileExistsError:
                    if (
                        _sha256_file(target)
                        != expected_hash
                    ):
                        raise FileExistsError(
                            "immutable daily canonical "
                            "artifact conflict after race"
                        )
            finally:
                temp.unlink(
                    missing_ok=True
                )

            target.chmod(0o660)

        if _sha256_file(target) != expected_hash:
            raise RuntimeError(
                "daily canonical artifact "
                "post-write hash mismatch"
            )

        counts = Counter(
            row.semantic_class
            for row in ordered
        )

        return CanonicalDailyArtifactManifest(
            instrument_id=instrument_id,
            provider=provider,
            provider_symbol=provider_symbol,
            canonical_symbol=canonical_symbol,
            asset_type=DataAssetType.DAILY_BARS,
            granularity=BarGranularity.D1,
            source_snapshot_date=snapshot_date,
            oldest_market_date=oldest,
            newest_market_date=newest,
            relative_path=str(relative),
            sha256=expected_hash,
            byte_size=len(payload),
            record_count=len(ordered),
            valid_bar_count=counts[
                DailyBarSemanticClass.VALID_EXECUTABLE
            ],
            quarantined_bar_count=counts[
                DailyBarSemanticClass.QUARANTINED_ANOMALY
            ],
        )
