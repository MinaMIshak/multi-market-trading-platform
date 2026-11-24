"""Synthetic fixtures only. No provider transport or production database."""

import json

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.data.egx_universe_changes import universe_sha256
from app.data.egx_universe_reviewed_changes import (
    EGXReviewedUniverseChanges,
    load_reviewed_egx_universe_changes,
)
from app.data.raw_store import ImmutableRawStore
from app.data.reference import UniverseEvidence
from app.storage.database import Database
from app.storage.reference_repository import ReferenceRepository


PREVIOUS_DAY = date(2020, 1, 1)
CURRENT_DAY = date(2020, 1, 2)
PUBLISHED = "2020-01-02T15:00:00+00:00"

COMI = "00000000-0000-0000-0000-000000000001"
HRHO = "00000000-0000-0000-0000-000000000002"
OLDX = "00000000-0000-0000-0000-000000000003"
NEWX = "00000000-0000-0000-0000-000000000004"


@pytest.fixture
def repo(tmp_path):
    database = Database(tmp_path / "isolated.db")
    database.initialize()
    return ReferenceRepository(database, ImmutableRawStore(tmp_path / "raw"))


def cutoff():
    return datetime.now(timezone.utc) + timedelta(seconds=1)


def universe_doc(day, members):
    return dict(
        contract="egx-universe-v1",
        market="EGX",
        effective_date=str(day),
        published_at=PUBLISHED,
        complete=True,
        members=[
            dict(instrument_id=i, symbol=s, eligible=e) for i, s, e in members
        ],
    )


def canonical_bytes(doc):
    return json.dumps(
        UniverseEvidence.model_validate(doc).model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def ingest(repo, doc, *, canonical=False, provider="synthetic"):
    payload = canonical_bytes(doc) if canonical else json.dumps(doc).encode()
    return repo.ingest(
        provider=provider,
        payload=payload,
        source_uri="fixture://synthetic",
        contract=doc["contract"],
    )


def review(repo, manifest):
    return ingest(
        repo,
        dict(
            contract="egx-source-review-v1",
            subject_ingestion_id=str(manifest.ingestion_id),
            subject_sha256=manifest.sha256,
            subject_provider=manifest.provider,
            subject_contract="egx-universe-v1",
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            reviewer="fixture-reviewer",
            evidence_uri="fixture://validation",
            methodology="synthetic fixture comparison",
            approved=True,
        ),
    )


PREVIOUS_MEMBERS = [
    (COMI, "COMI", True),
    (HRHO, "HRHO", True),
    (OLDX, "OLDX", True),
]

CURRENT_MEMBERS = [
    (COMI, "COMI", True),
    (HRHO, "HRHE", True),
    (NEWX, "NEWX", False),
]


def seed(repo, *, review_previous=True, review_current=True):
    previous = ingest(
        repo, universe_doc(PREVIOUS_DAY, PREVIOUS_MEMBERS), canonical=True
    )
    current = ingest(repo, universe_doc(CURRENT_DAY, CURRENT_MEMBERS))
    previous_review = review(repo, previous) if review_previous else None
    current_review = review(repo, current) if review_current else None
    return previous, current, previous_review, current_review


def test_reviewed_selections_and_changes(repo):
    previous, current, previous_review, current_review = seed(repo)

    result = load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )

    assert result.previous.ingestion_id == previous.ingestion_id
    assert result.current.ingestion_id == current.ingestion_id
    assert result.previous.retained_sha256 == previous.sha256
    assert result.current.retained_sha256 == current.sha256
    assert result.previous.provider == "synthetic"
    assert result.previous.review_ingestion_ids == (
        previous_review.ingestion_id,
    )
    assert result.current.review_ingestion_ids == (
        current_review.ingestion_id,
    )

    changes = result.changes
    assert [str(c.instrument_id) for c in changes.added] == [NEWX]
    assert [str(c.instrument_id) for c in changes.absent] == [OLDX]
    assert [
        (str(c.instrument_id), c.previous_symbol, c.current_symbol)
        for c in changes.symbol_changed
    ] == [(HRHO, "HRHO", "HRHE")]
    assert changes.unchanged_count == 1


def test_canonical_byte_flag_is_truthful(repo):
    seed(repo)

    result = load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )

    # Previous was retained as publisher-canonical bytes, current was not.
    assert result.previous.retained_bytes_canonical is True
    assert (
        result.previous.retained_sha256
        == result.changes.previous_universe_sha256
        == universe_sha256(
            UniverseEvidence.model_validate(
                universe_doc(PREVIOUS_DAY, PREVIOUS_MEMBERS)
            )
        )
    )
    assert result.current.retained_bytes_canonical is False
    assert (
        result.current.retained_sha256
        != result.changes.current_universe_sha256
    )


