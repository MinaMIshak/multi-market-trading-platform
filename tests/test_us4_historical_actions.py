from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
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
    USCorporateActionCoverage,
    USCorporateActionEvent,
    USCorporateActionType,
)
from app.us.historical_actions import (
    US_ACTION_COVERAGE_EVIDENCE_FIELDS,
    AdmittedUSCorporateActionHistory,
    HistoricalUSCorporateActionFact,
    admit_us_corporate_action_history,
    resolve_us_corporate_actions_on_date,
)


INSTRUMENT = uuid4()

DECISION = datetime(
    2024, 7, 11, 21, 0,
    tzinfo=timezone.utc,
)

BUILD = datetime(
    2026, 9, 11, 10, 0,
    tzinfo=timezone.utc,
)


def _package(
    *,
    available_at=datetime(
        2024, 7, 10, 20, 0,
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
        provider="fixture-action-provider",
        source="fixture corporate actions",
        source_locator=(
            f"fixture://us-actions/{sha_char}"
        ),
        sha256=sha_char * 64,
        byte_size=100,
        local_received_at=received_at,
        source_edition="fixture-edition-1",
        evidence_category="US_CORPORATE_ACTIONS",
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator=(
            f"fixture://us-actions-proof/{sha_char}"
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
        covered_scope=(
            "complete bounded US corporate-action coverage"
        ),
        covered_fields=tuple(
            sorted(
                covered_fields
                if covered_fields is not None
                else US_ACTION_COVERAGE_EVIDENCE_FIELDS
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


def _split(
    *,
    event_id="split-1",
    effective_date=date(2024, 7, 5),
):
    return USCorporateActionEvent(
        event_id=event_id,
        effective_date=effective_date,
        action_type=USCorporateActionType.SPLIT,
        new_shares=Decimal("4"),
        old_shares=Decimal("1"),
        details="fixture 4-for-1 split",
    )


def _dividend(
    *,
    event_id="dividend-1",
    effective_date=date(2024, 7, 8),
):
    return USCorporateActionEvent(
        event_id=event_id,
        effective_date=effective_date,
        action_type=USCorporateActionType.CASH_DIVIDEND,
        cash_amount=Decimal("0.25"),
        cash_currency="USD",
        details="fixture cash dividend",
    )


def _symbol_change(
    *,
    event_id="symbol-1",
    effective_date=date(2024, 7, 9),
):
    return USCorporateActionEvent(
        event_id=event_id,
        effective_date=effective_date,
        action_type=USCorporateActionType.SYMBOL_CHANGE,
        old_symbol="OLD",
        new_symbol="NEW",
        details="fixture ticker change",
    )


def _delisting(
    *,
    event_id="delist-1",
    effective_date=date(2024, 7, 10),
):
    return USCorporateActionEvent(
        event_id=event_id,
        effective_date=effective_date,
        action_type=USCorporateActionType.DELISTING,
        details="fixture delisting",
    )


def _coverage(
    *,
    start=date(2024, 7, 1),
    end=date(2024, 7, 10),
    instrument_id=INSTRUMENT,
    actions=(),
):
    return USCorporateActionCoverage(
        instrument_id=instrument_id,
        coverage_start=start,
        coverage_end=end,
        actions=tuple(actions),
    )


def _fact(
    *,
    coverage=None,
    package=None,
):
    return HistoricalUSCorporateActionFact(
        coverage=(
            coverage
            if coverage is not None
            else _coverage()
        ),
        evidence_package=(
            package
            if package is not None
            else _package()
        ),
    )


def _admit(
    *facts,
    instrument_id=INSTRUMENT,
    start=date(2024, 7, 1),
    end=date(2024, 7, 10),
    decision=DECISION,
    build=BUILD,
):
    return admit_us_corporate_action_history(
        tuple(facts),
        instrument_id=instrument_id,
        coverage_start=start,
        coverage_end=end,
        decision_at=decision,
        research_built_at=build,
    )


def test_empty_complete_coverage_is_valid_negative_evidence():
    history = _admit(
        _fact(
            coverage=_coverage(actions=()),
        )
    )

    assert isinstance(
        history,
        AdmittedUSCorporateActionHistory,
    )

    assert (
        resolve_us_corporate_actions_on_date(
            history,
            market_date=date(2024, 7, 5),
        )
        == ()
    )


def test_split_action_is_admitted_without_transforming_terms():
    split = _split()

    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(split,),
            )
        )
    )

    actions = resolve_us_corporate_actions_on_date(
        history,
        market_date=date(2024, 7, 5),
    )

    assert len(actions) == 1
    assert actions[0].new_shares == Decimal("4")
    assert actions[0].old_shares == Decimal("1")


def test_cash_dividend_terms_are_preserved():
    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(_dividend(),),
            )
        )
    )

    action = resolve_us_corporate_actions_on_date(
        history,
        market_date=date(2024, 7, 8),
    )[0]

    assert action.cash_amount == Decimal("0.25")
    assert action.cash_currency == "USD"


