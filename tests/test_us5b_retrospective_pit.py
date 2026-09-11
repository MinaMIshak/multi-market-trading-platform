from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal, getcontext
from uuid import uuid4

import pytest

from app.us.contracts import (
    USCorporateActionEvent,
    USCorporateActionType,
    USSecurityType,
)
from app.us.historical_actions import (
    admit_us_corporate_action_history,
)
from app.us.historical_daily import (
    admit_us_daily_bar_history,
)
from app.us.historical_universe import (
    admit_us_universe_history,
)
from app.us.retrospective_pit import (
    US_PIT_TRANSFORMATION,
    USPITExclusionReason,
    USRetrospectivePITDailyDataset,
    build_us_retrospective_pit_daily_dataset,
)

from test_us3_historical_daily import (
    BUILD,
    _bar,
    _bar_package,
    _fact as _daily_fact,
    _listing_history,
    _session_history,
)
from test_us4_historical_actions import (
    _coverage as _action_coverage,
    _fact as _action_fact,
    _package as _action_package,
    _split,
    _symbol_change,
)
from test_us5a_historical_universe import (
    _fact as _universe_fact,
    _member,
    _package as _universe_package,
    _snapshot,
)


INSTRUMENT = uuid4()

START = date(2024, 7, 5)
END = date(2024, 7, 8)

DECISION = datetime(
    2024, 7, 10, 21, 0,
    tzinfo=timezone.utc,
)


def _listing(
    *,
    symbol_5="TEST",
    symbol_8="TEST",
    decision=DECISION,
):
    # Rebuild explicit facts instead of relying on the US3 module's
    # random instrument constant.
    from app.us.contracts import (
        USListingIdentity,
    )
    from app.us.historical_identity import (
        HistoricalUSListingFact,
        US_LISTING_EVIDENCE_FIELDS,
        admit_us_listing_history,
    )
    from test_us3_historical_daily import (
        _package,
    )

    facts = []

    for index, (day, symbol) in enumerate(
        (
            (date(2024, 7, 5), symbol_5),
            (date(2024, 7, 8), symbol_8),
        )
    ):
        listing = USListingIdentity(
            effective_date=day,
            instrument_id=INSTRUMENT,
            canonical_symbol=symbol,
            listing_mic="XNAS",
            security_type=USSecurityType.COMMON_STOCK,
            provider_symbol=symbol,
            source_provider="identity-provider",
            source_instrument_key=(
                f"identity-key-{index}"
            ),
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
            sha_char=(
                "a" if index == 0 else "b"
            ),
        )

        facts.append(
            HistoricalUSListingFact(
                listing=listing,
                evidence_package=package,
            )
        )

    return admit_us_listing_history(
        tuple(facts),
        decision_at=decision,
        research_built_at=BUILD,
    )


def _sessions(
    *,
    decision=DECISION,
):
    # Existing fixture already represents:
    # Jul 5 open, Jul 6/7 closed, Jul 8 open.
    return _session_history(
        decision=decision,
        build=BUILD,
    )


def _daily(
    listing_history,
    session_history,
    *,
    include_5=True,
    include_8=True,
    symbol_5="TEST",
    symbol_8="TEST",
    adjusted_5=Decimal("11"),
    decision=DECISION,
):
    facts = []

    if include_5:
        bar = _bar(
            date(2024, 7, 5),
            instrument_id=INSTRUMENT,
            symbol=symbol_5,
            source_sha="c" * 64,
            adjusted=adjusted_5,
        )

        facts.append(
            _daily_fact(
                date(2024, 7, 5),
                bar=bar,
                package=_bar_package(
                    date(2024, 7, 5),
                    sha_char="c",
                ),
            )
        )

    if include_8:
        bar = _bar(
            date(2024, 7, 8),
            instrument_id=INSTRUMENT,
            symbol=symbol_8,
            source_sha="d" * 64,
        )

        facts.append(
            _daily_fact(
                date(2024, 7, 8),
                bar=bar,
                package=_bar_package(
                    date(2024, 7, 8),
                    sha_char="d",
                ),
            )
        )

    return admit_us_daily_bar_history(
        tuple(facts),
        instrument_id=INSTRUMENT,
        calendar_mic="XNAS",
        coverage_start=START,
        coverage_end=END,
        listing_history=listing_history,
        session_history=session_history,
        decision_at=decision,
        research_built_at=BUILD,
    )


