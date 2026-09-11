"""US6 synthetic ENGINEERING fixtures only; never market evidence."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import socket

import pytest

from app.strategies.eod import (
    SwingConfig,
    evaluate_swing_series,
)
from app.us.research_adapter import (
    USSwingResearchResult,
    evaluate_us_swing,
)
from test_us5b_retrospective_pit import (
    END,
    INSTRUMENT,
    _build,
    _deps,
)


D = Decimal

AT = datetime(
    2024, 7, 8, 21, 0,
    tzinfo=timezone.utc,
)


def config(**changes):
    values = {
        "config_version": "us6-test",
        "minimum_history": 2,
        "fast_ema_window": 1,
        "slow_ema_window": 2,
        "breakout_lookback": None,
    }

    return SwingConfig(
        **(values | changes)
    )


def source_dataset():
    deps = _deps(
        decision=AT,
    )

    return _build(
        dependencies=deps,
        decision=AT,
    )


class ExtendedSwingConfig(SwingConfig):
    """Legitimate legacy subclass used to protect EGX compatibility."""

    extension_name: str


def synthetic_watch_dataset():
    """Structurally valid adapter fixture; not historical PIT evidence."""

    source = source_dataset()

    raw_values = source.rows[0].model_dump(
        mode="python"
    )

    raw_values.update(
        market_date=date(2024, 7, 4),
        open=D("10"),
        high=D("11"),
        low=D("9"),
        close=D("10"),
    )

    prior_raw = type(
        source.rows[0]
    ).model_validate(
        raw_values,
        strict=True,
    )

    first_adjusted = replace(
        source.split_adjusted[0],
        market_date=date(2024, 7, 4),
        open=D("10"),
        high=D("11"),
        low=D("9"),
        close=D("10"),
        price_factor=D("1"),
        volume_factor=D("1"),
        split_event_ids=(),
    )

    middle_adjusted = replace(
        source.split_adjusted[0],
        open=D("12"),
        high=D("13"),
        low=D("11"),
        close=D("12"),
        price_factor=D("1"),
        volume_factor=D("1"),
        split_event_ids=(),
    )

    final_adjusted = replace(
        source.split_adjusted[-1],
        open=D("15"),
        high=D("16"),
        low=D("14"),
        close=D("15"),
        price_factor=D("1"),
        volume_factor=D("1"),
        split_event_ids=(),
    )

    return replace(
        source,
        coverage_start=date(2024, 7, 4),
        rows=(
            prior_raw,
            source.rows[0],
            source.rows[-1],
        ),
        split_adjusted=(
            first_adjusted,
            middle_adjusted,
            final_adjusted,
        ),
    )


def test_shared_swing_math_watch():
    result = evaluate_swing_series(
        closes=(
            10.0,
            12.0,
            15.0,
        ),
        highs=(
            11.0,
            13.0,
            16.0,
        ),
        config=config(
            minimum_history=3,
            fast_ema_window=2,
            slow_ema_window=3,
            breakout_lookback=2,
        ),
    )

    assert result.state == "WATCH"
    assert result.fast_ema > result.slow_ema
    assert result.breakout_reference == 13.0


def test_shared_swing_math_no_confirmation():
    result = evaluate_swing_series(
        closes=(
            10.0,
            10.0,
        ),
        highs=(
            11.0,
            11.0,
        ),
        config=config(),
    )

    assert result.state == "NO_CONFIRMATION"
    assert result.breakout_reference is None


def test_shared_swing_revalidates_parent_config_fields():
    bad = config().model_copy(
        update={
            "fast_ema_window": 3,
        }
    )

    with pytest.raises(ValueError):
        evaluate_swing_series(
            closes=(
                10.0,
                11.0,
            ),
            highs=(
                11.0,
                12.0,
            ),
            config=bad,
        )


def test_shared_swing_preserves_legacy_subclass_compatibility():
    cfg = ExtendedSwingConfig(
        config_version="legacy-extension",
        minimum_history=2,
        fast_ema_window=1,
        slow_ema_window=2,
        breakout_lookback=None,
        extension_name="fixture",
    )

    result = evaluate_swing_series(
        closes=(
            10.0,
            10.0,
        ),
        highs=(
            11.0,
            11.0,
        ),
        config=cfg,
    )

    assert result.state == "NO_CONFIRMATION"


@pytest.mark.parametrize(
    "closes,highs,match",
    [
        (
            [10.0, 11.0],
            (11.0, 12.0),
            "tuples",
        ),
        (
            (10.0,),
            (11.0,),
            "minimum history",
        ),
        (
            (10.0, 11.0),
            (11.0,),
            "length mismatch",
        ),
        (
            (10.0, float("nan")),
            (11.0, 12.0),
            "positive finite float",
        ),
        (
            (10, 11.0),
            (11.0, 12.0),
            "positive finite float",
        ),
    ],
)
def test_shared_swing_rejects_invalid_series(
    closes,
    highs,
    match,
):
    with pytest.raises(
        ValueError,
        match=match,
    ):
        evaluate_swing_series(
            closes=closes,
            highs=highs,
            config=config(),
        )


def test_us_adapter_preserves_stable_identity_and_nonexecution():
    source = source_dataset()

    result = evaluate_us_swing(
        source,
        config(),
    )

    assert isinstance(
        result,
        USSwingResearchResult,
    )

    assert result.instrument_id == INSTRUMENT
    assert (
        result.canonical_symbol
        == source.rows[-1].canonical_symbol
    )
    assert result.listing_mic == "XNAS"
    assert result.signal_date == END
    assert result.decision_at == AT

    assert (
        result.source_dataset_id
        == source.identity
    )

    assert (
        result.config_id
        == config().identity
    )

    assert result.state == "NO_CONFIRMATION"
    assert (
        result.planning_close_reference
        is None
    )

    assert (
        result.validation_status
        == "UNVALIDATED"
    )

    assert result.execution_allowed is False


def test_us_watch_uses_shared_indicators_but_raw_planning_reference():
    source = synthetic_watch_dataset()

    cfg = config(
        minimum_history=3,
        fast_ema_window=2,
        slow_ema_window=3,
        breakout_lookback=2,
    )

    result = evaluate_us_swing(
        source,
        cfg,
    )

    assert result.state == "WATCH"
    assert result.fast_ema > result.slow_ema
    assert result.breakout_reference == 13.0

    # Planning/execution reference remains the raw last close.
    assert (
        result.planning_close_reference
        == source.rows[-1].close
    )

    assert result.execution_allowed is False


def test_us_boundary_requires_exact_swing_config():
    cfg = ExtendedSwingConfig(
        config_version="extension",
        minimum_history=2,
        fast_ema_window=1,
        slow_ema_window=2,
        breakout_lookback=None,
        extension_name="fixture",
    )

    with pytest.raises(
        ValueError,
        match="exact SwingConfig",
    ):
        evaluate_us_swing(
            source_dataset(),
            cfg,
        )


def test_stale_retrospective_decision_is_not_signal_admissible():
    stale = _build()

    with pytest.raises(
        ValueError,
        match="decision horizon",
    ):
        evaluate_us_swing(
            stale,
            config(),
        )


def test_coverage_end_requires_eligible_observation():
    deps = _deps(
        include_bar_8=False,
        universe_kwargs={
            "eligible_8": False,
        },
        decision=AT,
    )

    source = _build(
        dependencies=deps,
        decision=AT,
    )

    assert (
        source.rows[-1].market_date
        != END
    )

    with pytest.raises(
        ValueError,
        match="coverage_end lacks eligible",
    ):
        evaluate_us_swing(
            source,
            config(),
        )


def test_raw_and_adjusted_alignment_corruption_fails_closed():
    source = source_dataset()

    corrupted = replace(
        source,
        split_adjusted=tuple(
            reversed(
                source.split_adjusted
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match="market_date mismatch",
    ):
        evaluate_us_swing(
            corrupted,
            config(),
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        (
            {
                "dq_status": "UNKNOWN",
            },
            "validated US5B dataset",
        ),
        (
            {
                "transformation": "unknown",
            },
            "split transformation",
        ),
    ],
)
def test_us5b_boundary_metadata_corruption_fails_closed(
    changes,
    match,
):
    source = replace(
        source_dataset(),
        **changes,
    )

    with pytest.raises(
        ValueError,
        match=match,
    ):
        evaluate_us_swing(
            source,
            config(),
        )


def test_noncanonical_dataset_utc_fails_closed():
    noncanonical_zero = timezone(
        timedelta(0),
        name="ZERO",
    )

    source = replace(
        source_dataset(),
        decision_at=AT.replace(
            tzinfo=noncanonical_zero,
        ),
    )

    with pytest.raises(
        ValueError,
        match="datetime.timezone.utc",
    ):
        evaluate_us_swing(
            source,
            config(),
        )


def test_us_adapter_revalidates_copied_config():
    bad = config().model_copy(
        update={
            "fast_ema_window": 3,
        }
    )

    with pytest.raises(ValueError):
        evaluate_us_swing(
            source_dataset(),
            bad,
        )


def test_us_adapter_is_deterministic_and_does_not_mutate_source():
    source = source_dataset()
    before = source.identity
    cfg = config()

    first = evaluate_us_swing(
        source,
        cfg,
    )

    second = evaluate_us_swing(
        source,
        cfg,
    )

    assert first == second
    assert source.identity == before
    assert (
        first.source_dataset_id
        == before
    )


def test_us6_has_no_network_dependency(
    monkeypatch,
):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "network forbidden"
        )

    monkeypatch.setattr(
        socket,
        "socket",
        forbidden,
    )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        forbidden,
    )

    monkeypatch.setattr(
        socket,
        "create_connection",
        forbidden,
    )

    result = evaluate_us_swing(
        source_dataset(),
        config(),
    )

    assert result.execution_allowed is False
