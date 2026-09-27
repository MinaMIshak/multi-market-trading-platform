import json

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.data.daily_ingestion import (
    DailyBarIngestor,
)
from app.data.daily_refresh_admission import (
    DailyRefreshAdmissionError,
    DailyRefreshAdmissionPolicy,
)
from app.data.provider import ProviderResponse
from app.data.raw_store import ImmutableRawStore


EXPECTED = date(2026, 9, 10)


class Resolver:
    def __init__(self):
        self.instrument_id = str(
            uuid4()
        )

    def resolve(
        self,
        value,
        *,
        provider=None,
    ):
        assert (value, provider) in (("COMI", "canonical"), ("COMI.EGX", "eodhd"))

        return {
            "instrument_id": (
                self.instrument_id
            ),
            "canonical_ticker": "COMI",
            "matched_provider": provider,
            "matched_alias_value": value,
        }


class Repository:
    def __init__(self):
        self.saved = []

    def save_manifest(
        self,
        manifest,
        **kwargs,
    ):
        self.saved.append(
            (manifest, kwargs)
        )
        return manifest


class Provider:
    name = "eodhd"

    def __init__(self, rows):
        self.rows = rows

    def fetch_daily_bars(
        self,
        *,
        symbol,
        start_date,
        end_date,
    ):
        payload = json.dumps(
            self.rows,
            separators=(",", ":"),
        ).encode("utf-8")

        return ProviderResponse(
            payload=payload,
            filename=(
                f"{symbol}-D1.json"
            ),
            source_uri=(
                "https://example.test/eod"
            ),
            record_count=len(
                self.rows
            ),
            metadata={},
        )


def make_rows(
    count,
    end_date,
):
    start = (
        end_date
        - timedelta(days=count - 1)
    )

    return [
        {
            "date": (
                start
                + timedelta(days=index)
            ).isoformat(),
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "adjusted_close": 101.0,
            "volume": 1000000 + index,
        }
        for index in range(count)
    ]


def make_ingestor(
    tmp_path,
    repository,
):
    return DailyBarIngestor(
        raw_store=ImmutableRawStore(
            tmp_path / "raw"
        ),
        repository=repository,
        resolver=Resolver(),
        admission_policy=(
            DailyRefreshAdmissionPolicy(
                minimum_valid_bars=260
            )
        ),
    )


def run_ingestion(
    ingestor,
    rows,
):
    return ingestor.ingest(
        provider=Provider(rows),
        canonical_symbol="COMI",
        provider_symbol="COMI.EGX",
        start_date=(
            EXPECTED
            - timedelta(days=400)
        ),
        end_date=EXPECTED,
        snapshot_date=EXPECTED,
    )


def raw_files(tmp_path):
    root = tmp_path / "raw"

    if not root.exists():
        return []

    return [
        path
        for path in root.rglob("*")
        if path.is_file()
    ]


def test_stale_response_writes_nothing(
    tmp_path,
):
    repository = Repository()

    ingestor = make_ingestor(
        tmp_path,
        repository,
    )

    rows = make_rows(
        260,
        EXPECTED - timedelta(days=1),
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="stale",
    ):
        run_ingestion(
            ingestor,
            rows,
        )

    assert raw_files(tmp_path) == []
    assert repository.saved == []


def test_insufficient_response_writes_nothing(
    tmp_path,
):
    repository = Repository()

    ingestor = make_ingestor(
        tmp_path,
        repository,
    )

    rows = make_rows(
        259,
        EXPECTED,
    )

    with pytest.raises(
        DailyRefreshAdmissionError,
        match="insufficient",
    ):
        run_ingestion(
            ingestor,
            rows,
        )

    assert raw_files(tmp_path) == []
    assert repository.saved == []


def test_admitted_response_persists_raw(
    tmp_path,
):
    repository = Repository()

    ingestor = make_ingestor(
        tmp_path,
        repository,
    )

    result = run_ingestion(
        ingestor,
        make_rows(
            260,
            EXPECTED,
        ),
    )

    assert result.record_count == 260
    assert len(raw_files(tmp_path)) == 1
    assert len(repository.saved) == 1