def _universe(
    *,
    eligible_5=True,
    eligible_8=True,
    include_5=True,
    include_8=True,
    member_5=True,
    member_8=True,
    symbol_5="TEST",
    symbol_8="TEST",
    decision=DECISION,
):
    facts = []

    specs = (
        (
            date(2024, 7, 5),
            include_5,
            member_5,
            eligible_5,
            symbol_5,
            "a",
        ),
        (
            date(2024, 7, 8),
            include_8,
            member_8,
            eligible_8,
            symbol_8,
            "b",
        ),
    )

    for (
        day,
        include_snapshot,
        include_member,
        eligible,
        symbol,
        sha_char,
    ) in specs:
        if not include_snapshot:
            continue

        members = (
            (
                _member(
                    instrument_id=INSTRUMENT,
                    symbol=symbol,
                    mic="XNAS",
                    eligible=eligible,
                ),
            )
            if include_member
            else ()
        )

        facts.append(
            _universe_fact(
                snapshot=_snapshot(
                    effective_date=day,
                    members=members,
                ),
                package=_universe_package(
                    available_at=datetime(
                        day.year,
                        day.month,
                        day.day,
                        20,
                        0,
                        tzinfo=timezone.utc,
                    ),
                    sha_char=sha_char,
                ),
            )
        )

    return admit_us_universe_history(
        tuple(facts),
        decision_at=decision,
        research_built_at=BUILD,
    )


def _actions(
    *,
    actions=(),
    decision=DECISION,
):
    fact = _action_fact(
        coverage=_action_coverage(
            start=START,
            end=END,
            instrument_id=INSTRUMENT,
            actions=actions,
        ),
        package=_action_package(
            available_at=datetime(
                2024, 7, 8, 20, 30,
                tzinfo=timezone.utc,
            ),
            sha_char="e",
        ),
    )

    return admit_us_corporate_action_history(
        (fact,),
        instrument_id=INSTRUMENT,
        coverage_start=START,
        coverage_end=END,
        decision_at=decision,
        research_built_at=BUILD,
    )


def _deps(
    *,
    symbol_5="TEST",
    symbol_8="TEST",
    include_bar_5=True,
    include_bar_8=True,
    adjusted_5=Decimal("11"),
    universe_kwargs=None,
    actions=(),
    decision=DECISION,
):
    listing = _listing(
        symbol_5=symbol_5,
        symbol_8=symbol_8,
        decision=decision,
    )

    sessions = _sessions(
        decision=decision,
    )

    daily = _daily(
        listing,
        sessions,
        include_5=include_bar_5,
        include_8=include_bar_8,
        symbol_5=symbol_5,
        symbol_8=symbol_8,
        adjusted_5=adjusted_5,
        decision=decision,
    )

    universe_args = {
        "symbol_5": symbol_5,
        "symbol_8": symbol_8,
        "decision": decision,
    }
    universe_args.update(
        universe_kwargs or {}
    )

    universe = _universe(
        **universe_args,
    )

    action_history = _actions(
        actions=actions,
        decision=decision,
    )

    return {
        "listing_history": listing,
        "session_history": sessions,
        "daily_history": daily,
        "action_history": action_history,
        "universe_history": universe,
    }


def _build(
    *,
    dependencies=None,
    decision=DECISION,
    build=BUILD,
):
    deps = dependencies or _deps(
        decision=decision,
    )

    return build_us_retrospective_pit_daily_dataset(
        instrument_id=INSTRUMENT,
        calendar_mic="XNAS",
        coverage_start=START,
        coverage_end=END,
        decision_at=decision,
        research_built_at=build,
        **deps,
    )