def test_fixed_labels(repo):
    seed(repo)

    result = load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )

    assert result.contract_version == "egx-reviewed-universe-changes-v1"
    assert result.selection_rule == "EXACT_DATED_REVIEWED_AT_AS_OF_V1"
    assert result.source_review == "REVIEWED_AT_AS_OF"
    assert result.listing_interpretation == "NOT_ESTABLISHED"
    assert result.delisting_interpretation == "NOT_ESTABLISHED"
    assert result.symbol_change_interpretation == "NOT_ESTABLISHED"
    assert result.intermediate_dates == "NOT_OBSERVED"
    assert result.trading_effect == "NONE"
    assert result.mode == "SHADOW"
    assert result.live_money is False
    # The pure comparison itself never sees reviews.
    assert result.changes.source_review == "NOT_CHECKED"


@pytest.mark.parametrize(
    "review_previous, review_current",
    [(False, True), (True, False)],
)
def test_unreviewed_universe_fails_closed(
    repo, review_previous, review_current
):
    seed(
        repo,
        review_previous=review_previous,
        review_current=review_current,
    )

    with pytest.raises(ValueError, match="validation evidence unavailable"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=PREVIOUS_DAY,
            current_date=CURRENT_DAY,
            as_of=cutoff(),
        )


def test_missing_dated_universe_fails_closed(repo):
    seed(repo)

    with pytest.raises(ValueError, match="exactly one dated universe"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=date(2019, 12, 31),
            current_date=CURRENT_DAY,
            as_of=cutoff(),
        )


def test_evidence_received_after_as_of_is_invisible(repo):
    seed(repo)

    # Both universes and reviews were received now, long after this as_of.
    with pytest.raises(ValueError, match="exactly one dated universe"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=PREVIOUS_DAY,
            current_date=CURRENT_DAY,
            as_of=datetime(2020, 1, 3, tzinfo=timezone.utc),
        )


def test_future_market_date_fails_closed(repo):
    seed(repo)

    with pytest.raises(ValueError, match="future universe date"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=PREVIOUS_DAY,
            current_date=CURRENT_DAY,
            as_of=datetime(2020, 1, 1, 12, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "previous_date, current_date",
    [(CURRENT_DAY, CURRENT_DAY), (CURRENT_DAY, PREVIOUS_DAY)],
)
def test_non_increasing_dates_fail_before_reading(
    repo, previous_date, current_date
):
    with pytest.raises(ValueError, match="must precede"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=previous_date,
            current_date=current_date,
            as_of=cutoff(),
        )


def test_input_types_are_exact(repo):
    with pytest.raises(TypeError, match="ReferenceRepository"):
        load_reviewed_egx_universe_changes(
            object(),
            previous_date=PREVIOUS_DAY,
            current_date=CURRENT_DAY,
            as_of=cutoff(),
        )
    with pytest.raises(TypeError, match="exact market dates"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            current_date=CURRENT_DAY,
            as_of=cutoff(),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        load_reviewed_egx_universe_changes(
            repo,
            previous_date=PREVIOUS_DAY,
            current_date=CURRENT_DAY,
            as_of=datetime(2030, 1, 1),
        )


def test_result_contract_rejects_mismatched_selection(repo):
    seed(repo)
    result = load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )

    data = result.model_dump()
    data["previous"]["effective_date"] = "2019-12-31"
    with pytest.raises(ValueError, match="selection dates"):
        EGXReviewedUniverseChanges.model_validate(data)

    data = result.model_dump()
    data["current"]["retained_bytes_canonical"] = True
    with pytest.raises(ValueError, match="canonical byte flag"):
        EGXReviewedUniverseChanges.model_validate(data)

    data = result.model_dump()
    data["previous"]["review_ingestion_ids"] = (
        data["previous"]["ingestion_id"],
    )
    with pytest.raises(ValueError, match="cannot review itself"):
        EGXReviewedUniverseChanges.model_validate(data)


def test_no_evidence_is_written_on_success(repo):
    seed(repo)

    with repo.database.connect() as con:
        before = con.execute(
            "SELECT COUNT(*) FROM reference_artifacts"
        ).fetchone()[0]
        ingestions_before = con.execute(
            "SELECT COUNT(*) FROM data_ingestions"
        ).fetchone()[0]

    load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )

    with repo.database.connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM reference_artifacts"
        ).fetchone()[0] == before
        assert con.execute(
            "SELECT COUNT(*) FROM data_ingestions"
        ).fetchone()[0] == ingestions_before


def test_review_ids_are_uuids(repo):
    seed(repo)
    result = load_reviewed_egx_universe_changes(
        repo,
        previous_date=PREVIOUS_DAY,
        current_date=CURRENT_DAY,
        as_of=cutoff(),
    )
    assert all(
        type(item) is UUID
        for item in result.previous.review_ingestion_ids
        + result.current.review_ingestion_ids
    )
