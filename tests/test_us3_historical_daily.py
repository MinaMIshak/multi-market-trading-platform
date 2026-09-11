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
    USListingIdentity,
    USSecurityType,
    USSessionRecord,
    USSessionState,
)
from app.us.historical_daily import (
    US_DAILY_BAR_EVIDENCE_FIELDS,
    AdmittedUSDailyBarHistory,
    HistoricalUSDailyBarFact,
    USHistoricalDailyBar,
    admit_us_daily_bar_history,
    resolve_us_daily_bar_on_date,
)
from app.us.historical_identity import (
    US_LISTING_EVIDENCE_FIELDS,
    HistoricalUSListingFact,
    admit_us_listing_history,
)
from app.us.historical_session import (
    US_SESSION_EVIDENCE_FIELDS,
    HistoricalUSSessionFact,
    admit_us_session_history,
)


INSTRUMENT = uuid4()

DECISION = datetime(
    2024, 7, 8, 21, 0,
    tzinfo=timezone.utc,
)

BUILD = datetime(
    2026, 9, 11, 10, 0,
    tzinfo=timezone.utc,
)


def _package(
    *,
    provider,
    category,
    covered_fields,
    available_at,
    sha_char="a",
    received_at=datetime(
        2026, 9, 10, 10, 0,
        tzinfo=timezone.utc,
    ),
    reviewed_at=datetime(
        2026, 9, 10, 12, 0,
        tzinfo=timezone.utc,
    ),
    approved=True,
):
    raw = HistoricalRawReceipt(
        provider=provider,
        source=f"fixture {category}",
        source_locator=(
            f"fixture://{category}/{sha_char}"
        ),
        sha256=sha_char * 64,
        byte_size=100,
        local_received_at=received_at,
        source_edition="fixture-edition-1",
        evidence_category=category,
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator=(
            f"fixture://proof/{category}/{sha_char}"
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
        covered_scope=f"fixture {category}",
        covered_fields=tuple(
            sorted(covered_fields)
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


def _listing(
    market_date,
    *,
    symbol="TEST",
    instrument_id=INSTRUMENT,
):
    listing = USListingIdentity(
        effective_date=market_date,
        instrument_id=instrument_id,
        canonical_symbol=symbol,
        listing_mic="XNAS",
        security_type=USSecurityType.COMMON_STOCK,
        provider_symbol=symbol,
        source_provider="identity-provider",
        source_instrument_key="identity-key",
        is_primary_listing=True,
    )

    package = _package(
        provider="identity-provider",
        category="US_LISTING_IDENTITY",
        covered_fields=US_LISTING_EVIDENCE_FIELDS,
        available_at=datetime(
            2024, 7, 4, 12, 0,
            tzinfo=timezone.utc,
        ),
        sha_char="a",
    )

    return HistoricalUSListingFact(
        listing=listing,
        evidence_package=package,
    )


def _listing_history(
    dates=(
        date(2024, 7, 5),
        date(2024, 7, 8),
    ),
    *,
    symbol="TEST",
    decision=DECISION,
    build=BUILD,
):
    return admit_us_listing_history(
        tuple(
            _listing(
                day,
                symbol=symbol,
            )
            for day in dates
        ),
        decision_at=decision,
        research_built_at=build,
    )


def _session(
    market_date,
    *,
    state=USSessionState.REGULAR,
):
    if state == USSessionState.CLOSED:
        record = USSessionRecord(
            market_date=market_date,
            calendar_mic="XNAS",
            state=state,
        )
    else:
        close_hour = (
            17
            if state == USSessionState.EARLY_CLOSE
            else 20
        )

        record = USSessionRecord(
            market_date=market_date,
            calendar_mic="XNAS",
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
                close_hour,
                0,
                tzinfo=timezone.utc,
            ),
        )

    package = _package(
        provider="calendar-provider",
        category="US_SESSION_CALENDAR",
        covered_fields=US_SESSION_EVIDENCE_FIELDS,
        available_at=datetime(
            2024, 7, 4, 12, 0,
            tzinfo=timezone.utc,
        ),
        sha_char="b",
    )

    return HistoricalUSSessionFact(
        session=record,
        evidence_package=package,
    )


def _session_history(
    *,
    decision=DECISION,
    build=BUILD,
):
    facts = (
        _session(date(2024, 7, 5)),
        _session(
            date(2024, 7, 6),
            state=USSessionState.CLOSED,
        ),
        _session(
            date(2024, 7, 7),
            state=USSessionState.CLOSED,
        ),
        _session(date(2024, 7, 8)),
    )

    return admit_us_session_history(
        facts,
        calendar_mic="XNAS",
        coverage_start=date(2024, 7, 5),
        coverage_end=date(2024, 7, 8),
        decision_at=decision,
        research_built_at=build,
    )


def _bar(
    market_date=date(2024, 7, 5),
    *,
    instrument_id=INSTRUMENT,
    symbol="TEST",
    mic="XNAS",
    provider="bar-provider",
    source_sha="c" * 64,
    adjusted=Decimal("10"),
):
    return USHistoricalDailyBar(
        instrument_id=instrument_id,
        market_date=market_date,
        calendar_mic=mic,
        canonical_symbol=symbol,
        provider_symbol="TEST.US",
        source_instrument_key="bar-provider-key",
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal("11"),
        volume=Decimal("1000"),
        provider_adjusted_close_reference=adjusted,
        source_provider=provider,
        source_snapshot_date=date(2026, 9, 10),
        source_row_number=1,
        source_sha256=source_sha,
    )


def _bar_package(
    market_date=date(2024, 7, 5),
    *,
    provider="bar-provider",
    sha_char="c",
    covered_fields=US_DAILY_BAR_EVIDENCE_FIELDS,
    available_at=None,
    received_at=datetime(
        2026, 9, 10, 10, 0,
        tzinfo=timezone.utc,
    ),
    reviewed_at=datetime(
        2026, 9, 10, 12, 0,
        tzinfo=timezone.utc,
    ),
    approved=True,
):
    if available_at is None:
        available_at = datetime(
            market_date.year,
            market_date.month,
            market_date.day,
            20,
            30,
            tzinfo=timezone.utc,
        )

    return _package(
        provider=provider,
        category="US_DAILY_BAR",
        covered_fields=covered_fields,
        available_at=available_at,
        sha_char=sha_char,
        received_at=received_at,
        reviewed_at=reviewed_at,
        approved=approved,
    )


def _fact(
    market_date=date(2024, 7, 5),
    *,
    bar=None,
    package=None,
):
    return HistoricalUSDailyBarFact(
        bar=bar or _bar(market_date),
        evidence_package=(
            package or _bar_package(market_date)
        ),
    )


def _admit(
    *facts,
    start=date(2024, 7, 5),
    end=date(2024, 7, 8),
    listing_history=None,
    session_history=None,
    decision=DECISION,
    build=BUILD,
):
    return admit_us_daily_bar_history(
        tuple(facts),
        instrument_id=INSTRUMENT,
        calendar_mic="XNAS",
        coverage_start=start,
        coverage_end=end,
        listing_history=(
            listing_history
            or _listing_history(
                decision=decision,
                build=build,
            )
        ),
        session_history=(
            session_history
            or _session_history(
                decision=decision,
                build=build,
            )
        ),
        decision_at=decision,
        research_built_at=build,
    )


def test_admits_raw_daily_bar():
    history = _admit(
        _fact(date(2024, 7, 5))
    )

    assert isinstance(
        history,
        AdmittedUSDailyBarHistory,
    )
    assert history.facts[0].bar.close == Decimal("11")


def test_provider_adjusted_close_is_reference_only():
    fact = _fact(
        bar=_bar(
            adjusted=Decimal("999"),
        )
    )

    history = _admit(fact)

    assert history.facts[0].bar.close == Decimal("11")
    assert (
        history.facts[0]
        .bar.provider_adjusted_close_reference
        == Decimal("999")
    )


def test_adjusted_price_basis_is_not_allowed():
    payload = _bar().model_dump(
        mode="python"
    )
    payload["price_basis"] = "ADJUSTED"

    with pytest.raises(ValidationError):
        USHistoricalDailyBar(**payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", 10),
        ("high", 12.0),
        ("low", "9"),
        ("close", 11),
        ("volume", 1000),
    ],
)
def test_prices_and_volume_require_exact_decimal(
    field,
    value,
):
    payload = _bar().model_dump(
        mode="python"
    )
    payload[field] = value

    with pytest.raises(
        ValidationError,
        match="exact Decimal",
    ):
        USHistoricalDailyBar(**payload)


def test_invalid_ohlc_is_rejected():
    payload = _bar().model_dump(
        mode="python"
    )
    payload["open"] = Decimal("20")

    with pytest.raises(
        ValidationError,
        match="open outside high-low range",
    ):
        USHistoricalDailyBar(**payload)


def test_snapshot_cannot_precede_market_date():
    payload = _bar().model_dump(
        mode="python"
    )
    payload["source_snapshot_date"] = date(
        2024, 7, 4
    )

    with pytest.raises(
        ValidationError,
        match="source_snapshot_date cannot precede",
    ):
        USHistoricalDailyBar(**payload)


def test_fact_requires_exact_bar_object():
    with pytest.raises(
        ValidationError,
        match="exact USHistoricalDailyBar required",
    ):
        HistoricalUSDailyBarFact(
            bar=_bar().model_dump(),
            evidence_package=_bar_package(),
        )


def test_fact_requires_exact_package_object():
    package = _bar_package()

    with pytest.raises(
        ValidationError,
        match="exact HistoricalEvidencePackage required",
    ):
        HistoricalUSDailyBarFact(
            bar=_bar(),
            evidence_package=package.model_dump(),
        )


def test_bar_provider_must_match_receipt():
    with pytest.raises(
        ValidationError,
        match="source_provider does not match",
    ):
        _fact(
            bar=_bar(provider="provider-a"),
            package=_bar_package(
                provider="provider-b",
            ),
        )


def test_bar_sha_must_match_receipt():
    with pytest.raises(
        ValidationError,
        match="source_sha256 does not match",
    ):
        _fact(
            bar=_bar(
                source_sha="d" * 64,
            ),
            package=_bar_package(
                sha_char="c",
            ),
        )


def test_evidence_must_cover_all_daily_fields():
    fields = set(
        US_DAILY_BAR_EVIDENCE_FIELDS
    )
    fields.remove("price_basis")

    with pytest.raises(
        ValidationError,
        match="price_basis",
    ):
        _fact(
            package=_bar_package(
                covered_fields=fields,
            )
        )


def test_extra_reviewed_fields_are_allowed():
    package = _bar_package(
        covered_fields=(
            set(US_DAILY_BAR_EVIDENCE_FIELDS)
            | {"vendor_exchange_code"}
        )
    )

    history = _admit(
        _fact(package=package)
    )

    assert len(history.facts) == 1


def test_bar_on_explicitly_closed_session_is_rejected():
    closed_history = admit_us_session_history(
        (
            _session(
                date(2024, 7, 6),
                state=USSessionState.CLOSED,
            ),
        ),
        calendar_mic="XNAS",
        coverage_start=date(2024, 7, 6),
        coverage_end=date(2024, 7, 6),
        decision_at=DECISION,
        research_built_at=BUILD,
    )

    listing_history = _listing_history(
        dates=(date(2024, 7, 6),),
    )

    with pytest.raises(
        ValueError,
        match="explicitly closed session",
    ):
        _admit(
            _fact(date(2024, 7, 6)),
            start=date(2024, 7, 6),
            end=date(2024, 7, 6),
            listing_history=listing_history,
            session_history=closed_history,
        )


def test_early_close_session_can_have_daily_bar():
    session_history = admit_us_session_history(
        (
            _session(
                date(2024, 7, 5),
                state=USSessionState.EARLY_CLOSE,
            ),
        ),
        calendar_mic="XNAS",
        coverage_start=date(2024, 7, 5),
        coverage_end=date(2024, 7, 5),
        decision_at=DECISION,
        research_built_at=BUILD,
    )

    history = _admit(
        _fact(date(2024, 7, 5)),
        start=date(2024, 7, 5),
        end=date(2024, 7, 5),
        listing_history=_listing_history(
            dates=(date(2024, 7, 5),)
        ),
        session_history=session_history,
    )

    assert len(history.facts) == 1


def test_missing_open_session_bar_is_not_inferred():
    # Coverage includes Jul 5 and Jul 8 plus the closed weekend,
    # but only Jul 5 has an admitted bar. US3 is intentionally sparse.
    history = _admit(
        _fact(date(2024, 7, 5))
    )

    assert len(history.facts) == 1

    with pytest.raises(
        ValueError,
        match="no admitted US daily bar",
    ):
        resolve_us_daily_bar_on_date(
            history,
            market_date=date(2024, 7, 8),
        )


def test_exact_dated_listing_identity_is_required():
    listing_history = _listing_history(
        dates=(date(2024, 7, 4),)
    )

    with pytest.raises(
        ValueError,
        match="exact-dated US listing identity unavailable",
    ):
        _admit(
            _fact(date(2024, 7, 5)),
            listing_history=listing_history,
        )


def test_ticker_must_match_exact_dated_identity():
    listing_history = _listing_history(
        dates=(date(2024, 7, 5),),
        symbol="NEW",
    )

    with pytest.raises(
        ValueError,
        match="canonical_symbol does not match",
    ):
        _admit(
            _fact(date(2024, 7, 5)),
            start=date(2024, 7, 5),
            end=date(2024, 7, 5),
            listing_history=listing_history,
            session_history=admit_us_session_history(
                (_session(date(2024, 7, 5)),),
                calendar_mic="XNAS",
                coverage_start=date(2024, 7, 5),
                coverage_end=date(2024, 7, 5),
                decision_at=DECISION,
                research_built_at=BUILD,
            ),
        )


def test_wrong_instrument_is_rejected():
    other = uuid4()

    with pytest.raises(
        ValueError,
        match="instrument_id mismatch",
    ):
        _admit(
            _fact(
                bar=_bar(
                    instrument_id=other,
                )
            )
        )


def test_wrong_mic_is_rejected():
    with pytest.raises(
        ValueError,
        match="calendar_mic mismatch",
    ):
        _admit(
            _fact(
                bar=_bar(
                    mic="XNYS",
                )
            )
        )


def test_input_must_be_exact_tuple():
    with pytest.raises(
        ValueError,
        match="canonical daily-bar fact tuple",
    ):
        admit_us_daily_bar_history(
            [_fact()],
            instrument_id=INSTRUMENT,
            calendar_mic="XNAS",
            coverage_start=date(2024, 7, 5),
            coverage_end=date(2024, 7, 8),
            listing_history=_listing_history(),
            session_history=_session_history(),
            decision_at=DECISION,
            research_built_at=BUILD,
        )


def test_duplicate_exact_fact_is_rejected():
    fact = _fact()

    with pytest.raises(
        ValueError,
        match="duplicate historical US daily-bar fact",
    ):
        _admit(fact, fact)


def test_conflicting_same_date_is_rejected():
    first = _fact(
        package=_bar_package(
            sha_char="c",
        )
    )

    second_bar = _bar(
        source_sha="d" * 64,
    )

    second = _fact(
        bar=second_bar,
        package=_bar_package(
            sha_char="d",
        ),
    )

    with pytest.raises(
        ValueError,
        match="multiple US daily-bar facts",
    ):
        _admit(first, second)


def test_bar_outside_requested_coverage_is_rejected():
    with pytest.raises(
        ValueError,
        match="outside requested coverage",
    ):
        _admit(
            _fact(date(2024, 7, 5)),
            start=date(2024, 7, 8),
            end=date(2024, 7, 8),
        )


def test_inverted_coverage_is_rejected():
    with pytest.raises(
        ValueError,
        match="coverage_end cannot precede",
    ):
        _admit(
            _fact(),
            start=date(2024, 7, 8),
            end=date(2024, 7, 5),
        )


def test_coverage_cannot_extend_beyond_decision():
    with pytest.raises(
        ValueError,
        match="coverage_end cannot exceed",
    ):
        _admit(
            _fact(),
            end=date(2024, 7, 9),
        )


def test_future_availability_is_rejected():
    package = _bar_package(
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
            _fact(package=package)
        )


def test_unapproved_review_is_rejected():
    package = _bar_package(
        approved=False,
    )

    with pytest.raises(
        ValueError,
        match="approved review required",
    ):
        _admit(
            _fact(package=package)
        )


def test_receipt_after_build_is_rejected():
    package = _bar_package(
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


def test_decision_requires_exact_utc():
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
                2024, 7, 8, 21, 0,
                tzinfo=zero,
            ),
        )


