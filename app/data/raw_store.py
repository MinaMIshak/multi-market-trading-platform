from __future__ import annotations

import hashlib
import os
import re
from datetime import (
    date,
    datetime,
    timezone,
)
from pathlib import Path
from uuid import uuid4

from app.data.models import (
    BarGranularity,
    DataAssetType,
    RawArtifactManifest,
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

    if not SAFE_TOKEN.fullmatch(value):
        raise ValueError(
            f"unsafe {field_name}: {value!r}"
        )

    if value in {".", ".."}:
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


class ImmutableRawStore:
    def __init__(
        self,
        root: str | Path,
    ) -> None:
        self.root = Path(root)

    def store_bytes(
        self,
        *,
        provider: str,
        asset_type: DataAssetType,
        payload: bytes,
        filename: str,
        market_date: date | None = None,
        symbol: str | None = None,
        granularity: BarGranularity | None = None,
        record_count: int | None = None,
    ) -> RawArtifactManifest:
        provider_token = _safe_token(
            provider.lower(),
            field_name="provider",
        )

        filename_token = _safe_token(
            filename,
            field_name="filename",
        )

        symbol_token = (
            _safe_token(
                symbol.upper(),
                field_name="symbol",
            )
            if symbol
            else "_all"
        )

        if market_date is None:
            date_parts = [
                "undated",
            ]
        else:
            date_parts = [
                f"{market_date.year:04d}",
                f"{market_date.month:02d}",
                f"{market_date.day:02d}",
            ]

        relative = Path(
            provider_token,
            asset_type.value.lower(),
            *date_parts,
            symbol_token,
            filename_token,
        )

        target = (
            self.root / relative
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        expected_hash = _sha256_bytes(
            payload
        )

        if target.exists():
            existing_hash = _sha256_file(
                target
            )

            if existing_hash != expected_hash:
                raise FileExistsError(
                    "immutable raw artifact conflict: "
                    f"{relative}"
                )

            return RawArtifactManifest(
                provider=provider_token,
                asset_type=asset_type,
                granularity=granularity,
                symbol=symbol,
                market_date=market_date,
                raw_path=str(relative),
                sha256=expected_hash,
                byte_size=len(payload),
                record_count=record_count,
                received_at=datetime.now(
                    timezone.utc
                ),
            )

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
                        "immutable raw artifact "
                        "conflict after race: "
                        f"{relative}"
                    )

        finally:
            temp.unlink(
                missing_ok=True
            )

        return RawArtifactManifest(
            provider=provider_token,
            asset_type=asset_type,
            granularity=granularity,
            symbol=symbol,
            market_date=market_date,
            raw_path=str(relative),
            sha256=expected_hash,
            byte_size=len(payload),
            record_count=record_count,
            received_at=datetime.now(
                timezone.utc
            ),
        )
