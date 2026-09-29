from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.data.raw_store import (
    ImmutableRawStore,
)
from app.data.security_master import (
    build_canonical_security_master,
)
from app.data.security_master_ingestion import (
    SecurityMasterIngestor,
    SecurityMasterProvider,
)


EGID_PROVIDER = "egid"


class SecurityMasterRefreshJobError(
    RuntimeError
):
    def __init__(
        self,
        *,
        provider: str,
        cause_type: str,
    ) -> None:
        self.provider = provider
        self.cause_type = cause_type

        super().__init__(
            "security master refresh "
            f"failed: {provider}: "
            f"{cause_type}"
        )


@dataclass(frozen=True)
class SecurityMasterRefreshResult:
    provider: str
    snapshot_date: date

    artifact_sha256: str
    record_count: int
    instrument_count: int


class SecurityMasterRefreshJob:
    """
    Acquire, canonicalize and admit a full
    provider security-master snapshot.

    Provider construction is deliberately
    outside this job.
    """

    def __init__(
        self,
        *,
        ingestor: SecurityMasterIngestor,
        raw_store: ImmutableRawStore,
        security_master_repository: Any,
        required_provider: str = (
            EGID_PROVIDER
        ),
    ) -> None:
        self.ingestor = ingestor
        self.raw_store = raw_store
        self.security_master_repository = (
            security_master_repository
        )
        self.required_provider = (
            required_provider
            .strip()
            .lower()
        )

    def run(
        self,
        *,
        provider: SecurityMasterProvider,
        snapshot_date: date,
    ) -> SecurityMasterRefreshResult:
        provider_name = (
            provider.name.strip().lower()
        )

        if (
            provider_name
            != self.required_provider
        ):
            raise ValueError(
                "security master refresh "
                "requires provider "
                f"{self.required_provider!r}"
            )

        try:
            ingestion = self.ingestor.ingest(
                provider=provider,
                snapshot_date=snapshot_date,
            )

            payload = (
                self.raw_store.read_verified(
                    ingestion.manifest
                )
            )

            try:
                records = json.loads(
                    payload.decode("utf-8")
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                raise ValueError(
                    "security master payload "
                    "is not valid JSON"
                ) from exc

            if not isinstance(
                records, list
            ):
                raise ValueError(
                    "security master payload "
                    "must be a JSON list"
                )

            if len(records) != (
                ingestion.record_count
            ):
                raise ValueError(
                    "security master record "
                    "count mismatch"
                )

            instruments = (
                build_canonical_security_master(
                    records=records,
                    source_sha256=(
                        ingestion
                        .manifest
                        .sha256
                    ),
                    source_market_date=(
                        snapshot_date
                        .isoformat()
                    ),
                )
            )

            (
                self
                .security_master_repository
                .replace_provider_snapshot(
                    provider=(
                        ingestion.provider
                    ),
                    instruments=instruments,
                )
            )

        except Exception as exc:
            raise (
                SecurityMasterRefreshJobError(
                    provider=provider_name,
                    cause_type=(
                        type(exc).__name__
                    ),
                )
            ) from exc

        return SecurityMasterRefreshResult(
            provider=ingestion.provider,
            snapshot_date=snapshot_date,
            artifact_sha256=(
                ingestion.manifest.sha256
            ),
            record_count=(
                ingestion.record_count
            ),
            instrument_count=len(
                instruments
            ),
        )
