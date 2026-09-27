from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.data.daily_canonical import (
    CanonicalDailyBar,
    DailyBarSemanticClass,
    canonicalize_daily_row,
)
from app.data.provider import ProviderResponse


class DailyRefreshAdmissionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class DailyRefreshAdmissionSummary:
    record_count: int
    valid_bar_count: int
    quarantined_bar_count: int
    newest_market_date: date
    newest_valid_market_date: date


@dataclass(frozen=True)
class DailyRefreshAdmissionPolicy:
    minimum_valid_bars: int = 260

    def __post_init__(self) -> None:
        if (type(self.minimum_valid_bars) is not int
                or self.minimum_valid_bars <= 0):
            raise ValueError(
                "minimum_valid_bars must "
                "be positive integer"
            )

    def validate_rows(
        self,
        rows: tuple[
            CanonicalDailyBar,
            ...,
        ],
        *,
        expected_market_date: date,
        requested_start_date: date | None = None,
    ) -> DailyRefreshAdmissionSummary:
        if requested_start_date is not None:
            if (type(requested_start_date) is not date
                    or type(expected_market_date) is not date
                    or requested_start_date > expected_market_date):
                raise DailyRefreshAdmissionError("invalid requested daily window")

        if not rows:
            raise DailyRefreshAdmissionError(
                "daily history is empty"
            )

        ordered = tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.market_date
                ),
            )
        )

        dates = [
            row.market_date
            for row in ordered
        ]

        if len(dates) != len(set(dates)):
            raise DailyRefreshAdmissionError(
                "duplicate daily market date"
            )

        if requested_start_date is not None and dates[0] < requested_start_date:
            raise DailyRefreshAdmissionError("daily history outside requested window")

        valid = tuple(
            row
            for row in ordered
            if row.semantic_class
            == DailyBarSemanticClass
            .VALID_EXECUTABLE
        )

        newest = ordered[-1].market_date

        if newest != expected_market_date:
            raise DailyRefreshAdmissionError(
                "daily history is stale"
            )

        if not valid:
            raise DailyRefreshAdmissionError(
                "no executable daily bars"
            )

        newest_valid = (
            valid[-1].market_date
        )

        if (
            newest_valid
            != expected_market_date
        ):
            raise DailyRefreshAdmissionError(
                "latest daily bar "
                "is not executable"
            )

        if (
            len(valid)
            < self.minimum_valid_bars
        ):
            raise DailyRefreshAdmissionError(
                "insufficient executable "
                "daily history"
            )

        return DailyRefreshAdmissionSummary(
            record_count=len(ordered),
            valid_bar_count=len(valid),
            quarantined_bar_count=(
                len(ordered) - len(valid)
            ),
            newest_market_date=newest,
            newest_valid_market_date=(
                newest_valid
            ),
        )

    def validate_provider_response(
        self,
        response: ProviderResponse,
        *,
        instrument_id: str,
        canonical_symbol: str,
        provider_symbol: str,
        provider: str,
        snapshot_date: date,
        expected_market_date: date,
        requested_start_date: date | None = None,
    ) -> DailyRefreshAdmissionSummary:
        if response.record_count is None:
            raise DailyRefreshAdmissionError(
                "provider record_count "
                "is required"
            )

        if type(response.record_count) is not int or response.record_count < 0:
            raise DailyRefreshAdmissionError(
                "provider record_count must be a nonnegative integer"
            )

        try:
            document = json.loads(
                response.payload.decode(
                    "utf-8"
                )
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise DailyRefreshAdmissionError(
                "invalid provider daily JSON"
            ) from exc

        if not isinstance(document, list):
            raise DailyRefreshAdmissionError(
                "provider daily payload "
                "must be a list"
            )

        if (
            len(document)
            != response.record_count
        ):
            raise DailyRefreshAdmissionError(
                "provider record_count "
                "mismatch"
            )

        digest = hashlib.sha256(
            response.payload
        ).hexdigest()

        identifier = UUID(
            instrument_id
        )

        rows = tuple(
            canonicalize_daily_row(
                row,
                instrument_id=identifier,
                canonical_symbol=(
                    canonical_symbol
                ),
                provider_symbol=(
                    provider_symbol
                ),
                source_provider=provider,
                source_snapshot_date=(
                    snapshot_date
                ),
                source_row_number=index,
                source_sha256=digest,
            )
            for index, row in enumerate(
                document,
                start=1,
            )
        )

        return self.validate_rows(
            rows,
            expected_market_date=(
                expected_market_date
            ),
            requested_start_date=requested_start_date,
        )