def test_builds_pit_safe_raw_and_indicator_series():
    dataset = _build()

    assert isinstance(
        dataset,
        USRetrospectivePITDailyDataset,
    )

    assert tuple(
        row.market_date
        for row in dataset.rows
    ) == (
        date(2024, 7, 5),
        date(2024, 7, 8),
    )

    assert len(dataset.split_adjusted) == 2
    assert dataset.dq_status == "VALIDATED"
    assert (
        dataset.transformation
        == US_PIT_TRANSFORMATION
    )


def test_closed_weekend_is_explicitly_excluded():
    dataset = _build()

    reasons = {
        item.market_date: item.reason
        for item in dataset.exclusions
    }

    assert reasons[date(2024, 7, 6)] == (
        USPITExclusionReason.CLOSED_SESSION
    )
    assert reasons[date(2024, 7, 7)] == (
        USPITExclusionReason.CLOSED_SESSION
    )


def test_absent_universe_member_is_excluded_not_failed():
    deps = _deps(
        include_bar_8=False,
        universe_kwargs={
            "member_8": False,
        },
    )

    dataset = _build(
        dependencies=deps,
    )

    assert tuple(
        row.market_date
        for row in dataset.rows
    ) == (
        date(2024, 7, 5),
    )

    assert any(
        item.market_date == date(2024, 7, 8)
        and item.reason
        == USPITExclusionReason.NOT_UNIVERSE_MEMBER
        for item in dataset.exclusions
    )


def test_ineligible_member_is_excluded_not_failed():
    deps = _deps(
        include_bar_8=False,
        universe_kwargs={
            "eligible_8": False,
        },
    )

    dataset = _build(
        dependencies=deps,
    )

    assert any(
        item.market_date == date(2024, 7, 8)
        and item.reason
        == (
            USPITExclusionReason
            .INELIGIBLE_UNIVERSE_MEMBER
        )
        for item in dataset.exclusions
    )


def test_missing_exact_universe_snapshot_fails_closed():
    deps = _deps(
        universe_kwargs={
            "include_8": False,
        },
    )

    with pytest.raises(
        ValueError,
        match="no exact-dated US universe snapshot",
    ):
        _build(
            dependencies=deps,
        )


def test_eligible_open_session_missing_bar_fails_closed():
    deps = _deps(
        include_bar_8=False,
    )

    with pytest.raises(
        ValueError,
        match="eligible open session missing",
    ):
        _build(
            dependencies=deps,
        )


def test_universe_symbol_must_match_identity():
    deps = _deps(
        universe_kwargs={
            "symbol_8": "WRONG",
        },
    )

    with pytest.raises(
        ValueError,
        match="universe symbol does not match",
    ):
        _build(
            dependencies=deps,
        )


def test_split_adjusts_only_pre_split_rows():
    split = _split(
        event_id="split-8",
        effective_date=date(2024, 7, 8),
    )

    dataset = _build(
        dependencies=_deps(
            actions=(split,),
        )
    )

    first, second = dataset.split_adjusted

    assert first.market_date == date(
        2024, 7, 5
    )
    assert first.price_factor == Decimal("0.25")
    assert first.volume_factor == Decimal("4")
    assert first.close == Decimal("2.75")
    assert first.volume == Decimal("4000")
    assert first.split_event_ids == ("split-8",)

    assert second.market_date == date(
        2024, 7, 8
    )
    assert second.price_factor == Decimal("1")
    assert second.volume_factor == Decimal("1")
    assert second.split_event_ids == ()


def test_raw_rows_are_never_modified_by_split_transform():
    split = _split(
        event_id="split-8",
        effective_date=date(2024, 7, 8),
    )

    dataset = _build(
        dependencies=_deps(
            actions=(split,),
        )
    )

    assert dataset.rows[0].close == Decimal("11")
    assert dataset.split_adjusted[0].close == Decimal(
        "2.75"
    )


