import hashlib
import json

from datetime import (
    date,
    datetime,
    timezone,
)
from uuid import uuid4

import pytest

from app.data.egx_universe_publisher import (
    PUBLISHER_PROVIDER,
    publish_egx_universe,
)
from app.data.raw_store import (
    ImmutableRawStore,
)
from app.data.security_master import (
    CanonicalInstrument,
    InstrumentType,
)
from app.storage.database import (
    Database,
)
from app.storage.reference_repository import (
    ReferenceRepository,
)
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


DAY = date(
    2026,
    11,
    23,
)

RECEIVED = (
    "2026-11-23T19:00:00+00:00"
)

RECORDED = (
    "2026-11-23T19:00:01+00:00"
)

OBSERVED = (
    "2026-11-23T19:00:02+00:00"
)

ARTIFACT_SHA = "a" * 64


def instrument(
    *,
    ticker,
    symbol_code,
    name,
    kind=InstrumentType.EQUITY,
):
    return CanonicalInstrument(
        instrument_id=uuid4(),
        instrument_type=kind,
        canonical_ticker=ticker,
        name_en=name,
        name_ar=None,
        short_name_en=None,
        short_name_ar=None,
        source_provider="egid",
        source_symbol_code=(
            symbol_code
        ),
        reuters_raw=(
            f"{ticker}.CA"
            if kind
            is InstrumentType.EQUITY
            else None
        ),
        reuters_normalized=(
            f"{ticker}.CA"
            if kind
            is InstrumentType.EQUITY
            else None
        ),
        source_sha256=(
            ARTIFACT_SHA
        ),
        source_market_date=(
            DAY.isoformat()
        ),
        normalization_notes=[],
    )


def canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@pytest.fixture
def runtime(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    security_master = (
        SecurityMasterRepository(
            database
        )
    )

    references = (
        ReferenceRepository(
            database,
            ImmutableRawStore(
                tmp_path / "raw"
            ),
        )
    )

    return (
        database,
        security_master,
        references,
    )


def seed_admission(
    database,
    *,
    retain_observation=True,
):
    instruments = [
        instrument(
            ticker="COMI",
            symbol_code="EGS60121C018",
            name=(
                "Commercial International "
                "Bank-Egypt (CIB)"
            ),
        ),
        instrument(
            ticker="CCAPP",
            symbol_code="EGS73541P048",
            name=(
                "QALA For Financial "
                "Investments - Preferred Shares"
            ),
        ),
        instrument(
            ticker="KORA_R1",
            symbol_code="EGS924O1C014",
            name=(
                "Subscription Rights "
                "Of KORRA -1"
            ),
        ),
        instrument(
            ticker="EGX30",
            symbol_code="EGX30",
            name="EGX 30",
            kind=InstrumentType.INDEX,
        ),
    ]

    encoded = sorted(
        (
            item.model_dump(
                mode="json"
            )
            for item in instruments
        ),
        key=lambda item: (
            item["instrument_id"]
        ),
    )

    receipt = {
        "schema_version":
            "security-master-admission-v1",
        "provider":
            "egid",
        "snapshot_date":
            DAY.isoformat(),
        "artifact_sha256":
            ARTIFACT_SHA,
        "byte_size":
            100,
        "record_count":
            4,
        "instrument_count":
            4,
        "instruments":
            encoded,
        "policy":
            "EGID_EQUITY_NONEMPTY_RETAIN_90_PERCENT_V1",
        "live_money":
            False,
        "recorded_at":
            RECORDED,
        "received_at":
            RECEIVED,
        "previous_instrument_count":
            0,
    }

    receipt_json = canonical_json(
        receipt
    )

    receipt_sha = hashlib.sha256(
        receipt_json.encode()
    ).hexdigest()

    with database.connect() as con:
        con.execute(
            """
            INSERT INTO
            security_master_admissions
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "egid",
                DAY.isoformat(),
                ARTIFACT_SHA,
                receipt_json,
                receipt_sha,
            ),
        )

        if retain_observation:
            observation = {
                "schema_version":
                    "security-master-observation-v1",
                "observed_at":
                    OBSERVED,
                "receipt_json":
                    receipt_json,
                "receipt_sha256":
                    receipt_sha,
                "live_money":
                    False,
            }

            envelope_json = (
                canonical_json(
                    observation
                )
            )

            con.execute(
                """
                INSERT INTO
                security_master_observations
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "egid",
                    DAY.isoformat(),
                    ARTIFACT_SHA,
                    envelope_json,
                    hashlib.sha256(
                        envelope_json.encode()
                    ).hexdigest(),
                ),
            )

    return receipt


def test_publish_requires_retained_observation(
    runtime,
):
    database, security_master, references = (
        runtime
    )

    seed_admission(
        database,
        retain_observation=False,
    )

    with pytest.raises(
        KeyError,
        match="retained security master observation",
    ):
        publish_egx_universe(
            security_master=security_master,
            references=references,
            snapshot_date=DAY,
            artifact_sha256=ARTIFACT_SHA,
        )


