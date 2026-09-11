from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.research.historical_evidence import (
    HistoricalAttachmentReference,
    HistoricalAvailability,
    HistoricalAvailabilityEvidence,
    HistoricalEvidenceAttachment,
    HistoricalEvidencePackage,
    HistoricalRawReceipt,
    HistoricalSourceReview,
)
from app.us.contracts import (
    USSecurityType,
    USUniverseMember,
    USUniverseSnapshot,
)
from app.us.historical_universe import (
    US_UNIVERSE_EVIDENCE_FIELDS,
    AdmittedUSUniverseHistory,
    HistoricalUSUniverseFact,
    admit_us_universe_history,
    resolve_us_universe_on_date,
)


DECISION = datetime(
    2024, 7, 10, 21, 0,
    tzinfo=timezone.utc,
)

BUILD = datetime(
    2026, 9, 11, 10, 0,
    tzinfo=timezone.utc,
)


def _member(
    *,
    instrument_id=None,
    symbol="TEST",
    mic="XNAS",
    eligible=True,
):
    return USUniverseMember(
        instrument_id=instrument_id or uuid4(),
        canonical_symbol=symbol,
        listing_mic=mic,
        security_type=USSecurityType.COMMON_STOCK,
        eligible=eligible,
    )


def _snapshot(
    effective_date=date(2024, 7, 8),
    *,
    members=None,
):
    return USUniverseSnapshot(
        effective_date=effective_date,
        members=tuple(
            members
            if members is not None
            else (_member(),)
        ),
    )


def _package(
    *,
    available_at=datetime(
        2024, 7, 8, 20, 0,
        tzinfo=timezone.utc,
    ),
    received_at=datetime(
        2026, 9, 10, 10, 0,
        tzinfo=timezone.utc,
    ),
    reviewed_at=datetime(
        2026, 9, 10, 12, 0,
        tzinfo=timezone.utc,
    ),
    approved=True,
    covered_fields=None,
    sha_char="a",
):
    raw = HistoricalRawReceipt(
        provider="fixture-universe-provider",
        source="fixture US universe",
        source_locator=(
            f"fixture://us-universe/{sha_char}"
        ),
        sha256=sha_char * 64,
        byte_size=100,
        local_received_at=received_at,
        source_edition="fixture-edition-1",
        evidence_category="US_UNIVERSE",
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator=(
            f"fixture://us-universe-proof/{sha_char}"
        ),
        sha256="f" * 64,
        local_received_at=received_at,
        description="fixture availability proof",
        source_authority_context="fixture authority",
    )

    reference = HistoricalAttachmentReference(
        attachment_id=attachment.identity,
        sha256=attachment.sha256,
    )

    availability = HistoricalAvailability(
        kind="EXACT",
        exact_at=available_at,
    )

    evidence = HistoricalAvailabilityEvidence(
        subject_receipt_id=raw.identity,
        subject_sha256=raw.sha256,
        source_edition=raw.source_edition,
        covered_scope="complete exact-date US universe fixture",
        covered_fields=tuple(
            sorted(
                covered_fields
                if covered_fields is not None
                else US_UNIVERSE_EVIDENCE_FIELDS
            )
        ),
        revision_semantics="immutable fixture edition",
        attachments=(reference,),
        availability=availability,
    )

    review = HistoricalSourceReview(
        reviewer="fixture-reviewer",
        reviewed_at=reviewed_at,
        methodology="fixture review",
        approved=approved,
        subject_receipt_id=raw.identity,
        subject_sha256=raw.sha256,
        availability_evidence_id=evidence.identity,
        attachments=(reference,),
    )

    return HistoricalEvidencePackage(
        raw_receipt=raw,
        evidence=evidence,
        review=review,
        attachments=(attachment,),
    )


def _fact(
    *,
    snapshot=None,
    package=None,
):
    return HistoricalUSUniverseFact(
        snapshot=(
            snapshot
            if snapshot is not None
            else _snapshot()
        ),
        evidence_package=(
            package
            if package is not None
            else _package()
        ),
    )


def _admit(
    *facts,
    decision=DECISION,
    build=BUILD,
):
    return admit_us_universe_history(
        tuple(facts),
        decision_at=decision,
        research_built_at=build,
    )


def test_admits_one_complete_exact_date_snapshot():
    fact = _fact()

    history = _admit(fact)

    assert isinstance(
        history,
        AdmittedUSUniverseHistory,
    )
    assert history.facts == (fact,)