def test_symbol_change_terms_are_preserved():
    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(_symbol_change(),),
            )
        )
    )

    action = resolve_us_corporate_actions_on_date(
        history,
        market_date=date(2024, 7, 9),
    )[0]

    assert action.old_symbol == "OLD"
    assert action.new_symbol == "NEW"


def test_delisting_is_admitted_without_listing_dependency():
    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(_delisting(),),
            )
        )
    )

    action = resolve_us_corporate_actions_on_date(
        history,
        market_date=date(2024, 7, 10),
    )[0]

    assert (
        action.action_type
        == USCorporateActionType.DELISTING
    )


def test_action_effective_date_does_not_require_session_in_us4():
    sunday = date(2024, 7, 7)

    event = USCorporateActionEvent(
        event_id="weekend-other",
        effective_date=sunday,
        action_type=USCorporateActionType.OTHER,
        details="explicit source effective date",
    )

    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(event,),
            )
        )
    )

    assert (
        resolve_us_corporate_actions_on_date(
            history,
            market_date=sunday,
        )[0]
        .event_id
        == "weekend-other"
    )


def test_multiple_distinct_actions_same_date_are_allowed():
    day = date(2024, 7, 8)

    first = _dividend(
        event_id="dividend-a",
        effective_date=day,
    )

    second = USCorporateActionEvent(
        event_id="other-b",
        effective_date=day,
        action_type=USCorporateActionType.OTHER,
        details="second distinct event",
    )

    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(second, first),
            )
        )
    )

    actions = resolve_us_corporate_actions_on_date(
        history,
        market_date=day,
    )

    assert tuple(
        item.event_id for item in actions
    ) == (
        "dividend-a",
        "other-b",
    )


def test_fact_canonicalizes_action_order():
    late = _delisting()
    early = _split()

    fact = _fact(
        coverage=_coverage(
            actions=(late, early),
        )
    )

    assert tuple(
        item.event_id
        for item in fact.coverage.actions
    ) == (
        "split-1",
        "delist-1",
    )


def test_fact_requires_exact_coverage_object():
    coverage = _coverage()

    with pytest.raises(
        ValidationError,
        match="exact USCorporateActionCoverage required",
    ):
        HistoricalUSCorporateActionFact(
            coverage=coverage.model_dump(),
            evidence_package=_package(),
        )


def test_fact_requires_exact_evidence_package():
    package = _package()

    with pytest.raises(
        ValidationError,
        match="exact HistoricalEvidencePackage required",
    ):
        HistoricalUSCorporateActionFact(
            coverage=_coverage(),
            evidence_package=package.model_dump(),
        )


