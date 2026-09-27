from datetime import (
    date,
    datetime,
    timezone,
)
from uuid import uuid4

from app.data.ingestion_repository import (
    DataIngestionRepository,
)
from app.data.models import (
    BarGranularity,
    DataAssetType,
    IngestionStatus,
    RawArtifactManifest,
)
from app.storage import Database


def test_lookup_returns_persisted_ingestion_identity(
    tmp_path,
):
    db = Database(tmp_path / "platform.db")
    db.initialize()

    repo = DataIngestionRepository(db)

    ingestion_id = uuid4()

    manifest = RawArtifactManifest(
        ingestion_id=ingestion_id,
        provider="eodhd",
        asset_type=DataAssetType.DAILY_BARS,
        granularity=BarGranularity.D1,
        symbol="COMI",
        market_date=date(2026, 9, 9),
        raw_path=(
            "eodhd/daily_bars/"
            "2026/09/09/COMI/test.json"
        ),
        sha256="a" * 64,
        byte_size=123,
        record_count=2,
        received_at=datetime.now(timezone.utc),
    )

    repo.save_manifest(
        manifest,
        status=IngestionStatus.RECEIVED,
    )

    persisted = repo.get_manifest_by_raw_path(
        manifest.raw_path
    )

    assert persisted is not None
    assert persisted.ingestion_id == ingestion_id
    assert persisted.raw_path == manifest.raw_path
    assert persisted.sha256 == manifest.sha256
    assert persisted.record_count == 2


def test_lookup_missing_path_returns_none(
    tmp_path,
):
    db = Database(tmp_path / "platform.db")
    db.initialize()

    repo = DataIngestionRepository(db)

    assert (
        repo.get_manifest_by_raw_path(
            "missing/raw/path.json"
        )
        is None
    )


def make_manifest(
    *,
    ingestion_id=None,
    sha256="a" * 64,
):
    return RawArtifactManifest(
        ingestion_id=(
            ingestion_id
            if ingestion_id is not None
            else uuid4()
        ),
        provider="eodhd",
        asset_type=DataAssetType.DAILY_BARS,
        granularity=BarGranularity.D1,
        symbol="COMI",
        market_date=date(2026, 9, 9),
        raw_path=(
            "eodhd/daily_bars/"
            "2026/09/09/COMI/replay.json"
        ),
        sha256=sha256,
        byte_size=123,
        record_count=2,
        received_at=datetime.now(timezone.utc),
    )


def test_exact_replay_returns_original_identity(
    tmp_path,
):
    db = Database(tmp_path / "platform.db")
    db.initialize()
    repo = DataIngestionRepository(db)

    first = make_manifest()
    second = make_manifest()

    persisted_first = repo.save_manifest(
        first,
        status=IngestionStatus.RECEIVED,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    persisted_second = repo.save_manifest(
        second,
        status=IngestionStatus.RECEIVED,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    assert (
        persisted_first.ingestion_id
        == persisted_second.ingestion_id
        == first.ingestion_id
    )

    assert persisted_second.ingestion_id != (
        second.ingestion_id
    )

    assert repo.count_ingestions() == 1


def test_validated_cannot_regress_to_received(
    tmp_path,
):
    db = Database(tmp_path / "platform.db")
    db.initialize()
    repo = DataIngestionRepository(db)

    first = make_manifest()

    repo.save_manifest(
        first,
        status=IngestionStatus.VALIDATED,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    replay = make_manifest()

    persisted = repo.save_manifest(
        replay,
        status=IngestionStatus.RECEIVED,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    assert persisted.ingestion_id == first.ingestion_id

    with db.connect() as con:
        status = con.execute(
            """
            SELECT status
            FROM data_ingestions
            WHERE raw_path=?
            """,
            (first.raw_path,),
        ).fetchone()["status"]

    assert status == "VALIDATED"


def test_same_raw_path_hash_conflict_is_rejected(
    tmp_path,
):
    import pytest

    db = Database(tmp_path / "platform.db")
    db.initialize()
    repo = DataIngestionRepository(db)

    first = make_manifest(
        sha256="a" * 64
    )

    repo.save_manifest(
        first,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    conflicting = make_manifest(
        sha256="b" * 64
    )

    with pytest.raises(
        ValueError,
        match="conflict on sha256",
    ):
        repo.save_manifest(
            conflicting,
            metadata={
                "provider_symbol": "COMI.EGX"
            },
        )


def test_same_raw_path_metadata_conflict_is_rejected(
    tmp_path,
):
    import pytest

    db = Database(tmp_path / "platform.db")
    db.initialize()
    repo = DataIngestionRepository(db)

    first = make_manifest()

    repo.save_manifest(
        first,
        metadata={"provider_symbol": "COMI.EGX"},
    )

    replay = make_manifest()

    with pytest.raises(
        ValueError,
        match="conflict on metadata_json",
    ):
        repo.save_manifest(
            replay,
            metadata={
                "provider_symbol": "WRONG.EGX"
            },
        )


def test_daily_ingestor_replay_returns_persisted_identity(
    tmp_path,
):
    from app.data.daily_ingestion import (
        DailyBarIngestor,
    )
    from app.data.provider import ProviderResponse
    from app.data.raw_store import ImmutableRawStore

    class Resolver:
        def resolve(
            self,
            value,
            *,
            provider=None,
        ):
            assert (value, provider) in (("COMI", "canonical"), ("COMI.EGX", "eodhd"))

            return {
                "instrument_id": "instrument-1",
                "canonical_ticker": "COMI",
                "matched_provider": provider,
                "matched_alias_value": value,
            }

    class Provider:
        name = "eodhd"

        def fetch_daily_bars(
            self,
            *,
            symbol,
            start_date,
            end_date,
        ):
            assert symbol == "COMI.EGX"

            return ProviderResponse(
                payload=(
                    b'[{"date":"2026-09-08",'
                    b'"open":140,"high":141,'
                    b'"low":138,"close":139,'
                    b'"adjusted_close":139,'
                    b'"volume":100}]'
                ),
                filename="COMI.EGX-D1.json",
                source_uri=(
                    "https://example.test/"
                    "api/eod/COMI.EGX"
                ),
                record_count=1,
                metadata={"endpoint": "eod"},
            )

    db = Database(
        tmp_path / "platform.db"
    )
    db.initialize()

    repository = DataIngestionRepository(
        db
    )

    ingestor = DailyBarIngestor(
        raw_store=ImmutableRawStore(
            tmp_path / "raw"
        ),
        repository=repository,
        resolver=Resolver(),
    )

    kwargs = {
        "provider": Provider(),
        "canonical_symbol": "COMI",
        "provider_symbol": "COMI.EGX",
        "start_date": date(2026, 9, 8),
        "end_date": date(2026, 9, 8),
        "snapshot_date": date(2026, 9, 9),
    }

    first = ingestor.ingest(
        **kwargs
    )
    second = ingestor.ingest(
        **kwargs
    )

    assert (
        first.manifest.ingestion_id
        == second.manifest.ingestion_id
    )

    assert (
        first.manifest.raw_path
        == second.manifest.raw_path
    )

    assert repository.count_ingestions() == 1

    persisted = (
        repository
        .get_manifest_by_raw_path(
            first.manifest.raw_path
        )
    )

    assert persisted is not None

    assert (
        persisted.ingestion_id
        == first.manifest.ingestion_id
    )
