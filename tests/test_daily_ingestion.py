from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.data.daily_ingestion import DailyBarIngestor
from app.data.provider import ProviderResponse
from app.data.raw_store import ImmutableRawStore


class Resolver:
    def __init__(self, ticker="COMI"):
        self.ticker = ticker

    def resolve(self, value, *, provider=None):
        assert (value, provider) in (("COMI", "canonical"), ("COMI.EGX", "eodhd"))
        return {
            "instrument_id": "instrument-1",
            "canonical_ticker": self.ticker,
            "matched_provider": provider,
            "matched_alias_value": value,
        }


class Provider:
    name = "eodhd"

    def __init__(self, count=1):
        self.count = count
        self.seen_symbol = None

    def fetch_daily_bars(
        self, *, symbol, start_date, end_date
    ):
        self.seen_symbol = symbol
        return ProviderResponse(
            payload=b'[{"raw":"exact"}]',
            filename="COMI.EGX-D1.json",
            source_uri="https://example.test/api/eod/COMI.EGX",
            record_count=self.count,
            metadata={"endpoint": "eod"},
        )


class Repo:
    def __init__(self):
        self.saved = None

    def save_manifest(
        self, manifest, *, status, source_uri, metadata
    ):
        self.saved = (
            manifest, status, source_uri, metadata
        )

        return manifest


def test_identity_and_provenance_boundary():
    with TemporaryDirectory() as tmp:
        provider = Provider()
        repo = Repo()

        ingestor = DailyBarIngestor(
            raw_store=ImmutableRawStore(Path(tmp)),
            repository=repo,
            resolver=Resolver(),
        )

        result = ingestor.ingest(
            provider=provider,
            canonical_symbol="comi",
            provider_symbol="comi.egx",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 9, 9),
            snapshot_date=date(2026, 9, 9),
        )

        assert provider.seen_symbol == "COMI.EGX"
        assert result.canonical_symbol == "COMI"
        assert result.provider_symbol == "COMI.EGX"
        assert result.instrument_id == "instrument-1"

        manifest, _, _, metadata = repo.saved
        assert manifest.symbol == "COMI"
        assert "/COMI/" in f"/{manifest.raw_path}"
        assert metadata["provider_symbol"] == "COMI.EGX"
        assert metadata["canonical_symbol"] == "COMI"
        assert metadata["instrument_id"] == "instrument-1"


def test_resolved_ticker_mismatch_is_rejected():
    with TemporaryDirectory() as tmp:
        ingestor = DailyBarIngestor(
            raw_store=ImmutableRawStore(Path(tmp)),
            repository=Repo(),
            resolver=Resolver("WRONG"),
        )

        with pytest.raises(
            ValueError,
            match="resolved canonical ticker mismatch",
        ):
            ingestor.ingest(
                provider=Provider(),
                canonical_symbol="COMI",
                provider_symbol="COMI.EGX",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 9, 9),
                snapshot_date=date(2026, 9, 9),
            )


def test_record_count_is_required():
    with TemporaryDirectory() as tmp:
        ingestor = DailyBarIngestor(
            raw_store=ImmutableRawStore(Path(tmp)),
            repository=Repo(),
            resolver=Resolver(),
        )

        with pytest.raises(
            ValueError,
            match="record_count is required",
        ):
            ingestor.ingest(
                provider=Provider(None),
                canonical_symbol="COMI",
                provider_symbol="COMI.EGX",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 9, 9),
                snapshot_date=date(2026, 9, 9),
            )


def test_missing_record_count_writes_nothing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = Repo()

        ingestor = DailyBarIngestor(
            raw_store=ImmutableRawStore(root),
            repository=repo,
            resolver=Resolver(),
        )

        with pytest.raises(
            ValueError,
            match="record_count is required",
        ):
            ingestor.ingest(
                provider=Provider(None),
                canonical_symbol="COMI",
                provider_symbol="COMI.EGX",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 9, 9),
                snapshot_date=date(2026, 9, 9),
            )

        assert repo.saved is None
        assert list(root.rglob("*")) == []


def test_provider_payload_is_preserved_exactly():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        provider = Provider()
        repo = Repo()

        ingestor = DailyBarIngestor(
            raw_store=ImmutableRawStore(root),
            repository=repo,
            resolver=Resolver(),
        )

        result = ingestor.ingest(
            provider=provider,
            canonical_symbol="COMI",
            provider_symbol="COMI.EGX",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 9, 9),
            snapshot_date=date(2026, 9, 9),
        )

        stored = (
            root / result.manifest.raw_path
        ).read_bytes()

        assert stored == b'[{"raw":"exact"}]'
        assert result.manifest.byte_size == len(stored)