def test_provider_adjusted_close_never_drives_pit_values():
    dataset = _build(
        dependencies=_deps(
            adjusted_5=Decimal("999"),
        )
    )

    assert (
        dataset.rows[0]
        .provider_adjusted_close_reference
        == Decimal("999")
    )

    assert dataset.split_adjusted[0].close == Decimal(
        "11"
    )


def test_cash_dividend_is_nontransforming_on_price_basis_series():
    dividend = USCorporateActionEvent(
        event_id="cash-dividend-8",
        effective_date=date(2024, 7, 8),
        action_type=USCorporateActionType.CASH_DIVIDEND,
        cash_amount=Decimal("0.25"),
        cash_currency="USD",
        details="fixture cash dividend",
    )

    dataset = _build(
        dependencies=_deps(
            actions=(dividend,),
        )
    )

    assert tuple(
        row.close
        for row in dataset.split_adjusted
    ) == tuple(
        row.close
        for row in dataset.rows
    )

    assert all(
        row.price_factor == Decimal("1")
        and row.volume_factor == Decimal("1")
        and row.split_event_ids == ()
        for row in dataset.split_adjusted
    )


@pytest.mark.parametrize(
    "action_type",
    [
        USCorporateActionType.STOCK_DIVIDEND,
        USCorporateActionType.MERGER,
        USCorporateActionType.SPINOFF,
        USCorporateActionType.RIGHTS,
        USCorporateActionType.OTHER,
    ],
)
def test_unsupported_price_semantics_crossing_series_fail_closed(
    action_type,
):
    event = USCorporateActionEvent(
        event_id="unsupported",
        effective_date=date(2024, 7, 8),
        action_type=action_type,
        details="fixture",
    )

    with pytest.raises(
        ValueError,
        match="unsupported corporate action crosses",
    ):
        _build(
            dependencies=_deps(
                actions=(event,),
            )
        )


def test_symbol_change_is_identity_event_not_price_transform():
    change = _symbol_change(
        event_id="symbol-8",
        effective_date=date(2024, 7, 8),
    )

    dataset = _build(
        dependencies=_deps(
            symbol_5="OLD",
            symbol_8="NEW",
            actions=(change,),
        )
    )

    assert dataset.rows[0].canonical_symbol == "OLD"
    assert dataset.rows[1].canonical_symbol == "NEW"

    assert all(
        item.price_factor == Decimal("1")
        for item in dataset.split_adjusted
    )


def test_symbol_change_old_symbol_mismatch_fails():
    change = USCorporateActionEvent(
        event_id="symbol-8",
        effective_date=date(2024, 7, 8),
        action_type=USCorporateActionType.SYMBOL_CHANGE,
        old_symbol="WRONG",
        new_symbol="NEW",
        details="fixture",
    )

    with pytest.raises(
        ValueError,
        match="old_symbol does not match",
    ):
        _build(
            dependencies=_deps(
                symbol_5="OLD",
                symbol_8="NEW",
                actions=(change,),
            )
        )


def test_symbol_change_new_symbol_mismatch_fails():
    change = USCorporateActionEvent(
        event_id="symbol-8",
        effective_date=date(2024, 7, 8),
        action_type=USCorporateActionType.SYMBOL_CHANGE,
        old_symbol="OLD",
        new_symbol="WRONG",
        details="fixture",
    )

    with pytest.raises(
        ValueError,
        match="new_symbol does not match",
    ):
        _build(
            dependencies=_deps(
                symbol_5="OLD",
                symbol_8="NEW",
                actions=(change,),
            )
        )


def test_eligible_observation_after_delisting_fails():
    delisting = USCorporateActionEvent(
        event_id="delist-5",
        effective_date=date(2024, 7, 5),
        action_type=USCorporateActionType.DELISTING,
        details="fixture",
    )

    with pytest.raises(
        ValueError,
        match="observation exists after",
    ):
        _build(
            dependencies=_deps(
                actions=(delisting,),
            )
        )