def test_empty_complete_universe_is_valid_negative_evidence():
    fact = _fact(
        snapshot=_snapshot(
            members=(),
        )
    )

    history = _admit(fact)

    snapshot = resolve_us_universe_on_date(
        history,
        effective_date=date(2024, 7, 8),
    )

    assert snapshot.complete is True
    assert snapshot.members == ()


def test_ineligible_members_are_preserved_not_filtered():
    member = _member(
        eligible=False,
    )

    history = _admit(
        _fact(
            snapshot=_snapshot(
                members=(member,),
            )
        )
    )

    snapshot = resolve_us_universe_on_date(
        history,
        effective_date=date(2024, 7, 8),
    )

    assert len(snapshot.members) == 1
    assert snapshot.members[0].eligible is False


def test_fact_requires_exact_snapshot_object():
    snapshot = _snapshot()

    with pytest.raises(
        ValidationError,
        match="exact USUniverseSnapshot required",
    ):
        HistoricalUSUniverseFact(
            snapshot=snapshot.model_dump(),
            evidence_package=_package(),
        )


def test_fact_requires_exact_evidence_package():
    package = _package()

    with pytest.raises(
        ValidationError,
        match="exact HistoricalEvidencePackage required",
    ):
        HistoricalUSUniverseFact(
            snapshot=_snapshot(),
            evidence_package=package.model_dump(),
        )


def test_evidence_must_cover_all_universe_fields():
    fields = set(
        US_UNIVERSE_EVIDENCE_FIELDS
    )
    fields.remove("members")

    with pytest.raises(
        ValidationError,
        match="members",
    ):
        _fact(
            package=_package(
                covered_fields=fields,
            )
        )


def test_extra_reviewed_fields_are_allowed():
    fields = (
        set(US_UNIVERSE_EVIDENCE_FIELDS)
        | {"index_family"}
    )

    history = _admit(
        _fact(
            package=_package(
                covered_fields=fields,
            )
        )
    )

    assert len(history.facts) == 1


def test_input_must_be_exact_tuple():
    with pytest.raises(
        ValueError,
        match="canonical universe fact tuple",
    ):
        admit_us_universe_history(
            [_fact()],
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_at_least_one_fact_is_required():
    with pytest.raises(
        ValueError,
        match="at least one historical US universe fact",
    ):
        _admit()


def test_items_must_be_exact_fact_type():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="exact HistoricalUSUniverseFact",
    ):
        admit_us_universe_history(
            (fact.model_dump(),),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_future_effective_snapshot_is_rejected():
    fact = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 11),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 8, 20, 0,
                tzinfo=timezone.utc,
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="future-effective US universe snapshot",
    ):
        _admit(fact)


def test_future_availability_is_rejected():
    package = _package(
        available_at=datetime(
            2024, 7, 11, 10, 0,
            tzinfo=timezone.utc,
        )
    )

    with pytest.raises(
        ValueError,
        match="historical availability not safely known",
    ):
        _admit(
            _fact(package=package)
        )


def test_receipt_after_build_is_rejected():
    package = _package(
        received_at=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        ),
        reviewed_at=datetime(
            2026, 9, 10, 12, 0,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(
        ValueError,
        match="raw receipt after research build",
    ):
        _admit(
            _fact(package=package)
        )


def test_review_after_build_is_rejected():
    package = _package(
        reviewed_at=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        )
    )

    with pytest.raises(
        ValueError,
        match="review after research build",
    ):
        _admit(
            _fact(package=package)
        )


def test_unapproved_review_is_rejected():
    package = _package(
        approved=False,
    )

    with pytest.raises(
        ValueError,
        match="approved review required",
    ):
        _admit(
            _fact(package=package)
        )


def test_decision_requires_exact_timezone_utc():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        ValueError,
        match="decision_at must use datetime.timezone.utc",
    ):
        _admit(
            _fact(),
            decision=datetime(
                2024, 7, 10, 21, 0,
                tzinfo=zero,
            ),
        )


def test_build_requires_exact_timezone_utc():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        ValueError,
        match="research_built_at must use",
    ):
        _admit(
            _fact(),
            build=datetime(
                2026, 9, 11, 10, 0,
                tzinfo=zero,
            ),
        )


