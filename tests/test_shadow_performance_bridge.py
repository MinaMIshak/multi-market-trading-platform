"""Authenticated C1-to-M7 periodic-return bridge tests."""
import hashlib
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import (
    Context,
    Decimal,
    Inexact,
    ROUND_UP,
    getcontext,
    localcontext,
)

import pytest

from app.paper import (
    shadow_daily_snapshots,
    shadow_performance_bridge,
)
from app.paper.shadow_daily_series import (
    CashSnapshotRequest,
    authenticated_daily_portfolio_series,
)
from app.paper.shadow_daily_snapshots import (
    append_cash_only_daily_portfolio_snapshot,
)
from app.paper.shadow_performance_bridge import (
    PeriodicReturnBridgeConfig,
    attach_authenticated_periodic_returns,
    authenticated_periodic_return_series,
)
from app.performance.analyzer import analyze_performance
from app.performance.extensions import PeriodicReturnSeries
from app.performance.models import (
    PerformanceAnalysisInput,
    PerformanceConfig,
)
from tests.test_shadow_daily_snapshots import (
    setup_cash_snapshot,
)


D = Decimal


def _three_cash_snapshots(
    tmp_path,
    monkeypatch,
    *,
    second_days=1,
    third_days=1,
):
    _, portfolio, first_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    second_at = first_at + timedelta(
        days=second_days,
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: second_at,
    )

    append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    third_at = second_at + timedelta(
        days=third_days,
    )

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: third_at,
    )

    append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    requests = (
        CashSnapshotRequest(first_at.date()),
        CashSnapshotRequest(second_at.date()),
        CashSnapshotRequest(third_at.date()),
    )

    return (
        portfolio,
        first_at,
        second_at,
        third_at,
        requests,
    )


def _config(**changes):
    values = {
        "period_seconds": 86400,
        "risk_free_return": D("0"),
        "sortino_target": D("0"),
        "minimum_samples": 2,
        "annualization_factor": None,
    }
    values.update(changes)
    return PeriodicReturnBridgeConfig(**values)


def _periodic():
    from datetime import datetime, timezone

    first = datetime(
        2026,
        1,
        2,
        12,
        tzinfo=timezone.utc,
    )
    second = first + timedelta(days=1)

    return PeriodicReturnSeries(
        schema_version="periodic-return-v1",
        series_id="authenticated-shadow-periodic-test",
        period_seconds=86400,
        period_ends=(first, second),
        returns=(D("0.1"), D("-0.05")),
        risk_free_return=D("0"),
        sortino_target=D("0"),
        minimum_samples=2,
        annualization_factor=None,
    )


def _empty_performance_request(
    *,
    periodic_returns=None,
):
    return PerformanceAnalysisInput(
        schema_version="performance-analysis-v1",
        config=PerformanceConfig(
            config_version="performance-v1",
            starting_equity=D("100"),
        ),
        observations=(),
        periodic_returns=periodic_returns,
    )


def test_exact_authenticated_daily_cadence_builds_periodic_return_series(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        second_at,
        third_at,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
    )

    result = authenticated_periodic_return_series(
        tmp_path,
        portfolio,
        requests,
        _config(),
    )

    assert type(result) is PeriodicReturnSeries

    assert result.schema_version == (
        "periodic-return-v1"
    )
    assert result.period_seconds == 86400

    assert result.period_ends == (
        second_at,
        third_at,
    )

    assert result.returns == (
        D("0"),
        D("0"),
    )

    assert result.risk_free_return == D("0")
    assert result.sortino_target == D("0")
    assert result.minimum_samples == 2

    # No annualization assumption is invented.
    assert result.annualization_factor is None

    assert len(result.series_id) == 64
    assert all(
        char in "0123456789abcdef"
        for char in result.series_id
    )


def test_explicit_annualization_is_preserved_but_never_inferred(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        _,
        _,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
    )

    unscaled = authenticated_periodic_return_series(
        tmp_path,
        portfolio,
        requests,
        _config(),
    )

    scaled = authenticated_periodic_return_series(
        tmp_path,
        portfolio,
        requests,
        _config(
            annualization_factor=D("252"),
        ),
    )

    assert unscaled.annualization_factor is None
    assert scaled.annualization_factor == D("252")

    # Configuration changes provenance identity.
    assert scaled.series_id != unscaled.series_id