def test_covered_date_with_no_actions_is_valid():
    dataset = _build(
        dependencies=_deps(
            actions=(),
        )
    )

    assert len(dataset.rows) == 2


def test_dependency_decision_horizons_must_match():
    different = datetime(
        2024, 7, 10, 20, 0,
        tzinfo=timezone.utc,
    )

    deps = _deps(
        decision=different,
    )

    with pytest.raises(
        ValueError,
        match="decision_at must match PIT",
    ):
        _build(
            dependencies=deps,
            decision=DECISION,
        )


def test_dependency_build_cannot_be_after_pit_build():
    later_build = datetime(
        2026, 9, 12, 10, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        ValueError,
        match="built after PIT research build",
    ):
        _build(
            dependencies=_deps(),
            build=datetime(
                2026, 9, 10, 10, 0,
                tzinfo=timezone.utc,
            ),
        )


def test_requested_coverage_cannot_exceed_decision_date():
    deps = _deps()

    with pytest.raises(
        ValueError,
        match="coverage_end cannot exceed",
    ):
        build_us_retrospective_pit_daily_dataset(
            instrument_id=INSTRUMENT,
            calendar_mic="XNAS",
            coverage_start=START,
            coverage_end=date(2024, 7, 11),
            decision_at=DECISION,
            research_built_at=BUILD,
            **deps,
        )


def test_requested_instrument_id_requires_exact_uuid():
    deps = _deps()

    with pytest.raises(
        ValueError,
        match="instrument_id must be exact UUID",
    ):
        build_us_retrospective_pit_daily_dataset(
            instrument_id=str(INSTRUMENT),
            calendar_mic="XNAS",
            coverage_start=START,
            coverage_end=END,
            decision_at=DECISION,
            research_built_at=BUILD,
            **deps,
        )


def test_invalid_calendar_mic_fails():
    deps = _deps()

    with pytest.raises(
        ValueError,
        match="calendar_mic must be canonical",
    ):
        build_us_retrospective_pit_daily_dataset(
            instrument_id=INSTRUMENT,
            calendar_mic="NAS",
            coverage_start=START,
            coverage_end=END,
            decision_at=DECISION,
            research_built_at=BUILD,
            **deps,
        )


def test_ambient_decimal_precision_does_not_change_output():
    split = _split(
        event_id="split-8",
        effective_date=date(2024, 7, 8),
    )

    deps = _deps(
        actions=(split,),
    )

    original = getcontext().prec

    try:
        getcontext().prec = 6
        first = _build(
            dependencies=deps,
        )

        getcontext().prec = 50
        second = _build(
            dependencies=deps,
        )
    finally:
        getcontext().prec = original

    assert first.split_adjusted == second.split_adjusted
    assert first.identity == second.identity


def test_history_identity_is_deterministic():
    first = _build()
    second = _build()

    assert first.rows == second.rows
    assert first.split_adjusted == second.split_adjusted
    assert first.exclusions == second.exclusions
    assert first.identity == second.identity


def test_research_build_is_excluded_from_semantic_identity():
    deps = _deps()

    first = _build(
        dependencies=deps,
        build=BUILD,
    )

    later = datetime(
        2026, 9, 12, 10, 0,
        tzinfo=timezone.utc,
    )

    second = _build(
        dependencies=deps,
        build=later,
    )

    assert first.identity == second.identity


def test_source_history_ids_are_bound_into_dataset():
    deps = _deps()
    dataset = _build(
        dependencies=deps,
    )

    assert (
        dataset.listing_history_id
        == deps["listing_history"].identity
    )
    assert (
        dataset.session_history_id
        == deps["session_history"].identity
    )
    assert (
        dataset.daily_history_id
        == deps["daily_history"].identity
    )
    assert (
        dataset.action_history_id
        == deps["action_history"].identity
    )
    assert (
        dataset.universe_history_id
        == deps["universe_history"].identity
    )
