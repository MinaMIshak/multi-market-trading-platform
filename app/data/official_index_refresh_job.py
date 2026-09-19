from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


OFFICIAL_PROVIDER = (
    "egx_official_public"
)

REQUIRED_OFFICIAL_INDICES = (
    "CASE30",
    "EGX70_EWI",
    "EGX100_EWI",
)


class OfficialIndexRefreshJobError(
    RuntimeError
):
    def __init__(
        self,
        *,
        index_name: str,
        cause_type: str,
    ) -> None:
        self.index_name = index_name
        self.cause_type = cause_type

        super().__init__(
            "official index refresh failed:"
            f"{index_name}:"
            f"{cause_type}"
        )


@dataclass(frozen=True)
class OfficialIndexRefreshItem:
    index_name: str
    artifact_id: str
    record_count: int
    newest_market_date: date


@dataclass(frozen=True)
class OfficialIndexRefreshResult:
    snapshot_date: date

    items: tuple[
        OfficialIndexRefreshItem,
        ...,
    ]


class OfficialIndexRefreshJob:
    """
    Acquire and canonicalize the three
    official EGX calendar-evidence indices.

    Provider construction is deliberately
    outside this job.
    """

    def __init__(
        self,
        *,
        ingestor: Any,
        pipeline: Any,
        canonical_store: Any,
        artifact_repository: Any,
        index_names: tuple[
            str,
            ...,
        ] = REQUIRED_OFFICIAL_INDICES,
    ) -> None:
        normalized = tuple(
            name.strip().upper()
            for name in index_names
        )

        if (
            not normalized
            or any(
                not name
                for name in normalized
            )
        ):
            raise ValueError(
                "index_names cannot "
                "contain empty values"
            )

        if len(normalized) != len(
            set(normalized)
        ):
            raise ValueError(
                "index_names must be unique"
            )

        self.ingestor = ingestor
        self.pipeline = pipeline
        self.canonical_store = (
            canonical_store
        )
        self.artifact_repository = (
            artifact_repository
        )
        self.index_names = normalized

    def run(
        self,
        *,
        provider: Any,
        start_date: date,
        end_date: date,
        snapshot_date: date,
        page_size: int = 1000,
    ) -> OfficialIndexRefreshResult:
        provider_name = (
            provider.name
            .strip()
            .lower()
        )

        if (
            provider_name
            != OFFICIAL_PROVIDER
        ):
            raise ValueError(
                "official index refresh "
                "requires egx_official_public"
            )

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

        if (
            isinstance(
                page_size,
                bool,
            )
            or not isinstance(
                page_size,
                int,
            )
            or not 1 <= page_size <= 1000
        ):
            raise ValueError(
                "page_size must be "
                "between 1 and 1000"
            )

        items: list[
            OfficialIndexRefreshItem
        ] = []

        for index_name in self.index_names:
            try:
                ingestion = (
                    self.ingestor.ingest(
                        provider=provider,
                        index_name=(
                            index_name
                        ),
                        start_date=(
                            start_date
                        ),
                        end_date=(
                            end_date
                        ),
                        snapshot_date=(
                            snapshot_date
                        ),
                        page_size=page_size,
                    )
                )

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

            except Exception as exc:
                raise (
                    OfficialIndexRefreshJobError(
                        index_name=(
                            index_name
                        ),
                        cause_type=(
                            type(exc)
                            .__name__
                        ),
                    )
                ) from exc

            items.append(
                OfficialIndexRefreshItem(
                    index_name=index_name,
                    artifact_id=(
                        promotion
                        .artifact_id
                    ),
                    record_count=(
                        promotion
                        .canonical_manifest
                        .record_count
                    ),
                    newest_market_date=(
                        promotion
                        .canonical_manifest
                        .newest_market_date
                    ),
                )
            )

        return OfficialIndexRefreshResult(
            snapshot_date=snapshot_date,
            items=tuple(items),
        )
