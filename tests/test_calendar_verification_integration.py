from datetime import (
    date,
    datetime,
    timezone,
)
import hashlib
from uuid import uuid4

from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.calendar_verification import (
    CalendarVerificationPolicy,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.core.schedule import CalendarTruth
from app.data.index_canonical_store import (
    SEMANTIC_CONTRACT_VERSION,
    SERIALIZATION_FORMAT,
)
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage import Database
from app.storage.repository import (
    TradingRepository,
)


DAY = date(2026, 9, 10)
VERIFIED_AT = datetime(
    2026,
    9,
    10,
    14,
    45,
    tzinfo=timezone.utc,
)


def insert_index_artifact(
    database,
    symbol,
    *,
    snapshot=DAY,
    newest=DAY,
):
    artifact_id = str(uuid4())
    digest = hashlib.sha256(
        symbol.encode()
    ).hexdigest()

    with database.connect() as con:
        con.execute(
            """
            INSERT INTO canonical_data_artifacts (
                artifact_id,
                provider,
                asset_type,
                granularity,
                symbol,
                source_snapshot_date,
                canonical_path,
                sha256,
                byte_size,
                record_count,
                oldest_market_date,
                newest_market_date,
                semantic_contract_version,
                serialization_format,
                full_ohlc_valid_count,
                legacy_close_reference_count,
                quarantined_anomaly_count,
                full_ohlc_usable_count,
                close_history_usable_count,
                status,
                created_at,
                validated_at,
                metadata_json
            )
            VALUES (
                ?,
                'egx_official_public',
                'INDEX_BARS',
                'D1',
                ?,
                ?,
                ?,
                ?,
                1,
                1,
                ?,
                ?,
                ?,
                ?,
                1,
                0,
                0,
                1,
                1,
                'VALIDATED',
                ?,
                ?,
                '{}'
            )
            """,
            (
                artifact_id,
                symbol,
                snapshot.isoformat(),
                (
                    "canonical/index/"
                    f"{symbol}/test.json"
                ),
                digest,
                newest.isoformat(),
                newest.isoformat(),
                SEMANTIC_CONTRACT_VERSION,
                SERIALIZATION_FORMAT,
                VERIFIED_AT.isoformat(),
                VERIFIED_AT.isoformat(),
            ),
        )


def build_chain(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    trading_repository = TradingRepository(
        database
    )

    evidence_repository = (
        OfficialIndexEvidenceRepository(
            database
        )
    )

    policy = CalendarVerificationPolicy()

    service = CalendarVerificationService(
        evidence_repository=(
            evidence_repository
        ),
        trading_repository=(
            trading_repository
        ),
        policy=policy,
    )

    resolver = CalendarTruthResolver(
        trading_repository
    )

    return (
        database,
        trading_repository,
        service,
        resolver,
    )


def test_complete_official_chain_promotes_truth(
    tmp_path,
):
    (
        database,
        trading_repository,
        service,
        resolver,
    ) = build_chain(tmp_path)

    for symbol in (
        "CASE30",
        "EGX70_EWI",
        "EGX100_EWI",
    ):
        insert_index_artifact(
            database,
            symbol,
        )

    decision = service.verify(
        DAY,
        verified_at=VERIFIED_AT,
    )

    assert (
        decision.status
        == MarketSessionStatus.VERIFIED
    )

    session = (
        trading_repository
        .get_market_session(DAY)
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.VERIFIED
    )
    assert (
        session.data_verified_at
        == VERIFIED_AT
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth
        .VERIFIED_TRADING_DAY
    )


def test_incomplete_evidence_stays_unverified(
    tmp_path,
):
    (
        database,
        trading_repository,
        service,
        resolver,
    ) = build_chain(tmp_path)

    for symbol in (
        "CASE30",
        "EGX70_EWI",
    ):
        insert_index_artifact(
            database,
            symbol,
        )

    decision = service.verify(
        DAY,
        verified_at=VERIFIED_AT,
    )

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert (
        trading_repository
        .get_market_session(DAY)
        is None
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth.UNVERIFIED
    )