def test_build_cannot_precede_decision():
    with pytest.raises(
        ValueError,
        match="cannot precede",
    ):
        _admit(
            _fact(),
            build=datetime(
                2024, 7, 1, 10, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_us_boundary_rejects_noncanonical_package_clock():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    package = _package(
        received_at=datetime(
            2026, 9, 10, 10, 0,
            tzinfo=zero,
        ),
        reviewed_at=datetime(
            2026, 9, 10, 12, 0,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(
        ValueError,
        match="raw_receipt.local_received_at",
    ):
        _admit(
            _fact(package=package)
        )


def test_exact_duplicate_fact_is_rejected():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="duplicate historical US universe fact",
    ):
        _admit(
            fact,
            fact,
        )


def test_two_different_snapshots_same_date_are_rejected():
    first = _fact(
        snapshot=_snapshot(
            members=(
                _member(symbol="AAA"),
            )
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        snapshot=_snapshot(
            members=(
                _member(symbol="BBB"),
            )
        ),
        package=_package(
            sha_char="b",
        ),
    )

    with pytest.raises(
        ValueError,
        match="multiple US universe snapshots",
    ):
        _admit(
            first,
            second,
        )


def test_same_instrument_can_change_symbol_across_exact_dates():
    instrument_id = uuid4()

    first = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 8),
            members=(
                _member(
                    instrument_id=instrument_id,
                    symbol="OLD",
                ),
            ),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 8, 20, 0,
                tzinfo=timezone.utc,
            ),
            sha_char="a",
        ),
    )

    second = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 9),
            members=(
                _member(
                    instrument_id=instrument_id,
                    symbol="NEW",
                ),
            ),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 9, 20, 0,
                tzinfo=timezone.utc,
            ),
            sha_char="b",
        ),
    )

    history = _admit(
        second,
        first,
    )

    assert (
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 8),
        )
        .members[0]
        .canonical_symbol
        == "OLD"
    )

    assert (
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 9),
        )
        .members[0]
        .canonical_symbol
        == "NEW"
    )


def test_symbol_can_be_reused_by_different_instruments_across_dates():
    first_id = uuid4()
    second_id = uuid4()

    first = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 8),
            members=(
                _member(
                    instrument_id=first_id,
                    symbol="REUSE",
                ),
            ),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 8, 20, 0,
                tzinfo=timezone.utc,
            ),
            sha_char="a",
        ),
    )

    second = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 9),
            members=(
                _member(
                    instrument_id=second_id,
                    symbol="REUSE",
                ),
            ),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 9, 20, 0,
                tzinfo=timezone.utc,
            ),
            sha_char="b",
        ),
    )

    history = _admit(
        first,
        second,
    )

    assert (
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 8),
        )
        .members[0]
        .instrument_id
        == first_id
    )

    assert (
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 9),
        )
        .members[0]
        .instrument_id
        == second_id
    )


def test_input_order_is_deterministic():
    first = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 8),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        snapshot=_snapshot(
            effective_date=date(2024, 7, 9),
        ),
        package=_package(
            available_at=datetime(
                2024, 7, 9, 20, 0,
                tzinfo=timezone.utc,
            ),
            sha_char="b",
        ),
    )

    a = _admit(first, second)
    b = _admit(second, first)

    assert a.facts == b.facts
    assert a.identity == b.identity


def test_research_build_is_excluded_from_identity():
    fact = _fact()

    first = _admit(
        fact,
        build=BUILD,
    )

    second = _admit(
        fact,
        build=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert first.identity == second.identity


def test_decision_is_part_of_identity():
    fact = _fact()

    first = _admit(
        fact,
        decision=DECISION,
    )

    later = datetime(
        2024, 7, 10, 21, 30,
        tzinfo=timezone.utc,
    )

    second = _admit(
        fact,
        decision=later,
    )

    assert first.identity != second.identity


def test_resolve_returns_exact_date_only():
    history = _admit(
        _fact()
    )

    snapshot = resolve_us_universe_on_date(
        history,
        effective_date=date(2024, 7, 8),
    )

    assert snapshot.effective_date == date(
        2024, 7, 8
    )


def test_missing_date_is_not_forward_filled():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="no exact-dated US universe snapshot",
    ):
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 9),
        )


def test_missing_date_is_not_back_filled():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="no exact-dated US universe snapshot",
    ):
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 7),
        )


def test_resolver_rejects_future_date():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="after admitted decision horizon",
    ):
        resolve_us_universe_on_date(
            history,
            effective_date=date(2024, 7, 11),
        )


def test_resolver_requires_exact_date():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="effective_date must be exact date",
    ):
        resolve_us_universe_on_date(
            history,
            effective_date=datetime(
                2024, 7, 8,
                tzinfo=timezone.utc,
            ),
        )
