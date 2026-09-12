from __future__ import annotations

import ast
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from pathlib import Path
from uuid import UUID

import pytest

from app.data.daily_canonical import (
    CanonicalDailyBar,
    DailyBarSemanticClass,
)
from app.data.reference import (
    ActionEvidence,
    ActionEvidenceRow,
    UniverseEvidence,
    UniverseMember,
)
from app.research.historical_evidence import (
    HistoricalAttachmentReference,
    HistoricalAvailability,
    HistoricalAvailabilityEvidence,
    HistoricalEvidenceAttachment,
    HistoricalEvidencePackage,
    HistoricalRawReceipt,
    HistoricalSourceReview,
)
from app.research.historical_pit import (
    ACTION_EVIDENCE_FIELDS,
    DAILY_EVIDENCE_FIELDS,
    IDENTITY_EVIDENCE_FIELDS,
    SESSION_EVIDENCE_FIELDS,
    TRANSFORMATION_VERSION,
    UNIVERSE_EVIDENCE_FIELDS,
    HistoricalActionCoverage,
    HistoricalDailyObservation,
    HistoricalIdentityMapping,
    HistoricalPITInput,
    HistoricalSessionRecord,
    HistoricalUniverseSnapshot,
    ResearchIndicatorBar,
    ResearchPITDaily,
    derive_research_pit_daily,
)


INSTRUMENT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_INSTRUMENT = UUID("22222222-2222-2222-2222-222222222222")

D1 = date(2020, 1, 2)
D2 = date(2020, 1, 3)
D3 = date(2020, 1, 4)

