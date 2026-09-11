from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

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
    USSessionRecord,
    USSessionState,
)
from app.us.historical_session import (
    US_SESSION_EVIDENCE_FIELDS,
    AdmittedUSSessionHistory,
    HistoricalUSSessionFact,
    admit_us_session_history,
    resolve_us_session_on_date,
)


DECISION = datetime(
    2024, 7, 8, 18, 0,
    tzinfo=timezone.utc,
)

BUILD = datetime(
    2026, 9, 11, 10, 0,
    tzinfo=timezone.utc,
)


def _session(
    market_date=date(2024, 7, 5),
    *,
    mic="XNAS",
    state=USSessionState.REGULAR,
):
    if state == USSessionState.CLOSED:
        return USSessionRecord(
            market_date=market_date,
            calendar_mic=mic,
            state=state,
        )

    return USSessionRecord(
        market_date=market_date,
        calendar_mic=mic,
        state=state,
        opens_at_utc=datetime(
            market_date.year,
            market_date.month,
            market_date.day,
            13,
            30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            market_date.year,
            market_date.month,
            market_date.day,
            20,
            0,
            tzinfo=timezone.utc,
        ),
    )


def _package(
    *,
    available_at=datetime(
        2024, 7, 4, 20, 0,
        tzinfo=timezone.utc,
    ),
    received_at=datetime(
        2026, 9, 10, 10, 0,
        tzinfo=timezone.utc,
    ),
    attachment_received_at=None,
    reviewed_at=datetime(
        2026, 9, 10, 12, 0,
        tzinfo=timezone.utc,
    ),
    approved=True,
    covered_fields=None,
    sha_char="a",
):
    attachment_received_at = (
        received_at
        if attachment_received_at is None
        else attachment_received_at
    )

    raw = HistoricalRawReceipt(
        provider="fixture-provider",
        source="fixture US session calendar",
        source_locator=(
            f"fixture://us-session/{sha_char}"
        ),
        sha256=sha_char * 64,
        byte_size=100,
        local_received_at=received_at,
        source_edition="fixture-edition-1",
        evidence_category="US_SESSION_CALENDAR",
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator=(
            f"fixture://availability/{sha_char}"
        ),
        sha256="f" * 64,
        local_received_at=attachment_received_at,
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
            "exact US MIC-scoped session fixture"
        ),
        covered_fields=tuple(
            covered_fields
            if covered_fields is not None
            else sorted(
                US_SESSION_EVIDENCE_FIELDS
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
    market_date=date(2024, 7, 5),
    *,
    mic="XNAS",
    state=USSessionState.REGULAR,
    package=None,
):
    return HistoricalUSSessionFact(
        session=_session(
            market_date,
            mic=mic,
            state=state,
        ),
        evidence_package=(
            package or _package()
        ),
    )


def _three_days(
    *,
    mic="XNAS",
):
    return (
        _fact(
            date(2024, 7, 5),
            mic=mic,
            package=_package(sha_char="c"),
        ),
        _fact(
            date(2024, 7, 6),
            mic=mic,
            state=USSessionState.CLOSED,
            package=_package(sha_char="d"),
        ),
        _fact(
            date(2024, 7, 7),
            mic=mic,
            state=USSessionState.CLOSED,
            package=_package(sha_char="e"),
        ),
    )


def _admit(
    *facts,
    mic="XNAS",
    start=date(2024, 7, 5),
    end=date(2024, 7, 7),
    decision=DECISION,
    build=BUILD,
):
    return admit_us_session_history(
        tuple(facts),
        calendar_mic=mic,
        coverage_start=start,
        coverage_end=end,
        decision_at=decision,
        research_built_at=build,
    )


def test_admits_complete_three_calendar_day_history():
    facts = _three_days()

    history = _admit(*facts)

    assert isinstance(
        history,
        AdmittedUSSessionHistory,
    )
    assert tuple(
        item.session.market_date
        for item in history.facts
    ) == (
        date(2024, 7, 5),
        date(2024, 7, 6),
        date(2024, 7, 7),
    )


def test_closed_weekend_dates_are_explicit_facts():
    history = _admit(*_three_days())

    saturday = resolve_us_session_on_date(
        history,
        market_date=date(2024, 7, 6),
    )

    sunday = resolve_us_session_on_date(
        history,
        market_date=date(2024, 7, 7),
    )

    assert saturday.state == USSessionState.CLOSED
    assert sunday.state == USSessionState.CLOSED


def test_single_closed_day_is_valid_complete_history():
    fact = _fact(
        date(2024, 7, 6),
        state=USSessionState.CLOSED,
    )

    history = _admit(
        fact,
        start=date(2024, 7, 6),
        end=date(2024, 7, 6),
    )

    assert history.facts == (fact,)


def test_missing_calendar_date_is_rejected():
    first, _, third = _three_days()

    with pytest.raises(
        ValueError,
        match="2024-07-06",
    ):
        _admit(first, third)


def test_exact_duplicate_fact_is_rejected():
    fact = _fact(
        date(2024, 7, 5),
    )

    with pytest.raises(
        ValueError,
        match="duplicate historical US session fact",
    ):
        _admit(
            fact,
            fact,
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_multiple_facts_for_same_date_are_rejected():
    first = _fact(
        date(2024, 7, 5),
        package=_package(sha_char="c"),
    )

    second = _fact(
        date(2024, 7, 5),
        state=USSessionState.CLOSED,
        package=_package(sha_char="d"),
    )

    with pytest.raises(
        ValueError,
        match="multiple US session facts",
    ):
        _admit(
            first,
            second,
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_wrong_calendar_mic_is_rejected():
    fact = _fact(
        date(2024, 7, 5),
        mic="XNYS",
    )

    with pytest.raises(
        ValueError,
        match="calendar_mic does not match",
    ):
        _admit(
            fact,
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_xnas_and_xnys_histories_are_independent():
    xnas = _admit(
        *_three_days(mic="XNAS"),
        mic="XNAS",
    )

    xnys = _admit(
        *_three_days(mic="XNYS"),
        mic="XNYS",
    )

    assert xnas.calendar_mic == "XNAS"
    assert xnys.calendar_mic == "XNYS"
    assert xnas.identity != xnys.identity


@pytest.mark.parametrize(
    "mic",
    [
        "xnas",
        "NAS",
        "XNAS1",
        "XN@S",
    ],
)
def test_requested_calendar_mic_must_be_canonical(mic):
    with pytest.raises(
        ValueError,
        match="canonical four-character MIC",
    ):
        _admit(
            *_three_days(),
            mic=mic,
        )


def test_fact_outside_requested_coverage_is_rejected():
    fact = _fact(
        date(2024, 7, 4),
    )

    with pytest.raises(
        ValueError,
        match="outside requested coverage",
    ):
        _admit(
            fact,
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_inverted_coverage_is_rejected():
    with pytest.raises(
        ValueError,
        match="coverage_end cannot precede",
    ):
        _admit(
            start=date(2024, 7, 7),
            end=date(2024, 7, 5),
        )


def test_coverage_dates_require_exact_date_objects():
    with pytest.raises(
        ValueError,
        match="coverage_start must be exact date",
    ):
        admit_us_session_history(
            (),
            calendar_mic="XNAS",
            coverage_start=datetime(
                2024, 7, 5,
                tzinfo=timezone.utc,
            ),
            coverage_end=date(2024, 7, 5),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_coverage_cannot_extend_past_us_local_decision_date():
    decision = datetime(
        2024, 7, 8, 1, 0,
        tzinfo=timezone.utc,
    )
    # 2024-07-08 01:00 UTC is still 2024-07-07
    # in America/New_York.

    with pytest.raises(
        ValueError,
        match="coverage_end cannot exceed",
    ):
        _admit(
            decision=decision,
            start=date(2024, 7, 8),
            end=date(2024, 7, 8),
        )


def test_future_effective_session_fact_is_rejected():
    future = _fact(
        date(2024, 7, 9),
    )

    with pytest.raises(
        ValueError,
        match="future-effective US session",
    ):
        _admit(
            future,
            start=date(2024, 7, 5),
            end=date(2024, 7, 8),
        )


def test_input_must_be_exact_tuple():
    with pytest.raises(
        ValueError,
        match="canonical session fact tuple",
    ):
        admit_us_session_history(
            list(_three_days()),
            calendar_mic="XNAS",
            coverage_start=date(2024, 7, 5),
            coverage_end=date(2024, 7, 7),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_items_must_be_exact_fact_type():
    fact = _fact(
        date(2024, 7, 5),
    )

    with pytest.raises(
        ValueError,
        match="exact HistoricalUSSessionFact",
    ):
        admit_us_session_history(
            (fact.model_dump(),),
            calendar_mic="XNAS",
            coverage_start=date(2024, 7, 5),
            coverage_end=date(2024, 7, 5),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_fact_requires_exact_session_object():
    session = _session()

    with pytest.raises(
        ValidationError,
        match="exact USSessionRecord required",
    ):
        HistoricalUSSessionFact(
            session=session.model_dump(),
            evidence_package=_package(),
        )


def test_fact_requires_exact_evidence_package_object():
    package = _package()

    with pytest.raises(
        ValidationError,
        match="exact HistoricalEvidencePackage required",
    ):
        HistoricalUSSessionFact(
            session=_session(),
            evidence_package=package.model_dump(),
        )


def test_evidence_must_cover_all_session_fields():
    fields = set(
        US_SESSION_EVIDENCE_FIELDS
    )
    fields.remove("calendar_mic")

    with pytest.raises(
        ValidationError,
        match="calendar_mic",
    ):
        _fact(
            package=_package(
                covered_fields=sorted(fields),
            )
        )


def test_extra_reviewed_fields_are_allowed():
    package = _package(
        covered_fields=sorted(
            set(US_SESSION_EVIDENCE_FIELDS)
            | {"exchange_name"}
        )
    )

    history = _admit(
        _fact(package=package),
        start=date(2024, 7, 5),
        end=date(2024, 7, 5),
    )

    assert len(history.facts) == 1


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
            *_three_days(),
            decision=datetime(
                2024, 7, 8, 18, 0,
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
        match=(
            "research_built_at must use "
            "datetime.timezone.utc"
        ),
    ):
        _admit(
            *_three_days(),
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
            *_three_days(),
            build=datetime(
                2024, 7, 1, 10, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_future_historical_availability_is_rejected():
    package = _package(
        available_at=datetime(
            2024, 7, 9, 10, 0,
            tzinfo=timezone.utc,
        )
    )

    with pytest.raises(
        ValueError,
        match="historical availability not safely known",
    ):
        _admit(
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
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
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_attachment_after_build_is_rejected():
    package = _package(
        attachment_received_at=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    with pytest.raises(
        ValueError,
        match="attachment receipt after research build",
    ):
        _admit(
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
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
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
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
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_us_boundary_rejects_noncanonical_receipt_clock():
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
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_us_boundary_rejects_noncanonical_availability_clock():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    package = _package(
        available_at=datetime(
            2024, 7, 4, 20, 0,
            tzinfo=zero,
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "availability.exact_at must use "
            "datetime.timezone.utc"
        ),
    ):
        _admit(
            _fact(package=package),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
        )


def test_input_order_does_not_change_canonical_history():
    facts = _three_days()

    first = _admit(*facts)
    second = _admit(*reversed(facts))

    assert first.facts == second.facts
    assert first.identity == second.identity


def test_research_build_clock_is_excluded_from_identity():
    facts = _three_days()

    first = _admit(
        *facts,
        build=BUILD,
    )

    second = _admit(
        *facts,
        build=datetime(
            2026, 9, 12, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert first.identity == second.identity


def test_decision_clock_is_part_of_semantic_identity():
    facts = _three_days()

    first = _admit(
        *facts,
        decision=datetime(
            2024, 7, 8, 18, 0,
            tzinfo=timezone.utc,
        ),
    )

    second = _admit(
        *facts,
        decision=datetime(
            2024, 7, 8, 19, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert first.identity != second.identity


def test_resolve_returns_exact_date_session():
    history = _admit(*_three_days())

    session = resolve_us_session_on_date(
        history,
        market_date=date(2024, 7, 5),
    )

    assert session.market_date == date(
        2024, 7, 5
    )
    assert session.state == USSessionState.REGULAR


def test_resolve_does_not_forward_fill_before_coverage():
    history = _admit(*_three_days())

    with pytest.raises(
        ValueError,
        match="outside admitted session coverage",
    ):
        resolve_us_session_on_date(
            history,
            market_date=date(2024, 7, 4),
        )


def test_resolve_does_not_back_fill_after_coverage():
    history = _admit(*_three_days())

    with pytest.raises(
        ValueError,
        match="outside admitted session coverage",
    ):
        resolve_us_session_on_date(
            history,
            market_date=date(2024, 7, 8),
        )


def test_resolve_market_date_requires_exact_date():
    history = _admit(*_three_days())

    with pytest.raises(
        ValueError,
        match="market_date must be exact date",
    ):
        resolve_us_session_on_date(
            history,
            market_date=datetime(
                2024, 7, 5,
                tzinfo=timezone.utc,
            ),
        )