def test_irregular_authenticated_snapshot_spacing_fails_closed(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        _,
        _,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
        second_days=1,
        third_days=2,
    )

    with pytest.raises(
        ValueError,
        match="fixed-period cadence",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


def test_configured_period_must_match_authenticated_receipt_spacing(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        _,
        _,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
    )

    with pytest.raises(
        ValueError,
        match="fixed-period cadence",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(
                period_seconds=43200,
            ),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"period_seconds": True},
            "period_seconds",
        ),
        (
            {"period_seconds": 0},
            "period_seconds",
        ),
        (
            {"risk_free_return": 0},
            "risk_free_return",
        ),
        (
            {"risk_free_return": D("NaN")},
            "risk_free_return",
        ),
        (
            {"sortino_target": 0.0},
            "sortino_target",
        ),
        (
            {"sortino_target": D("Infinity")},
            "sortino_target",
        ),
        (
            {"minimum_samples": True},
            "minimum_samples",
        ),
        (
            {"minimum_samples": 1},
            "minimum_samples",
        ),
        (
            {"annualization_factor": D("0")},
            "annualization_factor",
        ),
        (
            {"annualization_factor": D("NaN")},
            "annualization_factor",
        ),
        (
            {"annualization_factor": 252},
            "annualization_factor",
        ),
    ],
)
def test_bridge_config_is_explicit_exact_and_finite(
    changes,
    message,
):
    with pytest.raises(
        ValueError,
        match=message,
    ):
        _config(**changes)


def test_attachment_is_immutable_and_preserves_existing_m7_economics(
    tmp_path,
    monkeypatch,
):
    periodic = _periodic()

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_periodic_return_series",
        lambda *args, **kwargs: periodic,
    )

    original = _empty_performance_request()

    baseline = analyze_performance(original)

    attached = attach_authenticated_periodic_returns(
        original,
        tmp_path,
        object(),
        (),
        _config(),
    )

    # Input is not mutated.
    assert original.periodic_returns is None

    assert attached is not original
    assert attached.periodic_returns == periodic
    assert attached.config == original.config
    assert attached.observations == original.observations
    assert (
        attached.slippage_scenarios
        == original.slippage_scenarios
    )
    assert (
        attached.baseline_scenario_id
        == original.baseline_scenario_id
    )

    bridged = analyze_performance(attached)

    # C2 must not replace M7 trade economics or realized drawdown.
    assert bridged.summary == baseline.summary
    assert bridged.drawdown == baseline.drawdown
    assert bridged.months == baseline.months
    assert (
        bridged.monthly_consistency
        == baseline.monthly_consistency
    )
    assert bridged.regimes == baseline.regimes
    assert (
        bridged.slippage_sensitivity
        == baseline.slippage_sensitivity
    )
    assert (
        bridged.baseline_scenario_id
        == baseline.baseline_scenario_id
    )

    assert bridged.risk_adjusted.series == periodic


def test_attachment_refuses_to_overwrite_existing_periodic_returns(
    tmp_path,
    monkeypatch,
):
    existing = _periodic()

    original = _empty_performance_request(
        periodic_returns=existing,
    )

    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        return existing

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_periodic_return_series",
        should_not_run,
    )

    with pytest.raises(
        ValueError,
        match="already contains periodic returns",
    ):
        attach_authenticated_periodic_returns(
            original,
            tmp_path,
            object(),
            (),
            _config(),
        )

    assert called is False


def test_bridge_does_not_swallow_authenticated_series_failure(
    tmp_path,
    monkeypatch,
):
    def fail(*args, **kwargs):
        raise ValueError(
            "authenticated C1 series rejected tampering"
        )

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        fail,
    )

    with pytest.raises(
        ValueError,
        match="authenticated C1 series rejected tampering",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            object(),
            (),
            _config(),
        )


def _real_c1_source(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        _,
        _,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
    )

    source = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        requests,
    )

    return portfolio, requests, source


def _rehash_c1_source(source):
    basis = {
        key: value
        for key, value in source.items()
        if key != "series_id"
    }

    source["series_id"] = hashlib.sha256(
        shadow_daily_snapshots._canonical(basis)
    ).hexdigest()

    return source