def test_publish_exact_candidate_without_review(
    runtime,
):
    database, security_master, references = (
        runtime
    )

    seed_admission(
        database
    )

    result = publish_egx_universe(
        security_master=security_master,
        references=references,
        snapshot_date=DAY,
        artifact_sha256=ARTIFACT_SHA,
    )

    assert result.member_count == 3
    assert result.eligible_count == 1
    assert result.ineligible_count == 2
    assert result.live_money is False

    with database.connect() as con:
        assert con.execute(
            """
            SELECT COUNT(*)
            FROM reference_artifacts
            WHERE contract='egx-universe-v1'
              AND effective_date=?
            """,
            (DAY.isoformat(),),
        ).fetchone()[0] == 1

        assert con.execute(
            """
            SELECT COUNT(*)
            FROM reference_artifacts
            WHERE contract='egx-source-review-v1'
            """
        ).fetchone()[0] == 0

        provider = con.execute(
            """
            SELECT provider
            FROM data_ingestions
            WHERE ingestion_id=?
            """,
            (
                result.ingestion_id,
            ),
        ).fetchone()[0]

    assert (
        provider
        == PUBLISHER_PROVIDER
    )

    # Publication alone must not promote itself.
    with pytest.raises(
        ValueError,
        match=(
            "validation evidence unavailable"
        ),
    ):
        references.universe(
            market_date=DAY,
            as_of=datetime.now(
                timezone.utc
            ),
        )


def test_exact_retry_is_byte_idempotent(
    runtime,
    tmp_path,
):
    database, security_master, references = (
        runtime
    )

    seed_admission(
        database
    )

    first = publish_egx_universe(
        security_master=security_master,
        references=references,
        snapshot_date=DAY,
        artifact_sha256=ARTIFACT_SHA,
    )

    # Compare logical database state, not SQLite/WAL container bytes.
    with database.connect() as con:
        before_database = tuple(con.iterdump())
    before_raw = {
        path.relative_to(tmp_path / "raw"): path.read_bytes()
        for path in (tmp_path / "raw").rglob("*")
        if path.is_file()
    }
    assert before_raw
    assert any(
        hashlib.sha256(payload).hexdigest() == first.universe_sha256
        for payload in before_raw.values()
    )

    second = publish_egx_universe(
        security_master=security_master,
        references=references,
        snapshot_date=DAY,
        artifact_sha256=ARTIFACT_SHA,
    )

    with database.connect() as con:
        assert tuple(con.iterdump()) == before_database
        assert con.execute(
            "SELECT COUNT(*) FROM reference_artifacts "
            "WHERE contract='egx-source-review-v1'"
        ).fetchone()[0] == 0
    assert {
        path.relative_to(tmp_path / "raw"): path.read_bytes()
        for path in (tmp_path / "raw").rglob("*")
        if path.is_file()
    } == before_raw

    assert first == second

    with database.connect() as con:
        assert con.execute(
            """
            SELECT COUNT(*)
            FROM reference_artifacts
            WHERE contract='egx-universe-v1'
              AND effective_date=?
            """,
            (DAY.isoformat(),),
        ).fetchone()[0] == 1


def test_wrong_sha_never_falls_back_to_latest(
    runtime,
):
    database, security_master, references = (
        runtime
    )

    seed_admission(
        database
    )

    with pytest.raises(
        KeyError,
        match=(
            "historical security master "
            "admission unavailable"
        ),
    ):
        publish_egx_universe(
            security_master=security_master,
            references=references,
            snapshot_date=DAY,
            artifact_sha256=(
                "b" * 64
            ),
        )


def test_bad_sha_format_fails_before_lookup(
    runtime,
):
    _, security_master, references = (
        runtime
    )

    with pytest.raises(
        ValueError,
        match="lowercase sha256",
    ):
        publish_egx_universe(
            security_master=security_master,
            references=references,
            snapshot_date=DAY,
            artifact_sha256="ABC",
        )


@pytest.mark.parametrize("mismatch", ["subject_sha256", "subject_provider", "subject_ingestion_id"])
def test_published_universe_requires_separate_exact_review(runtime, mismatch):
    """Fixture lifecycle evidence only; this does not approve a real source."""
    database, security_master, references = runtime
    seed_admission(database)
    publication = publish_egx_universe(
        security_master=security_master,
        references=references,
        snapshot_date=DAY,
        artifact_sha256=ARTIFACT_SHA,
    )
    before_review = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="validation evidence unavailable"):
        references.universe(market_date=DAY, as_of=before_review)

    review = dict(
        contract="egx-source-review-v1",
        subject_ingestion_id=publication.ingestion_id,
        subject_sha256=publication.universe_sha256,
        subject_provider=PUBLISHER_PROVIDER,
        subject_contract="egx-universe-v1",
        reviewed_at=before_review.isoformat(),
        reviewer="independent-fixture-reviewer",
        evidence_uri="fixture://not-authoritative-source-evidence",
        methodology="Synthetic binding check only; exchange membership not established",
        approved=True,
    )
    wrong = dict(review)
    wrong[mismatch] = {
        "subject_sha256": "b" * 64,
        "subject_provider": "different-provider",
        "subject_ingestion_id": str(uuid4()),
    }[mismatch]
    references.ingest(
        provider="fixture-reviewer", payload=canonical_json(wrong).encode(),
        source_uri="fixture://mismatched-review", contract=review["contract"],
    )
    with pytest.raises(ValueError, match="validation evidence unavailable"):
        references.universe(market_date=DAY, as_of=datetime.now(timezone.utc))

    # Review is a separate ingestion, never a publisher side effect.
    valid = references.ingest(
        provider="fixture-reviewer", payload=canonical_json(review).encode(),
        source_uri="fixture://separate-review", contract=review["contract"],
    )
    # Later receipt must not retroactively authorize the earlier PIT query.
    with pytest.raises(ValueError, match="validation evidence unavailable"):
        references.universe(market_date=DAY, as_of=before_review)
    row, document, review_ids = references.universe(
        market_date=DAY, as_of=datetime.now(timezone.utc),
    )
    assert row["ingestion_id"] == publication.ingestion_id
    assert review_ids == (str(valid.ingestion_id),)
    assert str(valid.ingestion_id) != publication.ingestion_id
    assert len(document.members) == publication.member_count
    assert publication.live_money is False
