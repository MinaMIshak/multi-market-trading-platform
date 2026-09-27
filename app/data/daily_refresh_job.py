from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class DailyRefreshTarget:
    canonical_symbol: str
    provider_symbol: str

    def __post_init__(self) -> None:
        canonical = (
            self.canonical_symbol
            .strip()
            .upper()
        )
        provider = (
            self.provider_symbol
            .strip()
            .upper()
        )

        if not canonical:
            raise ValueError(
                "canonical_symbol cannot be empty"
            )

        if not provider:
            raise ValueError(
                "provider_symbol cannot be empty"
            )

        object.__setattr__(
            self,
            "canonical_symbol",
            canonical,
        )

        object.__setattr__(
            self,
            "provider_symbol",
            provider,
        )


@dataclass(frozen=True)
class DailyRefreshItemResult:
    canonical_symbol: str
    provider_symbol: str
    ingestion_id: str
    artifact_id: str
    record_count: int
    valid_bar_count: int
    quarantined_bar_count: int


@dataclass(frozen=True)
class DailyRefreshResult:
    start_date: date
    end_date: date
    snapshot_date: date
    items: tuple[
        DailyRefreshItemResult,
        ...,
    ]


class DailyRefreshJobError(RuntimeError):
    def __init__(
        self,
        *,
        canonical_symbol: str,
        completed: tuple[
            DailyRefreshItemResult,
            ...,
        ],
        cause: Exception,
    ) -> None:
        self.canonical_symbol = (
            canonical_symbol
        )
        self.completed = completed
        self.cause_type = (
            type(cause).__name__
        )

        super().__init__(
            "daily refresh failed for "
            f"{canonical_symbol}: "
            f"{self.cause_type}"
        )


class DailyRefreshJob:
    """
    Deterministic orchestration over the existing
    immutable daily ingestion/canonical pipeline.

    Scheduler claim/success/failure state is
    intentionally outside this component.
    """

    def __init__(
        self,
        *,
        ingestor: Any,
        pipeline: Any,
        canonical_store: Any,
        artifact_repository: Any,
        targets: tuple[
            DailyRefreshTarget,
            ...,
        ],
        target_admission: Any | None = None,
    ) -> None:
        # Validate and execute the same scope, including when callers supply
        # a mutable list or a one-shot iterable instead of a tuple.
        targets = tuple(targets)
        if not targets:
            raise ValueError(
                "daily refresh targets "
                "cannot be empty"
            )

        canonical_symbols = [
            item.canonical_symbol
            for item in targets
        ]

        if len(canonical_symbols) != len(
            set(canonical_symbols)
        ):
            raise ValueError(
                "duplicate canonical_symbol "
                "in daily refresh targets"
            )

        provider_symbols = [
            item.provider_symbol
            for item in targets
        ]

        if len(provider_symbols) != len(
            set(provider_symbols)
        ):
            raise ValueError(
                "duplicate provider_symbol "
                "in daily refresh targets"
            )

        self.ingestor = ingestor
        self.pipeline = pipeline
        self.canonical_store = (
            canonical_store
        )
        self.artifact_repository = (
            artifact_repository
        )
        self.targets = targets
        self.target_admission = target_admission

    def run(
        self,
        *,
        provider: Any,
        start_date: date,
        end_date: date,
        snapshot_date: date,
    ) -> DailyRefreshResult:
        # Daily windows are session dates, never timestamps or lexical strings.
        # Validate before alias admission or any provider/storage side effects.
        for field, value in (
            ("start_date", start_date),
            ("end_date", end_date),
            ("snapshot_date", snapshot_date),
        ):
            if type(value) is not date:
                raise ValueError(f"{field} must be a calendar date")

        if end_date < start_date:
            raise ValueError(
                "end_date cannot be "
                "before start_date"
            )

        if snapshot_date < end_date:
            raise ValueError(
                "snapshot_date cannot be "
                "before end_date"
            )

        provider_name = getattr(provider, "name", None)
        if (not isinstance(provider_name, str) or not provider_name.strip()
                or provider_name != provider_name.strip().lower()):
            raise ValueError("refresh provider name must be canonical")

        if self.target_admission is not None:
            self.target_admission(provider_name=provider_name, targets=self.targets)

        completed: list[
            DailyRefreshItemResult
        ] = []

        for target in self.targets:
            try:
                ingestion = (
                    self.ingestor.ingest(
                        provider=provider,
                        canonical_symbol=(
                            target
                            .canonical_symbol
                        ),
                        provider_symbol=(
                            target
                            .provider_symbol
                        ),
                        start_date=start_date,
                        end_date=end_date,
                        snapshot_date=(
                            snapshot_date
                        ),
                    )
                )

                expected_identity = {
                    "provider": provider_name,
                    "canonical_symbol": target.canonical_symbol,
                    "provider_symbol": target.provider_symbol,
                    "requested_start_date": start_date,
                    "requested_end_date": end_date,
                    "snapshot_date": snapshot_date,
                }
                if any(
                    getattr(ingestion, field, None) != expected
                    for field, expected in expected_identity.items()
                ):
                    raise ValueError("refresh ingestion identity or window mismatch")

                # Promotion persists canonical artifacts. Reject malformed
                # ingestion counts before invoking that side effect.
                if type(ingestion.record_count) is not int or ingestion.record_count < 0:
                    raise ValueError("refresh record_count must be a nonnegative integer")

                promotion = (
                    self.pipeline
                    .finalize_ingestion(
                        ingestion,
                        canonical_store=(
                            self
                            .canonical_store
                        ),
                        repository=(
                            self
                            .artifact_repository
                        ),
                    )
                )

                manifest = (
                    promotion
                    .canonical_manifest
                )

                counts = (
                    ingestion.record_count,
                    manifest.valid_bar_count,
                    manifest.quarantined_bar_count,
                )
                if any(type(count) is not int or count < 0 for count in counts):
                    raise ValueError("refresh counts must be nonnegative integers")
                if counts[0] != counts[1] + counts[2]:
                    raise ValueError("refresh counts do not reconcile")

                completed.append(
                    DailyRefreshItemResult(
                        canonical_symbol=(
                            target
                            .canonical_symbol
                        ),
                        provider_symbol=(
                            target
                            .provider_symbol
                        ),
                        ingestion_id=str(
                            ingestion
                            .manifest
                            .ingestion_id
                        ),
                        artifact_id=str(
                            promotion.artifact_id
                        ),
                        record_count=counts[0],
                        valid_bar_count=counts[1],
                        quarantined_bar_count=counts[2],
                    )
                )

            except Exception as exc:
                raise DailyRefreshJobError(
                    canonical_symbol=(
                        target
                        .canonical_symbol
                    ),
                    completed=tuple(
                        completed
                    ),
                    cause=exc,
                ) from exc

        return DailyRefreshResult(
            start_date=start_date,
            end_date=end_date,
            snapshot_date=snapshot_date,
            items=tuple(completed),
        )
