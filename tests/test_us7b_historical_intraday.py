"""US7B synthetic ENGINEERING fixtures only; never market evidence."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.data.models import BarGranularity
from app.us.contracts import USSessionState
from app.us.historical_intraday import (
    US_INTRADAY_EVIDENCE_FIELDS,
    AdmittedUSIntradaySession,
    HistoricalUSIntradaySegmentFact,
    USHistoricalIntradayBar,
    USHistoricalIntradaySegment,
    USIntradayCoverageMode,
    admit_us_intraday_session,
)
from app.us.historical_session import admit_us_session_history
from test_us3_historical_daily import (
    BUILD,
    DECISION,
    INSTRUMENT,
    _listing_history,
    _package,
    _session,
)


D = Decimal
DAY = date(2024, 7, 8)

OPEN = datetime(
    2024, 7, 8, 13, 30,
    tzinfo=timezone.utc,
)
CLOSE = datetime(
    2024, 7, 8, 20, 0,
    tzinfo=timezone.utc,
)
EARLY_CLOSE = datetime(
    2024, 7, 8, 17, 0,
    tzinfo=timezone.utc,
)


def _width(granularity):
    return {
        BarGranularity.M1: timedelta(minutes=1),
        BarGranularity.M5: timedelta(minutes=5),
    }[granularity]


def _histories(
    *,
    state=USSessionState.REGULAR,
    decision=DECISION,
    build=BUILD,
    symbol="TEST",
):
    listing = _listing_history(
        dates=(DAY,),
        symbol=symbol,
        decision=decision,
        build=build,
    )

    session = admit_us_session_history(
        (_session(DAY, state=state),),
        calendar_mic="XNAS",
        coverage_start=DAY,
        coverage_end=DAY,
        decision_at=decision,
        research_built_at=build,
    )

    return listing, session


def _bar(
    *,
    sequence,
    start,
    granularity=BarGranularity.M5,
    sha_char="c",
    provenance="fixture-chunk-1",
    symbol="TEST",
    available_at=None,
    source_row_number=None,
    open_="10",
    high="11",
    low="9",
    close="10.5",
):
    end = start + _width(granularity)

    return USHistoricalIntradayBar(
        instrument_id=INSTRUMENT,
        market_date=DAY,
        calendar_mic="XNAS",
        canonical_symbol=symbol,
        provider_symbol=symbol,
        source_instrument_key="intraday-key",
        granularity=granularity,
        session_sequence=sequence,
        interval_start_utc=start,
        interval_end_utc=end,
        available_at_utc=(
            end if available_at is None else available_at
        ),
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D("100"),
        traded_value=D("1000"),
        source_provider="intraday-provider",
        source_row_number=(
            sequence
            if source_row_number is None
            else source_row_number
        ),
        source_sha256=sha_char * 64,
        provenance_id=provenance,
    )


def _bars(
    *,
    start,
    end,
    first_sequence,
    granularity=BarGranularity.M5,
    sha_char="c",
    provenance="fixture-chunk-1",
):
    step = _width(granularity)
    count = (end - start) // step

    return tuple(
        _bar(
            sequence=first_sequence + i,
            start=start + step * i,
            granularity=granularity,
            sha_char=sha_char,
            provenance=provenance,
            source_row_number=i + 1,
        )
        for i in range(count)
    )


def _segment(
    *,
    start,
    end,
    first_sequence,
    granularity=BarGranularity.M5,
    sha_char="c",
    provenance="fixture-chunk-1",
    symbol="TEST",
):
    count = (end - start) // _width(granularity)

    return USHistoricalIntradaySegment(
        instrument_id=INSTRUMENT,
        market_date=DAY,
        calendar_mic="XNAS",
        canonical_symbol=symbol,
        granularity=granularity,
        coverage_start_at_utc=start,
        coverage_end_at_utc=end,
        first_session_sequence=first_sequence,
        last_session_sequence=(
            first_sequence + count - 1
        ),
        complete=True,
        source_provider="intraday-provider",
        source_sha256=sha_char * 64,
        provenance_id=provenance,
    )


def _fact(
    *,
    start=OPEN,
    end=CLOSE,
    first_sequence=1,
    granularity=BarGranularity.M5,
    sha_char="c",
    provenance="fixture-chunk-1",
    row_bars=None,
    covered_fields=None,
    package_available_at=None,
    symbol="TEST",
):
    segment = _segment(
        start=start,
        end=end,
        first_sequence=first_sequence,
        granularity=granularity,
        sha_char=sha_char,
        provenance=provenance,
        symbol=symbol,
    )

    rows = (
        _bars(
            start=start,
            end=end,
            first_sequence=first_sequence,
            granularity=granularity,
            sha_char=sha_char,
            provenance=provenance,
        )
        if row_bars is None
        else row_bars
    )

    package = _package(
        provider="intraday-provider",
        category="US_INTRADAY_BARS",
        covered_fields=(
            US_INTRADAY_EVIDENCE_FIELDS
            if covered_fields is None
            else covered_fields
        ),
        available_at=(
            end
            if package_available_at is None
            else package_available_at
        ),
        sha_char=sha_char,
    )

    return HistoricalUSIntradaySegmentFact(
        segment=segment,
        bars=rows,
        evidence_package=package,
    )


def _admit(
    *facts,
    coverage_mode=USIntradayCoverageMode.FULL_SESSION,
    granularity=BarGranularity.M5,
    listing_history=None,
    session_history=None,
    evidence_cutoff_at=DECISION,
    research_built_at=BUILD,
):
    if (
        listing_history is None
        or session_history is None
    ):
        listing, session = _histories(
            decision=evidence_cutoff_at,
            build=BUILD,
        )

        if listing_history is None:
            listing_history = listing
        if session_history is None:
            session_history = session

    return admit_us_intraday_session(
        tuple(facts),
        instrument_id=INSTRUMENT,
        calendar_mic="XNAS",
        market_date=DAY,
        granularity=granularity,
        coverage_mode=coverage_mode,
        listing_history=listing_history,
        session_history=session_history,
        evidence_cutoff_at=evidence_cutoff_at,
        research_built_at=research_built_at,
    )


def test_full_session_m5_is_admitted():
    result = _admit(_fact())

    assert isinstance(
        result,
        AdmittedUSIntradaySession,
    )
    assert len(result.bars) == 78
    assert result.first_session_sequence == 1
    assert result.last_session_sequence == 78
    assert result.coverage_start_at_utc == OPEN
    assert result.coverage_end_at_utc == CLOSE


def test_multiple_raw_artifacts_can_form_one_session():
    split = datetime(
        2024, 7, 8, 16, 0,
        tzinfo=timezone.utc,
    )

    result = _admit(
        _fact(
            start=OPEN,
            end=split,
            first_sequence=1,
            sha_char="c",
            provenance="chunk-a",
        ),
        _fact(
            start=split,
            end=CLOSE,
            first_sequence=31,
            sha_char="d",
            provenance="chunk-b",
        ),
    )

    assert len(result.facts) == 2
    assert len(result.bars) == 78
    assert result.bars[29].session_sequence == 30
    assert result.bars[30].session_sequence == 31


def test_bounded_window_preserves_real_session_origin():
    start = datetime(
        2024, 7, 8, 14, 0,
        tzinfo=timezone.utc,
    )
    end = start + timedelta(minutes=15)

    result = _admit(
        _fact(
            start=start,
            end=end,
            first_sequence=7,
        ),
        coverage_mode=(
            USIntradayCoverageMode.BOUNDED_WINDOW
        ),
    )

    assert tuple(
        row.session_sequence
        for row in result.bars
    ) == (7, 8, 9)


def test_bounded_window_cannot_fake_sequence_one():
    start = datetime(
        2024, 7, 8, 14, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        ValueError,
        match="session origin",
    ):
        _admit(
            _fact(
                start=start,
                end=start + timedelta(minutes=15),
                first_sequence=1,
            ),
            coverage_mode=(
                USIntradayCoverageMode.BOUNDED_WINDOW
            ),
        )


def test_full_session_cannot_be_truncated():
    with pytest.raises(
        ValueError,
        match="FULL_SESSION",
    ):
        _admit(
            _fact(
                end=CLOSE - timedelta(minutes=5),
            )
        )


def test_early_close_uses_explicit_session_truth():
    listing, session = _histories(
        state=USSessionState.EARLY_CLOSE,
    )

    result = _admit(
        _fact(end=EARLY_CLOSE),
        listing_history=listing,
        session_history=session,
    )

    assert len(result.bars) == 42
    assert result.coverage_end_at_utc == EARLY_CLOSE


def test_closed_session_rejects_intraday_rows():
    listing, session = _histories(
        state=USSessionState.CLOSED,
    )

    with pytest.raises(
        ValueError,
        match="explicitly closed session",
    ):
        _admit(
            _fact(),
            listing_history=listing,
            session_history=session,
        )


@pytest.mark.parametrize(
    "value",
    [
        BarGranularity.D1,
        "M5",
    ],
)
def test_only_exact_m1_or_m5_granularity(value):
    source = _bar(
        sequence=1,
        start=OPEN,
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="M1 or M5",
    ):
        USHistoricalIntradayBar(
            **(
                source.model_dump()
                | {"granularity": value}
            )
        )


def test_m1_bounded_window_is_supported():
    result = _admit(
        _fact(
            start=OPEN,
            end=OPEN + timedelta(minutes=3),
            first_sequence=1,
            granularity=BarGranularity.M1,
        ),
        coverage_mode=(
            USIntradayCoverageMode.BOUNDED_WINDOW
        ),
        granularity=BarGranularity.M1,
    )

    assert len(result.bars) == 3
    assert result.last_session_sequence == 3


def test_exact_utc_is_required():
    equivalent_zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="datetime.timezone.utc",
    ):
        _bar(
            sequence=1,
            start=OPEN.replace(
                tzinfo=equivalent_zero,
            ),
        )


def test_interval_width_must_match_granularity():
    source = _bar(
        sequence=1,
        start=OPEN,
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="width",
    ):
        USHistoricalIntradayBar(
            **(
                source.model_dump()
                | {
                    "interval_end_utc": (
                        OPEN + timedelta(minutes=1)
                    )
                }
            )
        )


def test_bar_cannot_be_available_before_end():
    with pytest.raises(
        (ValueError, ValidationError),
        match="available before",
    ):
        _bar(
            sequence=1,
            start=OPEN,
            available_at=(
                OPEN + timedelta(minutes=4)
            ),
        )


def test_gap_inside_artifact_is_rejected():
    rows = list(
        _bars(
            start=OPEN,
            end=OPEN + timedelta(minutes=15),
            first_sequence=1,
        )
    )

    rows[1] = _bar(
        sequence=2,
        start=OPEN + timedelta(minutes=6),
        source_row_number=2,
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="gap/overlap",
    ):
        _fact(
            start=OPEN,
            end=OPEN + timedelta(minutes=15),
            first_sequence=1,
            row_bars=tuple(rows),
        )


def test_gap_between_artifacts_is_rejected():
    split = datetime(
        2024, 7, 8, 16, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        ValueError,
        match="gap/overlap between",
    ):
        _admit(
            _fact(
                start=OPEN,
                end=split,
                first_sequence=1,
                sha_char="c",
                provenance="a",
            ),
            _fact(
                start=split + timedelta(minutes=5),
                end=CLOSE,
                first_sequence=32,
                sha_char="d",
                provenance="b",
            ),
        )


def test_segments_are_not_sorted_or_repaired():
    split = datetime(
        2024, 7, 8, 16, 0,
        tzinfo=timezone.utc,
    )

    first = _fact(
        start=OPEN,
        end=split,
        first_sequence=1,
        sha_char="c",
        provenance="a",
    )
    second = _fact(
        start=split,
        end=CLOSE,
        first_sequence=31,
        sha_char="d",
        provenance="b",
    )

    with pytest.raises(
        ValueError,
        match="already be chronologically ordered",
    ):
        _admit(
            second,
            first,
        )


def test_evidence_must_cover_all_intraday_fields():
    fields = set(
        US_INTRADAY_EVIDENCE_FIELDS
    )
    fields.remove("available_at_utc")

    with pytest.raises(
        (ValueError, ValidationError),
        match="available_at_utc",
    ):
        _fact(
            covered_fields=fields,
        )


def test_historical_evidence_after_cutoff_is_rejected():
    with pytest.raises(
        ValueError,
        match="historical availability",
    ):
        _admit(
            _fact(
                package_available_at=(
                    DECISION
                    + timedelta(seconds=1)
                )
            )
        )


def test_bar_after_evidence_cutoff_is_rejected():
    rows = list(
        _bars(
            start=OPEN,
            end=CLOSE,
            first_sequence=1,
        )
    )

    rows[-1] = _bar(
        sequence=78,
        start=CLOSE - timedelta(minutes=5),
        source_row_number=78,
        available_at=(
            DECISION
            + timedelta(seconds=1)
        ),
    )

    with pytest.raises(
        ValueError,
        match="unavailable by evidence cutoff",
    ):
        _admit(
            _fact(
                row_bars=tuple(rows),
            )
        )


def test_exact_dated_listing_is_required():
    listing = _listing_history(
        dates=(date(2024, 7, 5),),
        decision=DECISION,
        build=BUILD,
    )
    _, session = _histories()

    with pytest.raises(
        ValueError,
        match="exact-dated US listing identity unavailable",
    ):
        _admit(
            _fact(),
            listing_history=listing,
            session_history=session,
        )


def test_ticker_must_match_exact_dated_listing():
    listing, session = _histories(
        symbol="NEW",
    )

    with pytest.raises(
        ValueError,
        match="stable dated listing",
    ):
        _admit(
            _fact(),
            listing_history=listing,
            session_history=session,
        )


def test_identity_is_deterministic_and_excludes_build_clock():
    first = _admit(
        _fact()
    )

    second = _admit(
        _fact(),
        research_built_at=(
            BUILD + timedelta(days=1)
        ),
    )

    assert first.identity == second.identity
    assert first.bars == second.bars
    assert (
        first.research_built_at
        != second.research_built_at
    )


def test_us7b_does_not_materialize_shared_execution_bars():
    from app.data.intraday import IntradayBar

    result = _admit(
        _fact()
    )

    assert all(
        type(row) is USHistoricalIntradayBar
        for row in result.bars
    )
    assert all(
        not isinstance(row, IntradayBar)
        for row in result.bars
    )


def test_fact_is_revalidated_without_dumping_nested_contracts():
    source = _fact()

    result = _admit(
        source
    )

    assert len(result.facts) == 1
    assert (
        type(result.facts[0].segment)
        is USHistoricalIntradaySegment
    )
    assert all(
        type(row) is USHistoricalIntradayBar
        for row in result.facts[0].bars
    )


def test_copied_fact_with_noncanonical_nested_bars_fails_closed():
    corrupted = _fact().model_copy(
        update={
            "bars": [
                row.model_dump(
                    mode="python"
                )
                for row in _fact().bars
            ],
        }
    )

    with pytest.raises(
        ValueError,
        match="canonical intraday-bar tuple",
    ):
        _admit(
            corrupted
        )


def test_segment_receipt_sha_binding_is_revalidated():
    source = _fact()

    wrong_package = _package(
        provider="intraday-provider",
        category="US_INTRADAY_BARS",
        covered_fields=US_INTRADAY_EVIDENCE_FIELDS,
        available_at=CLOSE,
        sha_char="d",
    )

    corrupted = source.model_copy(
        update={
            "evidence_package": wrong_package,
        }
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="segment source_sha256 does not match",
    ):
        _admit(corrupted)


def test_nonincreasing_raw_source_rows_are_rejected():
    rows = (
        _bar(
            sequence=1,
            start=OPEN,
            source_row_number=2,
        ),
        _bar(
            sequence=2,
            start=OPEN + timedelta(minutes=5),
            source_row_number=1,
        ),
    )

    with pytest.raises(
        (ValueError, ValidationError),
        match="source rows must be strictly increasing",
    ):
        _fact(
            start=OPEN,
            end=OPEN + timedelta(minutes=10),
            first_sequence=1,
            row_bars=rows,
        )


def test_overlapping_artifacts_are_rejected():
    split = datetime(
        2024, 7, 8, 16, 0,
        tzinfo=timezone.utc,
    )

    with pytest.raises(
        ValueError,
        match="gap/overlap between intraday segments",
    ):
        _admit(
            _fact(
                start=OPEN,
                end=split,
                first_sequence=1,
                sha_char="c",
                provenance="chunk-a",
            ),
            _fact(
                start=split - timedelta(minutes=5),
                end=CLOSE,
                first_sequence=30,
                sha_char="d",
                provenance="chunk-b",
            ),
        )


def test_duplicate_segment_fact_is_rejected():
    source = _fact()

    with pytest.raises(
        ValueError,
        match="duplicate historical US intraday segment fact",
    ):
        _admit(
            source,
            source,
        )


def test_history_cutoff_must_match_intraday_cutoff():
    listing, session = _histories()

    with pytest.raises(
        ValueError,
        match="listing history decision_at must match",
    ):
        _admit(
            _fact(),
            listing_history=listing,
            session_history=session,
            evidence_cutoff_at=(
                DECISION + timedelta(hours=1)
            ),
        )


def test_research_build_cannot_precede_evidence_cutoff():
    listing, session = _histories()

    with pytest.raises(
        ValueError,
        match="cannot precede evidence_cutoff_at",
    ):
        _admit(
            _fact(),
            listing_history=listing,
            session_history=session,
            research_built_at=(
                DECISION - timedelta(seconds=1)
            ),
        )