def test_build_requires_exact_utc():
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


def test_noncanonical_package_clock_is_rejected():
    zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    package = _bar_package(
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


def test_dependency_decision_must_match():
    different = datetime(
        2024, 7, 8, 20, 0,
        tzinfo=timezone.utc,
    )

    listing_history = _listing_history(
        decision=different,
    )

    with pytest.raises(
        ValueError,
        match="listing history decision_at must match",
    ):
        _admit(
            _fact(),
            listing_history=listing_history,
        )


def test_input_order_is_deterministic():
    first = _fact(
        date(2024, 7, 5),
        package=_bar_package(
            date(2024, 7, 5),
            sha_char="c",
        ),
    )

    second = _fact(
        date(2024, 7, 8),
        bar=_bar(
            date(2024, 7, 8),
            source_sha="d" * 64,
        ),
        package=_bar_package(
            date(2024, 7, 8),
            sha_char="d",
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

    later = datetime(
        2026, 9, 12, 10, 0,
        tzinfo=timezone.utc,
    )

    listing_history = _listing_history(
        build=BUILD,
    )
    session_history = _session_history(
        build=BUILD,
    )

    second = _admit(
        fact,
        listing_history=listing_history,
        session_history=session_history,
        build=later,
    )

    assert first.identity == second.identity


def test_decision_is_part_of_identity():
    fact = _fact()

    first = _admit(fact)

    later_decision = datetime(
        2024, 7, 8, 21, 30,
        tzinfo=timezone.utc,
    )

    second = _admit(
        fact,
        decision=later_decision,
        listing_history=_listing_history(
            decision=later_decision,
        ),
        session_history=_session_history(
            decision=later_decision,
        ),
    )

    assert first.identity != second.identity


def test_exact_resolution_returns_raw_bar():
    history = _admit(_fact())

    bar = resolve_us_daily_bar_on_date(
        history,
        market_date=date(2024, 7, 5),
    )

    assert bar.close == Decimal("11")
    assert bar.price_basis == "RAW_UNADJUSTED"


def test_resolution_does_not_forward_fill():
    history = _admit(_fact())

    with pytest.raises(
        ValueError,
        match="no admitted US daily bar",
    ):
        resolve_us_daily_bar_on_date(
            history,
            market_date=date(2024, 7, 8),
        )


def test_resolution_outside_coverage_fails():
    history = _admit(_fact())

    with pytest.raises(
        ValueError,
        match="outside admitted daily-bar coverage",
    ):
        resolve_us_daily_bar_on_date(
            history,
            market_date=date(2024, 7, 4),
        )
