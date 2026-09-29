from __future__ import annotations

from datetime import date

import pytest

from app.data.models import (
    IngestionStatus,
)
from app.data.provider import (
    ProviderResponse,
)
from app.data.raw_store import (
    ImmutableRawStore,
)
from app.data.security_master_ingestion import (
    SecurityMasterIngestor,
)


class FakeRepository:
    def __init__(self) -> None:
        self.saved = []

    def save_manifest(
        self,
        manifest,
        *,
        status=IngestionStatus.RECEIVED,
        source_uri=None,
        metadata=None,
    ):
        self.saved.append(
            {
                "manifest": manifest,
                "status": status,
                "source_uri": source_uri,
                "metadata": metadata,
            }
        )

        return manifest


class FakeSecurityMasterProvider:
    name = "egid"

    def __init__(self, response) -> None:
        self.response = response
        self.calls = 0

    def fetch_security_master(self):
        self.calls += 1
        return self.response


def make_response(
    *,
    payload=b'[{"SYMBOL_CODE":"EGS1"}]',
    record_count=1,
):
    return ProviderResponse(
        payload=payload,
        filename="market-watch-names.json",
        source_uri=(
            "https://ticker.egidegypt.com/api/"
            "DelayedFeed/getAllMarketWatchNames"
        ),
        record_count=record_count,
        metadata={"access": "anonymous"},
    )


def test_ingests_snapshot_as_immutable_raw_artifact(
    tmp_path,
):
    raw_store = ImmutableRawStore(tmp_path / "raw")
    repository = FakeRepository()
    provider = FakeSecurityMasterProvider(
        make_response()
    )

    ingestor = SecurityMasterIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    result = ingestor.ingest(
        provider=provider,
        snapshot_date=date(2026, 9, 29),
    )

    assert result.provider == "egid"
    assert result.record_count == 1
    assert provider.calls == 1
    assert len(repository.saved) == 1
    assert (
        repository.saved[0]["status"]
        == IngestionStatus.RECEIVED
    )
    assert (
        repository.saved[0]["metadata"]
        ["snapshot_date"]
        == "2026-09-29"
    )

    files = list((tmp_path / "raw").rglob("*.json"))
    assert len(files) == 1
    assert files[0].read_bytes() == (
        b'[{"SYMBOL_CODE":"EGS1"}]'
    )


def test_same_payload_is_idempotent(tmp_path):
    raw_store = ImmutableRawStore(tmp_path / "raw")
    repository = FakeRepository()
    provider = FakeSecurityMasterProvider(
        make_response()
    )

    ingestor = SecurityMasterIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    kwargs = {
        "provider": provider,
        "snapshot_date": date(2026, 9, 29),
    }

    first = ingestor.ingest(**kwargs)
    second = ingestor.ingest(**kwargs)

    assert (
        first.manifest.raw_path
        == second.manifest.raw_path
    )
    assert (
        first.manifest.sha256
        == second.manifest.sha256
    )

    files = list((tmp_path / "raw").rglob("*.json"))
    assert len(files) == 1


def test_changed_payload_cannot_overwrite_snapshot(
    tmp_path,
):
    raw_store = ImmutableRawStore(tmp_path / "raw")
    repository = FakeRepository()

    ingestor = SecurityMasterIngestor(
        raw_store=raw_store,
        repository=repository,
    )

    ingestor.ingest(
        provider=FakeSecurityMasterProvider(
            make_response()
        ),
        snapshot_date=date(2026, 9, 29),
    )

    changed_provider = (
        FakeSecurityMasterProvider(
            make_response(
                payload=(
                    b'[{"SYMBOL_CODE":"EGS1",'
                    b'"revised":true}]'
                )
            )
        )
    )

    with pytest.raises(
        FileExistsError,
        match="immutable raw artifact conflict",
    ):
        ingestor.ingest(
            provider=changed_provider,
            snapshot_date=date(2026, 9, 29),
        )


def test_rejects_missing_record_count(tmp_path):
    ingestor = SecurityMasterIngestor(
        raw_store=ImmutableRawStore(
            tmp_path / "raw"
        ),
        repository=FakeRepository(),
    )

    provider = FakeSecurityMasterProvider(
        make_response(record_count=None)
    )

    with pytest.raises(
        ValueError, match="record_count"
    ):
        ingestor.ingest(
            provider=provider,
            snapshot_date=date(2026, 9, 29),
        )

    assert not (tmp_path / "raw").exists()


def test_rejects_non_canonical_provider_name(
    tmp_path,
):
    provider = FakeSecurityMasterProvider(
        make_response()
    )
    provider.name = "EGID"

    ingestor = SecurityMasterIngestor(
        raw_store=ImmutableRawStore(
            tmp_path / "raw"
        ),
        repository=FakeRepository(),
    )

    with pytest.raises(
        ValueError, match="canonical"
    ):
        ingestor.ingest(
            provider=provider,
            snapshot_date=date(2026, 9, 29),
        )