EARLY = datetime(2020, 1, 1, 10, tzinfo=timezone.utc)
DECISION = datetime(2020, 1, 4, 12, tzinfo=timezone.utc)
FUTURE = datetime(2020, 1, 5, 12, tzinfo=timezone.utc)
BUILD = datetime(2020, 1, 10, 12, tzinfo=timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _package(
    label: str,
    fields: tuple[str, ...],
    *,
    available_at: datetime = EARLY,
    received_at: datetime = EARLY,
    reviewed_at: datetime = EARLY,
    provider: str = "provider",
    approved: bool = True,
    bounded_start: datetime | None = None,
    bounded_end: datetime | None = None,
) -> HistoricalEvidencePackage:
    raw = HistoricalRawReceipt(
        provider=provider,
        source=f"{label}-source",
        source_locator=f"fixture://{label}/raw",
        sha256=_sha(f"{label}:raw"),
        byte_size=100,
        local_received_at=received_at,
        source_edition=f"{label}-edition",
        evidence_category=label,
    )

    attachment = HistoricalEvidenceAttachment(
        source_locator=f"fixture://{label}/evidence",
        sha256=_sha(f"{label}:attachment"),
        local_received_at=received_at,
        description=f"{label} retrospective fixture evidence",
        source_authority_context="engineering fixture only",
    )

    reference = HistoricalAttachmentReference(
        attachment_id=attachment.identity,
        sha256=attachment.sha256,
    )

    if bounded_start is None and bounded_end is None:
        availability = HistoricalAvailability(
            kind="EXACT",
            exact_at=available_at,
        )
    else:
        assert bounded_start is not None
        assert bounded_end is not None
        availability = HistoricalAvailability(
            kind="BOUNDED_INTERVAL",
            start=bounded_start,
            end=bounded_end,
        )

    evidence = HistoricalAvailabilityEvidence(
        subject_receipt_id=raw.identity,
        subject_sha256=raw.sha256,
        source_edition=raw.source_edition,
        covered_scope=f"{label} exact consumed fixture",
        covered_fields=fields,
        revision_semantics="fixture version is immutable",
        attachments=(reference,),
        availability=availability,
    )

    review = HistoricalSourceReview(
        reviewer="fixture-reviewer",
        reviewed_at=reviewed_at,
        methodology="retrospective fixture review",
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


def _bar(
    market_date: date,
    package: HistoricalEvidencePackage,
    *,
    symbol: str = "AAA",
    provider_symbol: str | None = None,
    instrument_id: UUID = INSTRUMENT,
    semantic_class: DailyBarSemanticClass = (
        DailyBarSemanticClass.VALID_EXECUTABLE
    ),
    adjusted_close: Decimal | None = Decimal("999"),
) -> CanonicalDailyBar:
    provider_symbol = provider_symbol or f"{symbol}.P"

    if semantic_class == DailyBarSemanticClass.VALID_EXECUTABLE:
        values = dict(
            open=Decimal("10"),
            high=Decimal("12"),
            low=Decimal("9"),
            close=Decimal("11"),
            volume=Decimal("100"),
        )
    else:
        values = dict(
            open=None,
            high=None,
            low=None,
            close=None,
            volume=None,
        )

    return CanonicalDailyBar(
        instrument_id=instrument_id,
        canonical_symbol=symbol,
        provider_symbol=provider_symbol,
        market_date=market_date,
        semantic_class=semantic_class,
        provider_adjusted_close_reference=adjusted_close,
        quality_flags=(),
        source_provider="provider",
        source_snapshot_date=market_date,
        source_row_number=1,
        source_sha256=package.raw_receipt.sha256,
        **values,
    )


def _daily(
    market_date: date,
    *,
    symbol: str = "AAA",
    provider_symbol: str | None = None,
    available_at: datetime = EARLY,
    label: str | None = None,
    semantic_class: DailyBarSemanticClass = (
        DailyBarSemanticClass.VALID_EXECUTABLE
    ),
) -> HistoricalDailyObservation:
    label = label or f"daily-{market_date}-{symbol}"
    package = _package(
        label,
        DAILY_EVIDENCE_FIELDS,
        available_at=available_at,
    )
    return HistoricalDailyObservation(
        row=_bar(
            market_date,
            package,
            symbol=symbol,
            provider_symbol=provider_symbol,
            semantic_class=semantic_class,
        ),
        evidence=package,
    )


def _universe(
    market_date: date,
    *,
    symbol: str = "AAA",
    eligible: bool = True,
    instrument_id: UUID = INSTRUMENT,
    available_at: datetime = EARLY,
    label: str | None = None,
    members: tuple[UniverseMember, ...] | None = None,
) -> HistoricalUniverseSnapshot:
    label = label or f"universe-{market_date}-{symbol}"

    if members is None:
        members = (
            UniverseMember(
                instrument_id=instrument_id,
                symbol=symbol,
                eligible=eligible,
            ),
        )

    return HistoricalUniverseSnapshot(
        universe=UniverseEvidence(
            contract="egx-universe-v1",
            market="EGX",
            effective_date=market_date,
            published_at=EARLY,
            complete=True,
            members=members,
        ),
        evidence=_package(
            label,
            UNIVERSE_EVIDENCE_FIELDS,
            available_at=available_at,
        ),
    )


def _identity(
    market_date: date,
    *,
    symbol: str = "AAA",
    provider_symbol: str | None = None,
    instrument_id: UUID = INSTRUMENT,
    available_at: datetime = EARLY,
    label: str | None = None,
) -> HistoricalIdentityMapping:
    provider_symbol = provider_symbol or f"{symbol}.P"
    label = label or f"identity-{market_date}-{symbol}-{instrument_id}"

    return HistoricalIdentityMapping(
        market_date=market_date,
        instrument_id=instrument_id,
        canonical_symbol=symbol,
        provider_symbol=provider_symbol,
        source_provider="provider",
        evidence=_package(
            label,
            IDENTITY_EVIDENCE_FIELDS,
            available_at=available_at,
            provider="provider",
        ),
    )


def _session(
    market_date: date,
    *,
    market_state: str = "TRADING_SESSION",
    instrument_state: str = "EXPECTED_OBSERVATION",
    available_at: datetime = EARLY,
    label: str | None = None,
    package: HistoricalEvidencePackage | None = None,
) -> HistoricalSessionRecord:
    label = label or f"session-{market_date}-{market_state}-{instrument_state}"
    package = package or _package(
        label,
        SESSION_EVIDENCE_FIELDS,
        available_at=available_at,
    )
    return HistoricalSessionRecord(
        market_date=market_date,
        market_state=market_state,
        instrument_state=instrument_state,
        evidence=package,
    )


def _coverage(
    *,
    start: date = D1,
    end: date = D3,
    rows: tuple[ActionEvidenceRow, ...] = (),
    available_at: datetime = EARLY,
    label: str = "actions",
    package: HistoricalEvidencePackage | None = None,
) -> HistoricalActionCoverage:
    package = package or _package(
        label,
        ACTION_EVIDENCE_FIELDS,
        available_at=available_at,
    )

    return HistoricalActionCoverage(
        actions=ActionEvidence(
            contract="egx-actions-v1",
            instrument_id=INSTRUMENT,
            coverage_start=start,
            coverage_end=end,
            published_at=EARLY,
            complete=True,
            actions=rows,
        ),
        evidence=package,
    )


def _source(
    *,
    symbols: dict[date, str] | None = None,
    sessions: tuple[HistoricalSessionRecord, ...] | None = None,
    observations: tuple[HistoricalDailyObservation, ...] | None = None,
    universes: tuple[HistoricalUniverseSnapshot, ...] | None = None,
    identities: tuple[HistoricalIdentityMapping, ...] | None = None,
    action_coverage: HistoricalActionCoverage | None = None,
) -> HistoricalPITInput:
    symbols = symbols or {
        D1: "AAA",
        D2: "AAA",
    }

    sessions = sessions or (
        _session(D1),
        _session(D2),
        _session(
            D3,
            market_state="NON_SESSION",
            instrument_state="NOT_APPLICABLE",
        ),
    )

    if observations is None:
        observations = tuple(
            _daily(day, symbol=symbols[day])
            for day in (D1, D2)
            if any(
                session.market_date == day
                and session.instrument_state
                == "EXPECTED_OBSERVATION"
                for session in sessions
            )
        )

    if universes is None:
        universes = tuple(
            _universe(day, symbol=symbols[day])
            for day in (D1, D2)
            if any(
                session.market_date == day
                and session.market_state == "TRADING_SESSION"
                for session in sessions
            )
        )

    if identities is None:
        identities = tuple(
            _identity(day, symbol=symbols[day])
            for day in (D1, D2)
            if any(
                session.market_date == day
                and session.market_state == "TRADING_SESSION"
                for session in sessions
            )
        )

    return HistoricalPITInput(
        observations=observations,
        universes=universes,
        identities=identities,
        sessions=sessions,
        action_coverage=action_coverage or _coverage(),
    )


def _clone(
    source: HistoricalPITInput,
    *,
    observations=None,
    universes=None,
    identities=None,
    sessions=None,
    action_coverage=None,
) -> HistoricalPITInput:
    return HistoricalPITInput(
        observations=(
            source.observations
            if observations is None
            else observations
        ),
        universes=(
            source.universes
            if universes is None
            else universes
        ),
        identities=(
            source.identities
            if identities is None
            else identities
        ),
        sessions=(
            source.sessions
            if sessions is None
            else sessions
        ),
        action_coverage=(
            source.action_coverage
            if action_coverage is None
            else action_coverage
        ),
    )


def _derive(
    source: HistoricalPITInput,
    *,
    decision_at: datetime = DECISION,
    research_built_at: datetime = BUILD,
) -> ResearchPITDaily:
    return derive_research_pit_daily(
        source,
        instrument_id=INSTRUMENT,
        start_date=D1,
        decision_date=D3,
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


def test_valid_small_derivation_and_no_action_complete_coverage():
    result = _derive(_source())

    assert type(result) is ResearchPITDaily
    assert result.instrument_id == INSTRUMENT
    assert result.canonical_symbol == "AAA"
    assert tuple(row.market_date for row in result.raw_rows) == (
        D1,
        D2,
    )
    assert tuple(row.market_date for row in result.indicator_rows) == (
        D1,
        D2,
    )
    assert all(
        row.transformation_version == TRANSFORMATION_VERSION
        for row in result.indicator_rows
    )
    assert all(row.event_ids == () for row in result.indicator_rows)


def test_exact_availability_cutoff_equality_passes():
    source = _source()

    equal_package = _package(
        "session-d1-cutoff-equality",
        SESSION_EVIDENCE_FIELDS,
        available_at=DECISION,
    )

    replacement = _session(
        D1,
        package=equal_package,
    )

    sessions = tuple(
        replacement if item.market_date == D1 else item
        for item in source.sessions
    )

    result = _derive(_clone(source, sessions=sessions))
    assert result.raw_rows[0].market_date == D1


def test_late_local_receipt_and_late_review_are_allowed():
    source = _source()

    package = _package(
        "late-local-session",
        SESSION_EVIDENCE_FIELDS,
        available_at=EARLY,
        received_at=DECISION + timedelta(days=1),
        reviewed_at=DECISION + timedelta(days=2),
    )

    replacement = _session(D1, package=package)
    sessions = tuple(
        replacement if item.market_date == D1 else item
        for item in source.sessions
    )

    result = _derive(_clone(source, sessions=sessions))
    assert result.instrument_id == INSTRUMENT


def test_future_candidate_facts_are_excluded():
    source = _source()
    baseline = _derive(source)

    future_daily = _daily(
        D1,
        symbol="ZZZ",
        available_at=FUTURE,
        label="future-daily",
    )

    future_universe = _universe(
        D1,
        symbol="ZZZ",
        available_at=FUTURE,
        label="future-universe",
    )

    future_identity = _identity(
        D1,
        symbol="ZZZ",
        available_at=FUTURE,
        label="future-identity",
    )

    future_session = _session(
        D1,
        available_at=FUTURE,
        label="future-session",
    )

    expanded = _clone(
        source,
        observations=source.observations + (future_daily,),
        universes=source.universes + (future_universe,),
        identities=source.identities + (future_identity,),
        sessions=source.sessions + (future_session,),
    )

    result = _derive(expanded)

    assert result.derivation_id == baseline.derivation_id
    assert result.used_evidence_ids == baseline.used_evidence_ids
    assert result.raw_rows == baseline.raw_rows
    assert result.indicator_rows == baseline.indicator_rows


def test_changing_future_fact_does_not_change_derivation():
    source = _source()
    baseline = _derive(source)

    first = _identity(
        D1,
        symbol="ZZZ",
        provider_symbol="ZZZ.P",
        available_at=FUTURE,
        label="future-identity-a",
    )
    second = _identity(
        D1,
        symbol="YYY",
        provider_symbol="YYY.P",
        available_at=FUTURE,
        label="future-identity-b",
    )

    a = _derive(
        _clone(
            source,
            identities=source.identities + (first,),
        )
    )
    b = _derive(
        _clone(
            source,
            identities=source.identities + (second,),
        )
    )

    assert a.derivation_id == baseline.derivation_id
    assert b.derivation_id == baseline.derivation_id


def test_future_fact_cannot_repair_missing_historical_coverage():
    source = _source()

    sessions = tuple(
        row for row in source.sessions
        if row.market_date != D2
    ) + (
        _session(
            D2,
            available_at=FUTURE,
            label="future-repair-session",
        ),
    )

    with pytest.raises(ValueError, match="session record required"):
        _derive(_clone(source, sessions=sessions))


def test_bounded_interval_crossing_cutoff_is_unusable():
    source = _source()

    package = _package(
        "crossing-session",
        SESSION_EVIDENCE_FIELDS,
        bounded_start=EARLY,
        bounded_end=FUTURE,
    )
    replacement = _session(D1, package=package)

    sessions = tuple(
        replacement if row.market_date == D1 else row
        for row in source.sessions
    )

    with pytest.raises(ValueError, match="session record required"):
        _derive(_clone(source, sessions=sessions))


def test_corrupt_r1_1_package_fails_even_when_nested():
    source = _source()
    original = source.sessions[0]

    corrupt_review = original.evidence.review.model_copy(
        update={"approved": False}
    )
    corrupt_package = original.evidence.model_copy(
        update={"review": corrupt_review}
    )
    corrupt_session = original.model_copy(
        update={"evidence": corrupt_package}
    )
    corrupt_source = source.model_copy(
        update={
            "sessions": (
                corrupt_session,
                *source.sessions[1:],
            )
        }
    )

    with pytest.raises(ValueError):
        _derive(corrupt_source)


def test_chronological_order_is_canonical_and_input_order_irrelevant():
    source = _source()

    reversed_source = HistoricalPITInput(
        observations=tuple(reversed(source.observations)),
        universes=tuple(reversed(source.universes)),
        identities=tuple(reversed(source.identities)),
        sessions=tuple(reversed(source.sessions)),
        action_coverage=source.action_coverage,
    )

    a = _derive(source)
    b = _derive(reversed_source)

    assert tuple(row.market_date for row in b.raw_rows) == (D1, D2)
    assert a.derivation_id == b.derivation_id


def test_duplicate_admitted_daily_date_fails():
    source = _source()

    duplicate = _daily(
        D1,
        symbol="AAA",
        label="daily-d1-second-edition",
    )

    with pytest.raises(
        ValueError,
        match="duplicate/conflicting admitted daily observation",
    ):
        _derive(
            _clone(
                source,
                observations=source.observations + (duplicate,),
            )
        )


def test_quarantined_daily_row_fails():
    source = _source()

    bad = _daily(
        D1,
        semantic_class=DailyBarSemanticClass.QUARANTINED_ANOMALY,
        label="quarantined-d1",
    )

    observations = tuple(
        bad if row.row.market_date == D1 else row
        for row in source.observations
    )

    with pytest.raises(ValueError, match="quarantined"):
        _derive(_clone(source, observations=observations))


def test_provider_adjusted_close_never_replaces_raw_indicator_price():
    source = _source()
    result = _derive(source)

    raw = result.raw_rows[0]
    adjusted = result.indicator_rows[0]

    assert raw.provider_adjusted_close_reference == Decimal("999")
    assert raw.close == Decimal("11")
    assert adjusted.close == Decimal("11")
    assert adjusted.close != raw.provider_adjusted_close_reference


def test_raw_input_rows_are_unchanged_after_derivation():
    source = _source()
    before = tuple(
        row.row.model_dump(mode="json")
        for row in source.observations
    )

    _derive(source)

    after = tuple(
        row.row.model_dump(mode="json")
        for row in source.observations
    )

    assert before == after


def test_exact_dated_universe_is_required():
    source = _source()
    universes = tuple(
        row for row in source.universes
        if row.universe.effective_date != D1
    )

    with pytest.raises(ValueError, match="dated universe required"):
        _derive(_clone(source, universes=universes))


def test_absent_universe_member_fails():
    source = _source()

    replacement = _universe(
        D1,
        members=(
            UniverseMember(
                instrument_id=OTHER_INSTRUMENT,
                symbol="OTHER",
                eligible=True,
            ),
        ),
        label="absent-target",
    )

    universes = tuple(
        replacement
        if row.universe.effective_date == D1
        else row
        for row in source.universes
    )

    with pytest.raises(ValueError, match="absent"):
        _derive(_clone(source, universes=universes))


def test_ineligible_universe_member_fails():
    source = _source()

    replacement = _universe(
        D1,
        eligible=False,
        label="ineligible-target",
    )

    universes = tuple(
        replacement
        if row.universe.effective_date == D1
        else row
        for row in source.universes
    )

    with pytest.raises(ValueError, match="ineligible"):
        _derive(_clone(source, universes=universes))


def test_conflicting_admitted_same_date_universe_fails():
    source = _source()

    conflicting = _universe(
        D1,
        members=(
            UniverseMember(
                instrument_id=INSTRUMENT,
                symbol="AAA",
                eligible=True,
            ),
            UniverseMember(
                instrument_id=OTHER_INSTRUMENT,
                symbol="OTHER",
                eligible=True,
            ),
        ),
        label="second-admitted-universe",
    )

    with pytest.raises(ValueError, match="dated universe required"):
        _derive(
            _clone(
                source,
                universes=source.universes + (conflicting,),
            )
        )


def test_later_universe_removal_does_not_change_past_derivation():
    source = _source()
    baseline = _derive(source)

    future_removal = _universe(
        D1,
        members=(),
        available_at=FUTURE,
        label="future-removal",
    )

    result = _derive(
        _clone(
            source,
            universes=source.universes + (future_removal,),
        )
    )

    assert result.derivation_id == baseline.derivation_id


def test_exact_identity_is_required():
    source = _source()

    identities = tuple(
        row for row in source.identities
        if row.market_date != D1
    )

    with pytest.raises(ValueError, match="dated identity required"):
        _derive(_clone(source, identities=identities))


def test_historical_symbol_change_same_instrument_is_supported():
    source = _source(
        symbols={
            D1: "AAA",
            D2: "BBB",
        }
    )

    result = _derive(source)

    assert tuple(row.canonical_symbol for row in result.raw_rows) == (
        "AAA",
        "BBB",
    )
    assert result.canonical_symbol == "BBB"


def test_ambiguous_provider_symbol_mapping_fails():
    source = _source()

    ambiguous = _identity(
        D1,
        symbol="OTHER",
        provider_symbol="AAA.P",
        instrument_id=OTHER_INSTRUMENT,
        label="ambiguous-provider-symbol",
    )

    with pytest.raises(ValueError, match="ambiguous"):
        _derive(
            _clone(
                source,
                identities=source.identities + (ambiguous,),
            )
        )


def test_daily_provider_symbol_mismatch_fails():
    source = _source()

    bad = _daily(
        D1,
        symbol="AAA",
        provider_symbol="WRONG.P",
        label="wrong-provider-symbol",
    )

    observations = tuple(
        bad if row.row.market_date == D1 else row
        for row in source.observations
    )

    with pytest.raises(ValueError, match="provider symbol mismatch"):
        _derive(_clone(source, observations=observations))


def test_future_symbol_mapping_does_not_rewrite_past():
    source = _source()
    baseline = _derive(source)

    future = _identity(
        D1,
        symbol="ZZZ",
        provider_symbol="ZZZ.P",
        available_at=FUTURE,
        label="future-symbol",
    )

    result = _derive(
        _clone(
            source,
            identities=source.identities + (future,),
        )
    )

    assert result.derivation_id == baseline.derivation_id
    assert result.raw_rows[0].canonical_symbol == "AAA"


def test_exact_action_coverage_is_required():
    source = _source()

    bad_coverage = _coverage(
        start=D1,
        end=D2,
        label="short-actions",
    )

    with pytest.raises(ValueError, match="coverage end"):
        _derive(
            _clone(
                source,
                action_coverage=bad_coverage,
            )
        )


def test_split_math_matches_operational_m3_semantics():
    split = ActionEvidenceRow(
        event_id="split-2-for-1",
        effective_date=D2,
        action_type="SPLIT",
        new_shares=Decimal("2"),
        old_shares=Decimal("1"),
        details="fixture 2-for-1 split",
    )

    source = _source(
        action_coverage=_coverage(rows=(split,))
    )

    result = _derive(source)

    first, second = result.indicator_rows

    assert first.market_date == D1
    assert first.price_factor == Decimal("0.5")
    assert first.volume_factor == Decimal("2")
    assert first.open == Decimal("5")
    assert first.high == Decimal("6")
    assert first.low == Decimal("4.5")
    assert first.close == Decimal("5.5")
    assert first.volume == Decimal("200")
    assert first.event_ids == ("split-2-for-1",)

    assert second.market_date == D2
    assert second.price_factor == Decimal("1")
    assert second.volume_factor == Decimal("1")
    assert second.open == Decimal("10")
    assert second.close == Decimal("11")
    assert second.volume == Decimal("100")
    assert second.event_ids == ()


def test_action_outside_declared_coverage_is_rejected_by_reference_contract():
    outside = ActionEvidenceRow(
        event_id="outside",
        effective_date=D3 + timedelta(days=1),
        action_type="SPLIT",
        new_shares=Decimal("2"),
        old_shares=Decimal("1"),
        details="outside coverage fixture",
    )

    with pytest.raises(ValueError, match="outside declared coverage"):
        ActionEvidence(
            contract="egx-actions-v1",
            instrument_id=INSTRUMENT,
            coverage_start=D1,
            coverage_end=D3,
            published_at=EARLY,
            complete=True,
            actions=(outside,),
        )


def test_non_split_relevant_action_fails_closed():
    dividend = ActionEvidenceRow(
        event_id="dividend",
        effective_date=D2,
        action_type="DIVIDEND",
        details="fixture cash dividend",
    )

    source = _source(
        action_coverage=_coverage(rows=(dividend,))
    )

    with pytest.raises(ValueError, match="unsupported corporate action"):
        _derive(source)


def test_duplicate_action_event_identity_is_rejected():
    first = ActionEvidenceRow(
        event_id="same-event",
        effective_date=D1,
        action_type="SPLIT",
        new_shares=Decimal("2"),
        old_shares=Decimal("1"),
        details="first",
    )
    second = ActionEvidenceRow(
        event_id="same-event",
        effective_date=D2,
        action_type="SPLIT",
        new_shares=Decimal("3"),
        old_shares=Decimal("1"),
        details="second",
    )

    with pytest.raises(ValueError, match="duplicate/ambiguous"):
        ActionEvidence(
            contract="egx-actions-v1",
            instrument_id=INSTRUMENT,
            coverage_start=D1,
            coverage_end=D3,
            published_at=EARLY,
            complete=True,
            actions=(first, second),
        )


def test_exact_session_record_every_calendar_date_is_required():
    source = _source()

    sessions = tuple(
        row for row in source.sessions
        if row.market_date != D3
    )

    with pytest.raises(ValueError, match="session record required"):
        _derive(_clone(source, sessions=sessions))


def test_non_session_requires_no_daily_bar():
    result = _derive(_source())
    assert D3 not in {row.market_date for row in result.raw_rows}


def test_daily_bar_on_non_session_fails():
    source = _source()

    extra = _daily(
        D3,
        label="bar-on-non-session",
    )

    with pytest.raises(ValueError, match="NON_SESSION"):
        _derive(
            _clone(
                source,
                observations=source.observations + (extra,),
            )
        )


def test_expected_observation_requires_bar():
    source = _source()

    observations = tuple(
        row for row in source.observations
        if row.row.market_date != D2
    )

    with pytest.raises(ValueError, match="requires daily bar"):
        _derive(_clone(source, observations=observations))


def test_explicit_suspended_session_allows_no_bar():
    sessions = (
        _session(D1),
        _session(
            D2,
            instrument_state="SUSPENDED",
        ),
        _session(
            D3,
            market_state="NON_SESSION",
            instrument_state="NOT_APPLICABLE",
        ),
    )

    source = _source(
        sessions=sessions,
        observations=(
            _daily(D1),
        ),
    )

    result = _derive(source)

    assert tuple(row.market_date for row in result.raw_rows) == (D1,)


def test_unsupported_session_fails_closed():
    sessions = (
        _session(D1),
        _session(
            D2,
            instrument_state="UNSUPPORTED",
        ),
        _session(
            D3,
            market_state="NON_SESSION",
            instrument_state="NOT_APPLICABLE",
        ),
    )

    source = _source(sessions=sessions)

    with pytest.raises(ValueError, match="unsupported session"):
        _derive(source)


def test_post_decision_session_evidence_cannot_repair_coverage():
    source = _source()

    sessions = tuple(
        row for row in source.sessions
        if row.market_date != D1
    ) + (
        _session(
            D1,
            available_at=FUTURE,
            label="post-decision-session",
        ),
    )

    with pytest.raises(ValueError, match="session record required"):
        _derive(_clone(source, sessions=sessions))


def test_no_synthetic_exchange_hours_exist():
    assert "opens_at" not in HistoricalSessionRecord.model_fields
    assert "closes_at" not in HistoricalSessionRecord.model_fields
    assert "session_open" not in HistoricalSessionRecord.model_fields
    assert "session_close" not in HistoricalSessionRecord.model_fields

    result = _derive(_source())
    dumped = result.model_dump(mode="json")
    assert "opens_at" not in str(dumped)
    assert "closes_at" not in str(dumped)


def test_dict_substitution_fails():
    source = _source()
    observation = source.observations[0]

    with pytest.raises(ValueError, match="CanonicalDailyBar"):
        HistoricalDailyObservation(
            row=observation.row.model_dump(),
            evidence=observation.evidence,
        )


def test_list_substitution_fails():
    source = _source()

    with pytest.raises(ValueError, match="tuple"):
        HistoricalPITInput(
            observations=list(source.observations),
            universes=source.universes,
            identities=source.identities,
            sessions=source.sessions,
            action_coverage=source.action_coverage,
        )


def test_model_construct_cannot_bypass_boundary():
    source = _source()

    forged = HistoricalPITInput.model_construct(
        schema_version="historical-pit-input-v1",
        observations=list(source.observations),
        universes=source.universes,
        identities=source.identities,
        sessions=source.sessions,
        action_coverage=source.action_coverage,
    )

    with pytest.raises(ValueError):
        _derive(forged)


def test_nested_model_copy_corruption_fails():
    source = _source()
    original = source.observations[0]

    corrupt_row = original.row.model_copy(
        update={"source_provider": "PROVIDER"}
    )
    corrupt_observation = original.model_copy(
        update={"row": corrupt_row}
    )
    corrupt_source = source.model_copy(
        update={
            "observations": (
                corrupt_observation,
                *source.observations[1:],
            )
        }
    )

    with pytest.raises(ValueError, match="noncanonical"):
        _derive(corrupt_source)


def test_meaningful_used_evidence_change_changes_derivation_id():
    source = _source()
    baseline = _derive(source)

    replacement = _session(
        D1,
        package=_package(
            "different-used-session-evidence",
            SESSION_EVIDENCE_FIELDS,
        ),
    )

    sessions = tuple(
        replacement if row.market_date == D1 else row
        for row in source.sessions
    )

    changed = _derive(_clone(source, sessions=sessions))

    assert changed.derivation_id != baseline.derivation_id


def test_research_build_clock_does_not_change_derivation_id():
    source = _source()

    first = _derive(source, research_built_at=BUILD)
    second = _derive(
        source,
        research_built_at=BUILD + timedelta(days=30),
    )

    assert first.research_built_at != second.research_built_at
    assert first.derivation_id == second.derivation_id


def test_input_models_remain_immutable_and_unchanged():
    source = _source()
    before = source.model_dump(mode="json")

    _derive(source)

    assert source.model_dump(mode="json") == before

    with pytest.raises(Exception):
        source.sessions = ()


def test_missing_required_evidence_field_coverage_fails():
    source = _source()

    incomplete_fields = tuple(
        field
        for field in SESSION_EVIDENCE_FIELDS
        if field != "instrument_state"
    )

    replacement = _session(
        D1,
        package=_package(
            "missing-session-field",
            incomplete_fields,
        ),
    )

    sessions = tuple(
        replacement if row.market_date == D1 else row
        for row in source.sessions
    )

    with pytest.raises(ValueError, match="does not cover required fields"):
        _derive(_clone(source, sessions=sessions))


def test_dependency_received_after_research_build_fails():
    source = _source()

    package = _package(
        "received-after-build",
        SESSION_EVIDENCE_FIELDS,
        available_at=EARLY,
        received_at=BUILD + timedelta(days=1),
        reviewed_at=EARLY,
    )

    replacement = _session(D1, package=package)
    sessions = tuple(
        replacement if row.market_date == D1 else row
        for row in source.sessions
    )

    with pytest.raises(ValueError, match="raw receipt after research build"):
        _derive(_clone(source, sessions=sessions))


def test_future_corrupt_package_still_fails_canonical_validation():
    source = _source()

    future = _identity(
        D1,
        symbol="ZZZ",
        available_at=FUTURE,
        label="future-corrupt",
    )

    corrupt_review = future.evidence.review.model_copy(
        update={"approved": False}
    )
    corrupt_package = future.evidence.model_copy(
        update={"review": corrupt_review}
    )
    corrupt_future = future.model_copy(
        update={"evidence": corrupt_package}
    )

    forged = source.model_copy(
        update={
            "identities": source.identities + (corrupt_future,)
        }
    )

    with pytest.raises(ValueError):
        _derive(forged)


def test_daily_evidence_must_bind_exact_source_hash():
    source = _source()
    original = source.observations[0]

    wrong_package = _package(
        "wrong-daily-artifact",
        DAILY_EVIDENCE_FIELDS,
    )

    wrong = HistoricalDailyObservation(
        row=original.row,
        evidence=wrong_package,
    )

    observations = tuple(
        wrong
        if row.row.market_date == D1
        else row
        for row in source.observations
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        _derive(_clone(source, observations=observations))


def test_identity_evidence_provider_must_match_identity_provider():
    source = _source()

    bad = HistoricalIdentityMapping(
        market_date=D1,
        instrument_id=INSTRUMENT,
        canonical_symbol="AAA",
        provider_symbol="AAA.P",
        source_provider="provider",
        evidence=_package(
            "wrong-identity-provider",
            IDENTITY_EVIDENCE_FIELDS,
            provider="different-provider",
        ),
    )

    identities = tuple(
        bad if row.market_date == D1 else row
        for row in source.identities
    )

    with pytest.raises(ValueError, match="identity evidence provider mismatch"):
        _derive(_clone(source, identities=identities))


def test_research_build_cannot_precede_decision():
    with pytest.raises(ValueError, match="cannot precede decision"):
        _derive(
            _source(),
            research_built_at=DECISION - timedelta(seconds=1),
        )


def test_output_is_not_operational_m3_dataset(monkeypatch):
    from app.data.point_in_time import (
        PointInTimeDailyDataset,
        PointInTimeDailyRepository,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("operational M3 load must not be called")

    monkeypatch.setattr(
        PointInTimeDailyRepository,
        "load",
        forbidden,
    )

    result = _derive(_source())

    assert not isinstance(result, PointInTimeDailyDataset)
    assert type(result) is ResearchPITDaily
    assert all(type(row) is CanonicalDailyBar for row in result.raw_rows)
    assert all(
        type(row) is ResearchIndicatorBar
        for row in result.indicator_rows
    )


def test_module_has_no_network_provider_storage_or_db_dependency():
    import app.research.historical_pit as historical_pit

    path = Path(historical_pit.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    forbidden_prefixes = (
        "app.storage",
        "app.providers",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "sqlite3",
        "sqlalchemy",
    )

    assert not any(
        module.startswith(forbidden_prefixes)
        for module in imports
    )

    assert "PointInTimeDailyRepository" not in source
    assert "SecurityMasterRepository" not in source


def test_output_surface_contains_no_strategy_execution_or_m8_objects():
    result = _derive(_source())

    assert type(result) is ResearchPITDaily
    assert {type(row) for row in result.raw_rows} == {
        CanonicalDailyBar
    }
    assert {type(row) for row in result.indicator_rows} == {
        ResearchIndicatorBar
    }

    module_names = {
        type(result).__module__,
        *(type(row).__module__ for row in result.raw_rows),
        *(type(row).__module__ for row in result.indicator_rows),
    }

    assert "app.paper" not in module_names
    assert "app.performance" not in module_names
    assert "app.risk" not in module_names


def test_daily_reference_and_quality_fields_require_r1_1_coverage():
    source = _source()

    incomplete_fields = tuple(
        field
        for field in DAILY_EVIDENCE_FIELDS
        if field != "provider_adjusted_close_reference"
    )

    package = _package(
        "daily-missing-adjusted-reference-coverage",
        incomplete_fields,
    )

    original = source.observations[0]
    replacement = HistoricalDailyObservation(
        row=CanonicalDailyBar(
            **{
                name: getattr(original.row, name)
                for name in CanonicalDailyBar.model_fields
                if name != "source_sha256"
            },
            source_sha256=package.raw_receipt.sha256,
        ),
        evidence=package,
    )

    observations = tuple(
        replacement
        if row.row.market_date == D1
        else row
        for row in source.observations
    )

    with pytest.raises(
        ValueError,
        match="does not cover required fields",
    ):
        _derive(
            _clone(
                source,
                observations=observations,
            )
        )


def test_universe_member_order_is_semantically_irrelevant():
    target = UniverseMember(
        instrument_id=INSTRUMENT,
        symbol="AAA",
        eligible=True,
    )
    other = UniverseMember(
        instrument_id=OTHER_INSTRUMENT,
        symbol="OTHER",
        eligible=True,
    )

    first_d1 = _universe(
        D1,
        members=(target, other),
        label="ordered-universe-d1",
    )
    second_d1 = _universe(
        D1,
        members=(other, target),
        label="ordered-universe-d1",
    )

    d2 = _universe(
        D2,
        symbol="AAA",
    )

    first_source = _source(
        universes=(first_d1, d2),
    )
    second_source = _source(
        universes=(second_d1, d2),
    )

    first = _derive(first_source)
    second = _derive(second_source)

    assert (
        first_source.universes[0].universe.members
        == second_source.universes[0].universe.members
    )
    assert first.raw_rows == second.raw_rows
    assert first.indicator_rows == second.indicator_rows
    assert first.used_evidence_ids == second.used_evidence_ids
    assert first.derivation_id == second.derivation_id


def test_multiple_split_order_is_canonical_and_decimal34_deterministic():
    early_split = ActionEvidenceRow(
        event_id="split-d2",
        effective_date=D2,
        action_type="SPLIT",
        new_shares=Decimal("2"),
        old_shares=Decimal("1"),
        details="fixture two-for-one split",
    )
    later_split = ActionEvidenceRow(
        event_id="split-d3",
        effective_date=D3,
        action_type="SPLIT",
        new_shares=Decimal("3"),
        old_shares=Decimal("2"),
        details="fixture three-for-two split",
    )

    forward = _source(
        action_coverage=_coverage(
            rows=(early_split, later_split),
            label="multiple-splits",
        )
    )
    reversed_input = _source(
        action_coverage=_coverage(
            rows=(later_split, early_split),
            label="multiple-splits",
        )
    )

    first = _derive(forward)
    second = _derive(reversed_input)

    assert (
        forward.action_coverage.actions.actions
        == reversed_input.action_coverage.actions.actions
    )

    assert first.indicator_rows == second.indicator_rows
    assert first.used_evidence_ids == second.used_evidence_ids
    assert first.derivation_id == second.derivation_id

    d1, d2 = first.indicator_rows

    with localcontext(Context(prec=34)):
        expected_d1_price = (
            (Decimal("1") / Decimal("2"))
            * (Decimal("2") / Decimal("3"))
        )
        expected_d1_volume = (
            (Decimal("2") / Decimal("1"))
            * (Decimal("3") / Decimal("2"))
        )
        expected_d2_price = (
            Decimal("2") / Decimal("3")
        )
        expected_d2_volume = (
            Decimal("3") / Decimal("2")
        )

    assert d1.event_ids == ("split-d2", "split-d3")
    assert d1.price_factor == expected_d1_price
    assert d1.volume_factor == expected_d1_volume

    assert d2.event_ids == ("split-d3",)
    assert d2.price_factor == expected_d2_price
    assert d2.volume_factor == expected_d2_volume
