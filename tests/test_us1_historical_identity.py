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
    USListingIdentity,
    USSecurityType,
)
from app.us.historical_identity import (
    US_LISTING_EVIDENCE_FIELDS,
    AdmittedUSListingHistory,
    HistoricalUSListingFact,
    admit_us_listing_history,
    resolve_us_listing_on_date,
)


DECISION = datetime(
    2024, 6, 15, 18, 0,
    tzinfo=timezone.utc,
)
BUILD = datetime(
    2026, 9, 11, 10, 0,
    tzinfo=timezone.utc,
)


def _listing(
    *,
    instrument_id=None,
    effective_date=date(2024, 6, 14),
    symbol="TEST",
    mic="XNAS",
    provider="fixture-provider",
    provider_key="provider-id-1",
):
    return USListingIdentity(
        effective_date=effective_date,
        instrument_id=instrument_id or uuid4(),
        canonical_symbol=symbol,
        listing_mic=mic,
        security_type=USSecurityType.COMMON_STOCK,
        provider_symbol=symbol,
        source_provider=provider,
        source_instrument_key=provider_key,
        is_primary_listing=True,
    )


def _package(
    *,
    provider="fixture-provider",
    available_at=datetime(
        2024, 6, 14, 20, 0,
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
        provider=provider,
        source="fixture listing history",
        source_locator="fixture://listing-history",
        sha256=sha_char * 64,
        byte_size=100,
        local_received_at=received_at,
        source_edition="fixture-edition-1",
        evidence_category="US_LISTING_IDENTITY",
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator="fixture://availability-proof",
        sha256="b" * 64,
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
        covered_scope="exact US listing identity fixture",
        covered_fields=tuple(
            covered_fields
            if covered_fields is not None
            else sorted(
                US_LISTING_EVIDENCE_FIELDS
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
    listing=None,
    package=None,
):
    return HistoricalUSListingFact(
        listing=listing or _listing(),
        evidence_package=package or _package(),
    )


def _admit(*facts, decision=DECISION, build=BUILD):
    return admit_us_listing_history(
        tuple(facts),
        decision_at=decision,
        research_built_at=build,
    )


def test_admits_one_exact_dated_listing_fact():
    fact = _fact()

    history = _admit(fact)

    assert isinstance(
        history,
        AdmittedUSListingHistory,
    )
    assert history.facts == (fact,)


def test_fact_requires_exact_listing_object():
    listing = _listing()

    with pytest.raises(
        ValidationError,
        match="exact USListingIdentity required",
    ):
        HistoricalUSListingFact(
            listing=listing.model_dump(),
            evidence_package=_package(),
        )


def test_fact_requires_exact_evidence_package_object():
    package = _package()

    with pytest.raises(
        ValidationError,
        match="exact HistoricalEvidencePackage required",
    ):
        HistoricalUSListingFact(
            listing=_listing(),
            evidence_package=package.model_dump(),
        )


def test_source_provider_must_match_receipt():
    with pytest.raises(
        ValidationError,
        match="source_provider does not match",
    ):
        _fact(
            listing=_listing(
                provider="provider-a",
            ),
            package=_package(
                provider="provider-b",
            ),
        )


def test_evidence_must_cover_all_listing_fields():
    fields = set(
        US_LISTING_EVIDENCE_FIELDS
    )
    fields.remove("listing_mic")

    with pytest.raises(
        ValidationError,
        match="listing_mic",
    ):
        _fact(
            package=_package(
                covered_fields=sorted(fields),
            )
        )


def test_extra_reviewed_fields_are_allowed():
    package = _package(
        covered_fields=sorted(
            set(US_LISTING_EVIDENCE_FIELDS)
            | {"issuer_name"}
        )
    )

    history = _admit(
        _fact(package=package)
    )

    assert len(history.facts) == 1


def test_input_must_be_exact_tuple():
    with pytest.raises(
        ValueError,
        match="canonical listing fact tuple",
    ):
        admit_us_listing_history(
            [_fact()],
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_items_must_be_exact_fact_type():
    with pytest.raises(
        ValueError,
        match="exact HistoricalUSListingFact",
    ):
        admit_us_listing_history(
            (_fact().model_dump(),),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_decision_clock_requires_exact_timezone_utc():
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
                2024, 6, 15, 18, 0,
                tzinfo=zero,
            ),
        )


def test_build_clock_requires_exact_timezone_utc():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        ValueError,
        match="research_built_at must use datetime.timezone.utc",
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
                2024, 6, 1, 10, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_future_historical_availability_is_rejected():
    package = _package(
        available_at=datetime(
            2024, 6, 16, 10, 0,
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


def test_us_boundary_rejects_noncanonical_zero_offset_package_clock():
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
        match="raw_receipt.local_received_at "
        "must use datetime.timezone.utc",
    ):
        _admit(
            _fact(package=package)
        )


def test_future_effective_listing_is_rejected_even_if_known_early():
    listing = _listing(
        effective_date=date(
            2024, 6, 17
        )
    )

    with pytest.raises(
        ValueError,
        match="future-effective",
    ):
        _admit(
            _fact(listing=listing)
        )


def test_same_instrument_can_change_symbol_on_different_exact_dates():
    instrument_id = uuid4()

    first = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=date(
                2024, 6, 13
            ),
            symbol="OLD",
            provider_key="key-1",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=date(
                2024, 6, 14
            ),
            symbol="NEW",
            provider_key="key-1",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    history = _admit(
        second,
        first,
    )

    assert [
        item.listing.canonical_symbol
        for item in history.facts
    ] == ["OLD", "NEW"]


def test_ticker_reuse_on_different_dates_by_different_instruments_is_allowed():
    first_id = uuid4()
    second_id = uuid4()

    first = _fact(
        listing=_listing(
            instrument_id=first_id,
            effective_date=date(
                2024, 6, 13
            ),
            symbol="REUSE",
            provider_key="old-key",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            instrument_id=second_id,
            effective_date=date(
                2024, 6, 14
            ),
            symbol="REUSE",
            provider_key="new-key",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    history = _admit(
        first,
        second,
    )

    assert len(history.facts) == 2


def test_same_dated_listing_key_cannot_map_to_two_instruments():
    market_date = date(2024, 6, 14)

    first = _fact(
        listing=_listing(
            instrument_id=uuid4(),
            effective_date=market_date,
            symbol="DUP",
            provider_key="key-a",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            instrument_id=uuid4(),
            effective_date=market_date,
            symbol="DUP",
            provider_key="key-b",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    with pytest.raises(
        ValueError,
        match="ambiguous dated US listing key",
    ):
        _admit(
            first,
            second,
        )


def test_same_instrument_same_date_cannot_have_multiple_identity_facts():
    instrument_id = uuid4()
    market_date = date(2024, 6, 14)

    first = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=market_date,
            symbol="ONE",
            provider_key="key-1",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=market_date,
            symbol="TWO",
            provider_key="key-2",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    with pytest.raises(
        ValueError,
        match="multiple identity facts",
    ):
        _admit(
            first,
            second,
        )


def test_provider_key_same_date_cannot_map_to_multiple_instruments():
    market_date = date(2024, 6, 14)

    first = _fact(
        listing=_listing(
            instrument_id=uuid4(),
            effective_date=market_date,
            symbol="ONE",
            provider_key="same-provider-key",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            instrument_id=uuid4(),
            effective_date=market_date,
            symbol="TWO",
            provider_key="same-provider-key",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    with pytest.raises(
        ValueError,
        match="provider instrument key maps",
    ):
        _admit(
            first,
            second,
        )


def test_exact_duplicate_fact_is_rejected():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="duplicate historical US listing fact",
    ):
        _admit(
            fact,
            fact,
        )


def test_admitted_order_is_deterministic():
    first = _fact(
        listing=_listing(
            effective_date=date(
                2024, 6, 13
            ),
            symbol="AAA",
            provider_key="key-a",
        ),
        package=_package(
            sha_char="c",
        ),
    )

    second = _fact(
        listing=_listing(
            effective_date=date(
                2024, 6, 14
            ),
            symbol="BBB",
            provider_key="key-b",
        ),
        package=_package(
            sha_char="d",
        ),
    )

    left = _admit(
        first,
        second,
    )
    right = _admit(
        second,
        first,
    )

    assert left.facts == right.facts
    assert left.identity == right.identity


def test_research_build_clock_is_not_part_of_semantic_history_identity():
    fact = _fact()

    first = _admit(
        fact,
        build=BUILD,
    )
    later = _admit(
        fact,
        build=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert (
        first.identity
        == later.identity
    )


def test_resolve_requires_exact_market_date_not_latest_wins():
    instrument_id = uuid4()

    fact = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=date(
                2024, 6, 13
            ),
            symbol="OLD",
        )
    )

    history = _admit(fact)

    with pytest.raises(
        ValueError,
        match="no exact-dated",
    ):
        resolve_us_listing_on_date(
            history,
            instrument_id=instrument_id,
            market_date=date(
                2024, 6, 14
            ),
        )


def test_resolve_returns_exact_dated_identity():
    instrument_id = uuid4()

    fact = _fact(
        listing=_listing(
            instrument_id=instrument_id,
            effective_date=date(
                2024, 6, 14
            ),
            symbol="TEST",
        )
    )

    history = _admit(fact)

    resolved = resolve_us_listing_on_date(
        history,
        instrument_id=instrument_id,
        market_date=date(
            2024, 6, 14
        ),
    )

    assert (
        resolved.instrument_id
        == instrument_id
    )
    assert (
        resolved.canonical_symbol
        == "TEST"
    )


def test_resolve_rejects_date_after_decision_horizon():
    instrument_id = uuid4()

    history = _admit(
        _fact(
            listing=_listing(
                instrument_id=instrument_id,
            )
        )
    )

    with pytest.raises(
        ValueError,
        match="after admitted decision horizon",
    ):
        resolve_us_listing_on_date(
            history,
            instrument_id=instrument_id,
            market_date=date(
                2024, 6, 16
            ),
        )


def test_empty_history_is_valid_but_cannot_resolve_identity():
    history = _admit()

    assert history.facts == ()

    with pytest.raises(
        ValueError,
        match="no exact-dated",
    ):
        resolve_us_listing_on_date(
            history,
            instrument_id=uuid4(),
            market_date=date(
                2024, 6, 14
            ),
        )