def test_bridge_rejects_tampered_c1_series_identity(
    tmp_path,
    monkeypatch,
):
    portfolio, requests, source = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    source = deepcopy(source)
    source["series_id"] = "f" * 64

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        lambda *args, **kwargs: source,
    )

    with pytest.raises(
        ValueError,
        match="source series identity",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "schema_version",
            "shadow-daily-portfolio-series-v999",
        ),
        (
            "label",
            "LIVE",
        ),
        (
            "scoring",
            "SCORED",
        ),
        (
            "performance_status",
            "PERFORMANCE EVALUATED",
        ),
    ],
)
def test_bridge_rejects_rehashed_invalid_c1_semantics(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    portfolio, requests, source = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    source = deepcopy(source)
    source[field] = value
    _rehash_c1_source(source)

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        lambda *args, **kwargs: source,
    )

    with pytest.raises(
        ValueError,
        match="C1 series semantics",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


@pytest.mark.parametrize(
    "field",
    [
        "start_snapshot_id",
        "end_snapshot_id",
    ],
)
def test_bridge_rejects_rehashed_interval_snapshot_provenance_tampering(
    tmp_path,
    monkeypatch,
    field,
):
    portfolio, requests, source = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    source = deepcopy(source)

    source["intervals"][0][field] = "f" * 64

    _rehash_c1_source(source)

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        lambda *args, **kwargs: source,
    )

    with pytest.raises(
        ValueError,
        match="interval provenance",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


def test_bridge_rejects_rehashed_interval_date_or_elapsed_day_tampering(
    tmp_path,
    monkeypatch,
):
    portfolio, requests, original = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    for field, value in (
        (
            "start_date_utc",
            original["observations"][1][
                "snapshot_date_utc"
            ],
        ),
        (
            "end_date_utc",
            original["observations"][0][
                "snapshot_date_utc"
            ],
        ),
        (
            "elapsed_days",
            99,
        ),
    ):
        source = deepcopy(original)
        source["intervals"][0][field] = value
        _rehash_c1_source(source)

        monkeypatch.setattr(
            shadow_performance_bridge,
            "authenticated_daily_portfolio_series",
            lambda *args, values=source, **kwargs: values,
        )

        with pytest.raises(
            ValueError,
            match="interval chronology",
        ):
            authenticated_periodic_return_series(
                tmp_path,
                portfolio,
                requests,
                _config(),
            )


def test_bridge_rejects_rehashed_observation_date_clock_mismatch(
    tmp_path,
    monkeypatch,
):
    portfolio, requests, source = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    source = deepcopy(source)

    recorded_at = datetime.fromisoformat(
        source["observations"][1]["recorded_at"]
    )

    source["observations"][1][
        "snapshot_date_utc"
    ] = (
        recorded_at.date() + timedelta(days=1)
    ).isoformat()

    _rehash_c1_source(source)

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        lambda *args, **kwargs: source,
    )

    with pytest.raises(
        ValueError,
        match="observation chronology",
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "currency",
            "EGP",
            "source currency",
        ),
        (
            "policy_id",
            "f" * 64,
            "source policy",
        ),
    ],
)
def test_bridge_rejects_rehashed_source_portfolio_binding_tampering(
    tmp_path,
    monkeypatch,
    field,
    value,
    message,
):
    portfolio, requests, source = _real_c1_source(
        tmp_path,
        monkeypatch,
    )

    source = deepcopy(source)
    source[field] = value
    _rehash_c1_source(source)

    monkeypatch.setattr(
        shadow_performance_bridge,
        "authenticated_daily_portfolio_series",
        lambda *args, **kwargs: source,
    )

    with pytest.raises(
        ValueError,
        match=message,
    ):
        authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            _config(),
        )


def test_hostile_decimal_context_does_not_change_periodic_bridge(
    tmp_path,
    monkeypatch,
):
    (
        portfolio,
        _,
        _,
        _,
        requests,
    ) = _three_cash_snapshots(
        tmp_path,
        monkeypatch,
    )

    config = _config(
        risk_free_return=D("0.00125"),
        sortino_target=D("-0.0025"),
        annualization_factor=D("252"),
    )

    expected = authenticated_periodic_return_series(
        tmp_path,
        portfolio,
        requests,
        config,
    )

    hostile = Context(
        prec=3,
        rounding=ROUND_UP,
        Emin=-2,
        Emax=2,
    )
    hostile.traps[Inexact] = True

    with localcontext(hostile):
        actual = authenticated_periodic_return_series(
            tmp_path,
            portfolio,
            requests,
            config,
        )

        assert actual == expected
        assert getcontext().prec == 3
        assert getcontext().rounding == ROUND_UP
        assert getcontext().traps[Inexact]