def test_evidence_must_cover_complete_action_scope():
    fields = set(
        US_ACTION_COVERAGE_EVIDENCE_FIELDS
    )
    fields.remove("actions")

    with pytest.raises(
        ValidationError,
        match="actions",
    ):
        _fact(
            package=_package(
                covered_fields=fields,
            )
        )


def test_extra_reviewed_fields_are_allowed():
    fields = (
        set(US_ACTION_COVERAGE_EVIDENCE_FIELDS)
        | {"provider_revision_id"}
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
        match="canonical corporate-action fact tuple",
    ):
        admit_us_corporate_action_history(
            [_fact()],
            instrument_id=INSTRUMENT,
            coverage_start=date(2024, 7, 1),
            coverage_end=date(2024, 7, 10),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_at_least_one_complete_coverage_fact_is_required():
    with pytest.raises(
        ValueError,
        match="at least one complete",
    ):
        _admit()


def test_items_must_be_exact_fact_type():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="exact HistoricalUSCorporateActionFact",
    ):
        admit_us_corporate_action_history(
            (fact.model_dump(),),
            instrument_id=INSTRUMENT,
            coverage_start=date(2024, 7, 1),
            coverage_end=date(2024, 7, 10),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_requested_instrument_id_requires_exact_uuid():
    with pytest.raises(
        ValueError,
        match="instrument_id must be exact UUID",
    ):
        admit_us_corporate_action_history(
            (_fact(),),
            instrument_id=str(INSTRUMENT),
            coverage_start=date(2024, 7, 1),
            coverage_end=date(2024, 7, 10),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_requested_dates_require_exact_date():
    with pytest.raises(
        ValueError,
        match="coverage_start must be exact date",
    ):
        admit_us_corporate_action_history(
            (_fact(),),
            instrument_id=INSTRUMENT,
            coverage_start=datetime(
                2024, 7, 1,
                tzinfo=timezone.utc,
            ),
            coverage_end=date(2024, 7, 10),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_inverted_requested_coverage_is_rejected():
    with pytest.raises(
        ValueError,
        match="coverage_end cannot precede",
    ):
        _admit(
            _fact(),
            start=date(2024, 7, 10),
            end=date(2024, 7, 1),
        )


def test_coverage_cannot_extend_beyond_decision_horizon():
    with pytest.raises(
        ValueError,
        match="coverage_end cannot exceed",
    ):
        _admit(
            _fact(),
            end=date(2024, 7, 12),
        )


def test_fact_instrument_must_match_requested_instrument():
    other = uuid4()

    fact = _fact(
        coverage=_coverage(
            instrument_id=other,
        )
    )

    with pytest.raises(
        ValueError,
        match="instrument_id mismatch",
    ):
        _admit(fact)


def test_fact_must_stay_inside_requested_coverage():
    fact = _fact(
        coverage=_coverage(
            start=date(2024, 6, 30),
            end=date(2024, 7, 10),
        )
    )

    with pytest.raises(
        ValueError,
        match="outside requested coverage",
    ):
        _admit(fact)


def test_two_adjacent_complete_segments_are_valid():
    first = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 5),
            actions=(_split(),),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        coverage=_coverage(
            start=date(2024, 7, 6),
            end=date(2024, 7, 10),
            actions=(_dividend(),),
        ),
        package=_package(
            sha_char="b",
        ),
    )

    history = _admit(
        first,
        second,
    )

    assert len(history.facts) == 2


def test_gap_between_complete_segments_is_rejected():
    first = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 4),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        coverage=_coverage(
            start=date(2024, 7, 6),
            end=date(2024, 7, 10),
        ),
        package=_package(
            sha_char="b",
        ),
    )

    with pytest.raises(
        ValueError,
        match="gap in complete",
    ):
        _admit(
            first,
            second,
        )


def test_overlap_between_complete_segments_is_rejected():
    first = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 6),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        coverage=_coverage(
            start=date(2024, 7, 6),
            end=date(2024, 7, 10),
        ),
        package=_package(
            sha_char="b",
        ),
    )

    with pytest.raises(
        ValueError,
        match="overlapping",
    ):
        _admit(
            first,
            second,
        )


