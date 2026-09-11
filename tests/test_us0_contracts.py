from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.us.contracts import (
    USCorporateActionCoverage,
    USCorporateActionEvent,
    USCorporateActionType,
    USListingIdentity,
    USSecurityType,
    USSessionRecord,
    USSessionState,
    USUniverseMember,
    USUniverseSnapshot,
)


def _identity(
    *,
    instrument_id=None,
    effective_date=date(2024, 1, 2),
    symbol="TEST",
):
    return USListingIdentity(
        effective_date=effective_date,
        instrument_id=instrument_id or uuid4(),
        canonical_symbol=symbol,
        listing_mic="XNAS",
        security_type=USSecurityType.COMMON_STOCK,
        provider_symbol=symbol,
        source_provider="fixture",
        source_instrument_key="stable-provider-key",
        is_primary_listing=True,
    )


def _member(
    *,
    instrument_id=None,
    symbol="TEST",
    mic="XNAS",
):
    return USUniverseMember(
        instrument_id=instrument_id or uuid4(),
        canonical_symbol=symbol,
        listing_mic=mic,
        security_type=USSecurityType.COMMON_STOCK,
        eligible=True,
    )


def test_listing_identity_is_exact_dated_and_stable_id_can_span_symbol_change():
    instrument_id = uuid4()

    old = _identity(
        instrument_id=instrument_id,
        effective_date=date(2023, 1, 3),
        symbol="OLD",
    )
    new = _identity(
        instrument_id=instrument_id,
        effective_date=date(2024, 1, 3),
        symbol="NEW",
    )

    assert old.instrument_id == new.instrument_id
    assert old.canonical_symbol == "OLD"
    assert new.canonical_symbol == "NEW"


def test_listing_identity_does_not_derive_id_from_ticker():
    first = _identity(symbol="SAME")
    second = _identity(symbol="SAME")

    assert first.instrument_id != second.instrument_id


def test_listing_identity_rejects_noncanonical_symbol():
    with pytest.raises(ValidationError):
        _identity(symbol="lower")


def test_listing_identity_requires_four_character_mic():
    with pytest.raises(ValidationError):
        USListingIdentity(
            effective_date=date(2024, 1, 2),
            instrument_id=uuid4(),
            canonical_symbol="TEST",
            listing_mic="NASDAQ",
            security_type=USSecurityType.COMMON_STOCK,
            provider_symbol="TEST",
            source_provider="fixture",
            source_instrument_key="id",
            is_primary_listing=True,
        )


def test_contracts_forbid_extra_fields():
    with pytest.raises(ValidationError):
        USUniverseMember(
            instrument_id=uuid4(),
            canonical_symbol="TEST",
            listing_mic="XNAS",
            security_type=USSecurityType.COMMON_STOCK,
            eligible=True,
            invented_truth=True,
        )


def test_universe_requires_unique_instrument_identity():
    instrument_id = uuid4()

    with pytest.raises(
        ValidationError,
        match="duplicate/conflicting universe instrument identity",
    ):
        USUniverseSnapshot(
            effective_date=date(2024, 1, 2),
            members=(
                _member(
                    instrument_id=instrument_id,
                    symbol="ONE",
                ),
                _member(
                    instrument_id=instrument_id,
                    symbol="TWO",
                ),
            ),
        )


def test_universe_requires_unique_dated_listing_key():
    with pytest.raises(
        ValidationError,
        match="duplicate/conflicting dated listing identity",
    ):
        USUniverseSnapshot(
            effective_date=date(2024, 1, 2),
            members=(
                _member(symbol="DUP", mic="XNAS"),
                _member(symbol="DUP", mic="XNAS"),
            ),
        )


def test_same_symbol_on_different_mics_is_not_silently_collapsed():
    snapshot = USUniverseSnapshot(
        effective_date=date(2024, 1, 2),
        members=(
            _member(symbol="TEST", mic="XNAS"),
            _member(symbol="TEST", mic="XNYS"),
        ),
    )

    assert len(snapshot.members) == 2


def test_closed_session_contains_no_synthetic_hours():
    session = USSessionRecord(
        market_date=date(2024, 1, 1),
        calendar_mic="XNAS",
        state=USSessionState.CLOSED,
    )

    assert session.opens_at_utc is None
    assert session.closes_at_utc is None


def test_closed_session_rejects_hours():
    with pytest.raises(
        ValidationError,
        match="closed session cannot contain",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 1),
            calendar_mic="XNAS",
            state=USSessionState.CLOSED,
            opens_at_utc=datetime(
                2024, 1, 1, 14, 30,
                tzinfo=timezone.utc,
            ),
            closes_at_utc=datetime(
                2024, 1, 1, 21, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_regular_session_requires_explicit_hours():
    with pytest.raises(
        ValidationError,
        match="requires explicit open and close",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 2),
            calendar_mic="XNAS",
            state=USSessionState.REGULAR,
        )


