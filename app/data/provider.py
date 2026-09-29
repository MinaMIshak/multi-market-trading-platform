from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.data.models import (
    BarGranularity,
)


@dataclass(frozen=True)
class ProviderResponse:
    payload: bytes
    filename: str

    source_uri: str | None = None
    record_count: int | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class ProviderBatchResponse:
    responses: tuple[
        ProviderResponse,
        ...,
    ]

    record_count: int | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


class MarketDataProvider(Protocol):
    @property
    def name(self) -> str:
        ...

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResponse:
        """Raw bytes of a JSON list of provider-neutral daily rows.

        Each row: ``date`` (market date, YYYY-MM-DD, exchange-local), raw
        unadjusted ``open``/``high``/``low``/``close`` and ``volume``;
        ``adjusted_close`` only when the source actually supplies it
        (audit reference, never an execution price). ``record_count`` must
        equal the list length. Adapters map provider fields to this shape
        without inventing values; candidate eligibility additionally
        requires an ADMITTED declaration in app/data/source_admission.py.
        """
        ...

    def fetch_intraday_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        granularity: BarGranularity,
    ) -> ProviderResponse:
        ...

    def fetch_corporate_actions(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResponse:
        ...


class IndexDataProvider(Protocol):
    @property
    def name(self) -> str:
        ...

    def fetch_index_bars(
        self,
        *,
        index_name: str,
        start_date: date,
        end_date: date,
        page_size: int = 1000,
    ) -> ProviderBatchResponse:
        ...