def test_missing_tail_complete_coverage_is_rejected():
    fact = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 9),
        )
    )

    with pytest.raises(
        ValueError,
        match="gap in complete",
    ):
        _admit(fact)


def test_duplicate_exact_fact_is_rejected():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="duplicate historical US",
    ):
        _admit(
            fact,
            fact,
        )


def test_duplicate_event_id_across_segments_is_rejected():
    first = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 5),
            actions=(
                _split(
                    event_id="same-event",
                ),
            ),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second_event = USCorporateActionEvent(
        event_id="same-event",
        effective_date=date(2024, 7, 8),
        action_type=USCorporateActionType.OTHER,
        details="different segment same id",
    )

    second = _fact(
        coverage=_coverage(
            start=date(2024, 7, 6),
            end=date(2024, 7, 10),
            actions=(second_event,),
        ),
        package=_package(
            sha_char="b",
        ),
    )

    with pytest.raises(
        ValueError,
        match="duplicate corporate-action event_id",
    ):
        _admit(
            first,
            second,
        )


def test_future_historical_availability_is_rejected():
    package = _package(
        available_at=datetime(
            2024, 7, 12, 10, 0,
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


def test_raw_receipt_after_build_is_rejected():
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
                2024, 7, 11, 21, 0,
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


def test_us4_rejects_noncanonical_package_clock():
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
        match=(
            "raw_receipt.local_received_at "
            "must use datetime.timezone.utc"
        ),
    ):
        _admit(
            _fact(package=package)
        )


def test_input_order_of_coverage_segments_is_deterministic():
    first = _fact(
        coverage=_coverage(
            start=date(2024, 7, 1),
            end=date(2024, 7, 5),
        ),
        package=_package(
            sha_char="a",
        ),
    )

    second = _fact(
        coverage=_coverage(
            start=date(2024, 7, 6),
            end=date(2024, 7, 10),
        ),
        package=_package(
            sha_char="b",
        ),
    )

    a = _admit(
        first,
        second,
    )

    b = _admit(
        second,
        first,
    )

    assert a.facts == b.facts
    assert a.identity == b.identity


def test_research_build_clock_is_excluded_from_identity():
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


def test_decision_clock_is_part_of_identity():
    fact = _fact()

    first = _admit(
        fact,
        decision=DECISION,
    )

    later = datetime(
        2024, 7, 11, 21, 30,
        tzinfo=timezone.utc,
    )

    second = _admit(
        fact,
        decision=later,
    )

    assert first.identity != second.identity


def test_resolve_returns_exact_date_actions_only():
    coverage = _coverage(
        actions=(
            _split(),
            _dividend(),
        )
    )

    history = _admit(
        _fact(
            coverage=coverage,
        )
    )

    actions = resolve_us_corporate_actions_on_date(
        history,
        market_date=date(2024, 7, 5),
    )

    assert tuple(
        item.event_id
        for item in actions
    ) == (
        "split-1",
    )


def test_resolve_covered_no_action_date_returns_empty_tuple():
    history = _admit(
        _fact(
            coverage=_coverage(
                actions=(_split(),),
            )
        )
    )

    assert (
        resolve_us_corporate_actions_on_date(
            history,
            market_date=date(2024, 7, 6),
        )
        == ()
    )


def test_resolve_outside_coverage_fails():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="outside admitted",
    ):
        resolve_us_corporate_actions_on_date(
            history,
            market_date=date(2024, 6, 30),
        )


def test_resolve_market_date_requires_exact_date():
    history = _admit(
        _fact()
    )

    with pytest.raises(
        ValueError,
        match="market_date must be exact date",
    ):
        resolve_us_corporate_actions_on_date(
            history,
            market_date=datetime(
                2024, 7, 5,
                tzinfo=timezone.utc,
            ),
        )