def test_session_requires_close_after_open():
    with pytest.raises(
        ValidationError,
        match="close must be after",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 2),
            calendar_mic="XNAS",
            state=USSessionState.REGULAR,
            opens_at_utc=datetime(
                2024, 1, 2, 21, 0,
                tzinfo=timezone.utc,
            ),
            closes_at_utc=datetime(
                2024, 1, 2, 14, 30,
                tzinfo=timezone.utc,
            ),
        )


def test_session_requires_exact_utc_timestamps():
    eastern = ZoneInfo("America/New_York")

    with pytest.raises(
        ValidationError,
        match="datetime.timezone.utc",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 2),
            calendar_mic="XNAS",
            state=USSessionState.REGULAR,
            opens_at_utc=datetime(
                2024, 1, 2, 9, 30,
                tzinfo=eastern,
            ),
            closes_at_utc=datetime(
                2024, 1, 2, 16, 0,
                tzinfo=eastern,
            ),
        )


def test_session_timestamps_must_resolve_to_market_date():
    with pytest.raises(
        ValidationError,
        match="does not belong to market_date",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 3),
            calendar_mic="XNAS",
            state=USSessionState.REGULAR,
            opens_at_utc=datetime(
                2024, 1, 2, 14, 30,
                tzinfo=timezone.utc,
            ),
            closes_at_utc=datetime(
                2024, 1, 2, 21, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_regular_session_accepts_explicit_winter_utc_times():
    session = USSessionRecord(
        market_date=date(2024, 1, 2),
        calendar_mic="XNAS",
        state=USSessionState.REGULAR,
        opens_at_utc=datetime(
            2024, 1, 2, 14, 30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            2024, 1, 2, 21, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert session.state == USSessionState.REGULAR


def test_regular_session_accepts_explicit_summer_utc_times():
    session = USSessionRecord(
        market_date=date(2024, 7, 1),
        calendar_mic="XNAS",
        state=USSessionState.REGULAR,
        opens_at_utc=datetime(
            2024, 7, 1, 13, 30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            2024, 7, 1, 20, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert session.state == USSessionState.REGULAR


def test_early_close_is_explicit_and_not_fixed_by_model():
    session = USSessionRecord(
        market_date=date(2024, 7, 3),
        calendar_mic="XNAS",
        state=USSessionState.EARLY_CLOSE,
        opens_at_utc=datetime(
            2024, 7, 3, 13, 30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            2024, 7, 3, 17, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert session.state == USSessionState.EARLY_CLOSE


def test_split_requires_explicit_ratio():
    with pytest.raises(
        ValidationError,
        match="split requires explicit",
    ):
        USCorporateActionEvent(
            event_id="split-1",
            effective_date=date(2024, 6, 10),
            action_type=USCorporateActionType.SPLIT,
            details="fixture split",
        )


def test_split_accepts_decimal_ratio():
    event = USCorporateActionEvent(
        event_id="split-1",
        effective_date=date(2024, 6, 10),
        action_type=USCorporateActionType.SPLIT,
        new_shares=Decimal("4"),
        old_shares=Decimal("1"),
        details="fixture 4-for-1 split",
    )

    assert event.new_shares == Decimal("4")
    assert event.old_shares == Decimal("1")


def test_non_split_rejects_split_ratio():
    with pytest.raises(
        ValidationError,
        match="only for explicit splits",
    ):
        USCorporateActionEvent(
            event_id="delist-1",
            effective_date=date(2024, 6, 10),
            action_type=USCorporateActionType.DELISTING,
            new_shares=Decimal("2"),
            old_shares=Decimal("1"),
            details="fixture",
        )


def test_symbol_change_requires_explicit_distinct_symbols():
    event = USCorporateActionEvent(
        event_id="symbol-1",
        effective_date=date(2024, 2, 1),
        action_type=USCorporateActionType.SYMBOL_CHANGE,
        old_symbol="OLD",
        new_symbol="NEW",
        details="fixture symbol change",
    )

    assert event.old_symbol == "OLD"
    assert event.new_symbol == "NEW"


def test_symbol_change_rejects_same_symbol():
    with pytest.raises(
        ValidationError,
        match="distinct symbols",
    ):
        USCorporateActionEvent(
            event_id="symbol-1",
            effective_date=date(2024, 2, 1),
            action_type=USCorporateActionType.SYMBOL_CHANGE,
            old_symbol="SAME",
            new_symbol="SAME",
            details="fixture",
        )


def test_cash_dividend_requires_explicit_usd_amount():
    event = USCorporateActionEvent(
        event_id="dividend-1",
        effective_date=date(2024, 3, 1),
        action_type=USCorporateActionType.CASH_DIVIDEND,
        cash_amount=Decimal("0.25"),
        cash_currency="USD",
        details="fixture cash dividend",
    )

    assert event.cash_amount == Decimal("0.25")


def test_action_coverage_rejects_inverted_range():
    with pytest.raises(
        ValidationError,
        match="invalid corporate-action coverage",
    ):
        USCorporateActionCoverage(
            instrument_id=uuid4(),
            coverage_start=date(2024, 2, 1),
            coverage_end=date(2024, 1, 1),
            actions=(),
        )


def test_action_coverage_rejects_action_outside_range():
    event = USCorporateActionEvent(
        event_id="delist-1",
        effective_date=date(2024, 3, 1),
        action_type=USCorporateActionType.DELISTING,
        details="fixture",
    )

    with pytest.raises(
        ValidationError,
        match="outside declared coverage",
    ):
        USCorporateActionCoverage(
            instrument_id=uuid4(),
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 2, 1),
            actions=(event,),
        )


def test_action_coverage_rejects_duplicate_event_id():
    first = USCorporateActionEvent(
        event_id="event-1",
        effective_date=date(2024, 2, 1),
        action_type=USCorporateActionType.DELISTING,
        details="first",
    )
    second = USCorporateActionEvent(
        event_id="event-1",
        effective_date=date(2024, 2, 2),
        action_type=USCorporateActionType.OTHER,
        details="second",
    )

    with pytest.raises(
        ValidationError,
        match="duplicate corporate-action event_id",
    ):
        USCorporateActionCoverage(
            instrument_id=uuid4(),
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 3, 1),
            actions=(first, second),
        )


def test_multiple_distinct_actions_on_same_date_are_allowed():
    first = USCorporateActionEvent(
        event_id="event-1",
        effective_date=date(2024, 2, 1),
        action_type=USCorporateActionType.OTHER,
        details="first",
    )
    second = USCorporateActionEvent(
        event_id="event-2",
        effective_date=date(2024, 2, 1),
        action_type=USCorporateActionType.DELISTING,
        details="second",
    )

    coverage = USCorporateActionCoverage(
        instrument_id=uuid4(),
        coverage_start=date(2024, 1, 1),
        coverage_end=date(2024, 3, 1),
        actions=(first, second),
    )

    assert len(coverage.actions) == 2


def test_contract_model_is_immutable():
    member = _member()

    with pytest.raises(ValidationError):
        member.eligible = False


def test_no_session_rule_is_inferred_from_weekday():
    # Sunday is allowed as an explicit CLOSED historical fact.
    session = USSessionRecord(
        market_date=date(2024, 1, 7),
        calendar_mic="XNAS",
        state=USSessionState.CLOSED,
    )

    assert session.state == USSessionState.CLOSED


def test_offset_zero_timezone_object_other_than_timezone_utc_is_rejected():
    zero_offset = timezone(timedelta(0), name="ZERO")

    with pytest.raises(
        ValidationError,
        match="datetime.timezone.utc",
    ):
        USSessionRecord(
            market_date=date(2024, 1, 2),
            calendar_mic="XNAS",
            state=USSessionState.REGULAR,
            opens_at_utc=datetime(
                2024, 1, 2, 14, 30,
                tzinfo=zero_offset,
            ),
            closes_at_utc=datetime(
                2024, 1, 2, 21, 0,
                tzinfo=zero_offset,
            ),
        )


def test_session_requires_valid_calendar_mic():
    with pytest.raises(ValidationError):
        USSessionRecord(
            market_date=date(2024, 1, 2),
            calendar_mic="NASDAQ",
            state=USSessionState.REGULAR,
            opens_at_utc=datetime(
                2024, 1, 2, 14, 30,
                tzinfo=timezone.utc,
            ),
            closes_at_utc=datetime(
                2024, 1, 2, 21, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_same_market_date_can_have_separate_calendar_mics():
    xnas = USSessionRecord(
        market_date=date(2024, 1, 2),
        calendar_mic="XNAS",
        state=USSessionState.REGULAR,
        opens_at_utc=datetime(
            2024, 1, 2, 14, 30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            2024, 1, 2, 21, 0,
            tzinfo=timezone.utc,
        ),
    )

    xnys = USSessionRecord(
        market_date=date(2024, 1, 2),
        calendar_mic="XNYS",
        state=USSessionState.REGULAR,
        opens_at_utc=datetime(
            2024, 1, 2, 14, 30,
            tzinfo=timezone.utc,
        ),
        closes_at_utc=datetime(
            2024, 1, 2, 21, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert xnas.calendar_mic == "XNAS"
    assert xnys.calendar_mic == "XNYS"
