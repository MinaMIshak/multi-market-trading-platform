from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.data.raw_store import (
    ImmutableRawStore,
)
from app.data.security_master import (
    InstrumentType,
    build_canonical_security_master,
)
from app.data.security_master_ingestion import (
    SecurityMasterIngestor,
    SecurityMasterProvider,
)


EGID_PROVIDER = "egid"

# A replacement snapshot may not drop more than
# this fraction of the provider's admitted
# instruments in one pass. Mass delisting in a
# single day is implausible; a short or truncated
# upstream response is not.
MIN_RETAINED_FRACTION = 0.9


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

    def _require_complete_snapshot(
        self,
        *,
        provider: str,
        instruments: list,
    ) -> None:
        """
        Fail closed before replacing the
        admitted master with an empty,
        equity-less or truncated snapshot.

        The raw artifact stays preserved
        either way; only admission is refused.
        """

        if not any(
            instrument.instrument_type
            is InstrumentType.EQUITY
            for instrument in instruments
        ):
            raise ValueError(
                "security master snapshot "
                "contains no equities"
            )

        previous = (
            self
            .security_master_repository
            .count_provider_instruments(
                provider
            )
        )

        if len(instruments) < (
            previous * MIN_RETAINED_FRACTION
        ):
            raise ValueError(
                "security master snapshot "
                "contracted beyond the "
                "retention threshold"
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

            self._require_complete_snapshot(
                provider=ingestion.provider,
                instruments=instruments,
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
