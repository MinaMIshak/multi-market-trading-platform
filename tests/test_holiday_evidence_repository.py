from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

import pytest

from app.storage import Database
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceConflictError,
    HolidayEvidenceRepository,
)


DAY = date(2026, 7, 2)
NOMINAL = date(2026, 6, 30)
HASH = "a" * 64
URI = "https://example.test/egx/holiday"
NOW = datetime(
    2026,
    7,
    1,
    10,
    0,
    tzinfo=timezone.utc,
)


def build(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    return (
        database,
        HolidayEvidenceRepository(
            database
        ),
    )


def save(
    repository,
    *,
    closed=True,
    received=NOW,
    metadata=None,
):
    return repository.save_evidence(
        authority="egx_official",
        observed_date=DAY,
        nominal_date=NOMINAL,
        market_closed=closed,
        source_uri=URI,
        source_published_at=NOW,
        content_hash=HASH,
        received_at=received,
        metadata=(
            metadata
            if metadata is not None
            else {"document": "closure_notice"}
        ),
    )


def test_persists_full_provenance(
    tmp_path,
):
    _, repository = build(tmp_path)

    assert save(repository) is True

    rows = repository.list_for_observed_date(
        DAY
    )

    assert len(rows) == 1

    row = rows[0]

    assert row.authority == "egx_official"
    assert row.observed_date == DAY
    assert row.nominal_date == NOMINAL
    assert row.market_closed is True
    assert row.source_uri == URI
    assert row.source_published_at == NOW
    assert row.content_hash == HASH
    assert row.received_at == NOW
    assert row.metadata == {
        "document": "closure_notice"
    }


def test_exact_replay_is_idempotent(
    tmp_path,
):
    database, repository = build(tmp_path)

    assert save(repository) is True

    assert save(
        repository,
        received=(
            NOW + timedelta(hours=1)
        ),
    ) is False

    with database.connect() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM holiday_evidence
            """
        ).fetchone()["count"]

    assert count == 1

    row = repository.list_for_observed_date(
        DAY
    )[0]

    assert row.received_at == NOW


def test_conflicting_interpretation_fails_closed(
    tmp_path,
):
    _, repository = build(tmp_path)

    assert save(repository) is True

    with pytest.raises(
        HolidayEvidenceConflictError
    ):
        save(
            repository,
            closed=False,
        )

    row = repository.list_for_observed_date(
        DAY
    )[0]

    assert row.market_closed is True


def test_conflicting_metadata_fails_closed(
    tmp_path,
):
    _, repository = build(tmp_path)

    assert save(repository) is True

    with pytest.raises(
        HolidayEvidenceConflictError
    ):
        save(
            repository,
            metadata={"document": "changed"},
        )


def test_target_date_query_is_scoped(
    tmp_path,
):
    _, repository = build(tmp_path)

    assert save(repository) is True

    repository.save_evidence(
        authority="egypt_government",
        observed_date=date(2026, 7, 3),
        market_closed=True,
        source_uri="https://example.test/gov",
        content_hash="b" * 64,
        received_at=NOW,
    )

    assert len(
        repository.list_for_observed_date(
            DAY
        )
    ) == 1

    assert (
        repository.list_for_observed_date(
            date(2026, 7, 4)
        )
        == []
    )


def test_naive_received_at_rejected(
    tmp_path,
):
    _, repository = build(tmp_path)

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        save(
            repository,
            received=datetime(
                2026,
                7,
                1,
                10,
                0,
            ),
        )


def test_invalid_hash_rejected(
    tmp_path,
):
    _, repository = build(tmp_path)

    with pytest.raises(
        ValueError,
        match="SHA-256",
    ):
        repository.save_evidence(
            authority="egx_official",
            observed_date=DAY,
            market_closed=True,
            source_uri=URI,
            content_hash="not-a-hash",
            received_at=NOW,
        )


def test_non_boolean_market_closed_rejected(
    tmp_path,
):
    _, repository = build(tmp_path)

    with pytest.raises(
        ValueError,
        match="must be bool",
    ):
        repository.save_evidence(
            authority="egx_official",
            observed_date=DAY,
            market_closed=1,
            source_uri=URI,
            content_hash=HASH,
            received_at=NOW,
        )
