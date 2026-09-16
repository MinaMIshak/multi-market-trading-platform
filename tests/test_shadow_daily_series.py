"""Authenticated historical native-currency portfolio valuation-series tests."""
import hashlib
from datetime import datetime, time, timedelta, timezone
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
    shadow_daily_series,
    shadow_daily_snapshots,
    shadow_portfolio,
)
from app.paper.shadow_daily_series import (
    CashSnapshotRequest,
    MarkedSnapshotRequest,
    authenticated_daily_portfolio_series,
)
from app.paper.shadow_daily_snapshots import (
    append_cash_only_daily_portfolio_snapshot,
)
from tests.test_shadow_daily_snapshots import (
    setup_cash_snapshot,
)


def _policy_id(tmp_path, portfolio):
    return shadow_portfolio.audit_portfolio_policy(
        tmp_path,
        portfolio,
    )["policy_id"]


def _fake_snapshot(
    day,
    nav,
    *,
    policy_id,
    currency="USD",
    marked=False,
):
    recorded_at = datetime.combine(
        day,
        time(12),
        tzinfo=timezone.utc,
    )

    identity = hashlib.sha256(
        (
            f"{day.isoformat()}|{nav}|{policy_id}|"
            f"{currency}|{marked}"
        ).encode()
    ).hexdigest()

    return {
        "snapshot_date_utc": day.isoformat(),
        "snapshot_id": identity,
        "recorded_at": recorded_at.isoformat(),
        "policy_id": policy_id,
        "currency": currency,
        "valuation_status": (
            "AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION"
            if marked
            else
            "AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION"
        ),
        "performance_status": "PERFORMANCE NOT EVALUATED",
        "valuation": {
            "gross_marked_nav": str(Decimal(nav)),
        },
    }


def test_two_real_cash_snapshots_build_authenticated_zero_return_series(
    tmp_path,
    monkeypatch,
):
    _, portfolio, first_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    first_path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    second_at = first_at + timedelta(days=1)

    monkeypatch.setattr(
        shadow_daily_snapshots,
        "_now",
        lambda: second_at,
    )

    second_path = append_cash_only_daily_portfolio_snapshot(
        tmp_path,
        portfolio,
    )

    result = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        (
            CashSnapshotRequest(first_at.date()),
            CashSnapshotRequest(second_at.date()),
        ),
    )

    assert result["currency"] == "USD"
    assert result["performance_status"] == (
        "VALUATION SERIES ONLY / M7 PERFORMANCE NOT EVALUATED"
    )

    assert len(result["observations"]) == 2
    assert len(result["intervals"]) == 1

    interval = result["intervals"][0]

    assert interval["start_date_utc"] == (
        first_at.date().isoformat()
    )
    assert interval["end_date_utc"] == (
        second_at.date().isoformat()
    )
    assert interval["elapsed_days"] == 1
    assert Decimal(
        interval["gross_marked_return"]
    ) == 0

    summary = result["summary"]

    assert Decimal(
        summary["starting_gross_marked_nav"]
    ) == Decimal("1000")

    assert Decimal(
        summary["ending_gross_marked_nav"]
    ) == Decimal("1000")

    assert Decimal(
        summary["cumulative_gross_marked_return"]
    ) == 0

    assert Decimal(
        summary["max_drawdown_amount"]
    ) == 0

    assert Decimal(
        summary["max_drawdown_fraction"]
    ) == 0

    assert (
        summary["max_drawdown_amount_peak_date_utc"]
        is None
    )
    assert (
        summary["max_drawdown_amount_trough_date_utc"]
        is None
    )
    assert (
        summary["max_drawdown_fraction_peak_date_utc"]
        is None
    )
    assert (
        summary["max_drawdown_fraction_trough_date_utc"]
        is None
    )

    first_event = (
        shadow_daily_snapshots
        .audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            first_at.date(),
        )
    )

    second_event = (
        shadow_daily_snapshots
        .audit_cash_only_daily_portfolio_snapshot(
            tmp_path,
            portfolio,
            second_at.date(),
        )
    )

    expected_hashes = [
        hashlib.sha256(
            shadow_daily_snapshots._canonical(event)
        ).hexdigest()
        for event in (
            first_event,
            second_event,
        )
    ]

    assert [
        row["snapshot_receipt_sha256"]
        for row in result["observations"]
    ] == expected_hashes

    assert first_path.exists()
    assert second_path.exists()


def test_irregular_snapshot_intervals_compute_returns_and_marked_drawdown(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)
    third = first + timedelta(days=3)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "120",
            policy_id=policy_id,
        ),
        third: _fake_snapshot(
            third,
            "90",
            policy_id=policy_id,
        ),
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    result = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        (
            CashSnapshotRequest(first),
            CashSnapshotRequest(second),
            CashSnapshotRequest(third),
        ),
    )

    assert [
        row["elapsed_days"]
        for row in result["intervals"]
    ] == [1, 2]

    assert [
        Decimal(row["gross_marked_return"])
        for row in result["intervals"]
    ] == [
        Decimal("0.2"),
        Decimal("-0.25"),
    ]

    summary = result["summary"]

    assert Decimal(
        summary["cumulative_gross_marked_return"]
    ) == Decimal("-0.1")

    assert Decimal(
        summary["peak_gross_marked_nav"]
    ) == Decimal("120")

    assert Decimal(
        summary["max_drawdown_amount"]
    ) == Decimal("30")

    assert Decimal(
        summary["max_drawdown_fraction"]
    ) == Decimal("0.25")

    assert (
        summary["max_drawdown_amount_peak_date_utc"]
        == second.isoformat()
    )
    assert (
        summary["max_drawdown_amount_trough_date_utc"]
        == third.isoformat()
    )
    assert (
        summary["max_drawdown_fraction_peak_date_utc"]
        == second.isoformat()
    )
    assert (
        summary["max_drawdown_fraction_trough_date_utc"]
        == third.isoformat()
    )


def test_marked_request_dispatches_to_authenticated_marked_snapshot_audit(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    first_event = _fake_snapshot(
        first,
        "100",
        policy_id=policy_id,
    )

    second_event = _fake_snapshot(
        second,
        "101",
        policy_id=policy_id,
        marked=True,
    )

    seen = []

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: first_event,
    )

    def marked_audit(
        directory,
        supplied_portfolio,
        day,
        mark_requests,
    ):
        seen.append(
            (
                directory,
                supplied_portfolio,
                day,
                mark_requests,
            )
        )
        return second_event

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_marked_daily_portfolio_snapshot",
        marked_audit,
    )

    result = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        (
            CashSnapshotRequest(first),
            MarkedSnapshotRequest(second, ()),
        ),
    )

    assert len(seen) == 1
    assert seen[0][0] == tmp_path
    assert seen[0][1] == portfolio
    assert seen[0][2] == second
    assert seen[0][3] == ()

    assert Decimal(
        result["intervals"][0][
            "gross_marked_return"
        ]
    ) == Decimal("0.01")


def test_series_requires_exact_tuple_two_observations_and_strict_date_order(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    with pytest.raises(
        ValueError,
        match="exact tuple",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            [
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ],
        )

    with pytest.raises(
        ValueError,
        match="at least two",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
            ),
        )

    with pytest.raises(
        ValueError,
        match="strictly increasing",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(second),
                CashSnapshotRequest(first),
            ),
        )

    with pytest.raises(
        ValueError,
        match="strictly increasing",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(first),
            ),
        )


def test_series_fails_closed_on_policy_or_currency_mismatch(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    good = _fake_snapshot(
        first,
        "100",
        policy_id=policy_id,
    )

    cases = (
        (
            _fake_snapshot(
                second,
                "101",
                policy_id="f" * 64,
            ),
            "policy",
        ),
        (
            _fake_snapshot(
                second,
                "101",
                policy_id=policy_id,
                currency="EGP",
            ),
            "currency",
        ),
    )

    for bad, message in cases:
        events = {
            first: good,
            second: bad,
        }

        monkeypatch.setattr(
            shadow_daily_series,
            "audit_cash_only_daily_portfolio_snapshot",
            lambda directory, supplied_portfolio, day, values=events: (
                values[day]
            ),
        )

        with pytest.raises(
            ValueError,
            match=message,
        ):
            authenticated_daily_portfolio_series(
                tmp_path,
                portfolio,
                (
                    CashSnapshotRequest(first),
                    CashSnapshotRequest(second),
                ),
            )


def test_series_rejects_zero_previous_nav_instead_of_inventing_return(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    events = {
        first: _fake_snapshot(
            first,
            "0",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "10",
            policy_id=policy_id,
        ),
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match=(
            "positive previous gross marked NAV"
        ),
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


def test_series_does_not_swallow_snapshot_audit_failure(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    def fail_audit(*args, **kwargs):
        raise ValueError(
            "upstream snapshot audit rejected tampering"
        )

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        fail_audit,
    )

    with pytest.raises(
        ValueError,
        match=(
            "upstream snapshot audit rejected tampering"
        ),
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


def test_drawdown_amount_and_fraction_track_independent_episodes(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)
    third = first + timedelta(days=2)
    fourth = first + timedelta(days=3)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "50",
            policy_id=policy_id,
        ),
        third: _fake_snapshot(
            third,
            "1000",
            policy_id=policy_id,
        ),
        fourth: _fake_snapshot(
            fourth,
            "900",
            policy_id=policy_id,
        ),
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    result = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        (
            CashSnapshotRequest(first),
            CashSnapshotRequest(second),
            CashSnapshotRequest(third),
            CashSnapshotRequest(fourth),
        ),
    )

    summary = result["summary"]

    assert Decimal(
        summary["peak_gross_marked_nav"]
    ) == Decimal("1000")

    # Largest absolute decline:
    # 1000 -> 900 = 100.
    assert Decimal(
        summary["max_drawdown_amount"]
    ) == Decimal("100")

    assert (
        summary["max_drawdown_amount_peak_date_utc"]
        == third.isoformat()
    )
    assert (
        summary["max_drawdown_amount_trough_date_utc"]
        == fourth.isoformat()
    )

    # Largest proportional decline:
    # 100 -> 50 = 50%.
    assert Decimal(
        summary["max_drawdown_fraction"]
    ) == Decimal("0.5")

    assert (
        summary["max_drawdown_fraction_peak_date_utc"]
        == first.isoformat()
    )
    assert (
        summary["max_drawdown_fraction_trough_date_utc"]
        == second.isoformat()
    )


@pytest.mark.parametrize(
    "bad_nav",
    [
        101,
        101.0,
        True,
        None,
    ],
)
def test_series_rejects_noncanonical_nav_type_from_audit_boundary(
    tmp_path,
    monkeypatch,
    bad_nav,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    first_event = _fake_snapshot(
        first,
        "100",
        policy_id=policy_id,
    )

    second_event = _fake_snapshot(
        second,
        "101",
        policy_id=policy_id,
    )

    second_event["valuation"][
        "gross_marked_nav"
    ] = bad_nav

    events = {
        first: first_event,
        second: second_event,
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match="canonical decimal string",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


@pytest.mark.parametrize(
    "bad_nav",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "-1",
        "",
        "not-a-decimal",
    ],
)
def test_series_rejects_invalid_decimal_nav(
    tmp_path,
    monkeypatch,
    bad_nav,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "101",
            policy_id=policy_id,
        ),
    }

    events[second]["valuation"][
        "gross_marked_nav"
    ] = bad_nav

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match="gross marked NAV",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


def test_hostile_decimal_context_does_not_change_series(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)
    third = first + timedelta(days=2)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "123.456789",
            policy_id=policy_id,
        ),
        third: _fake_snapshot(
            third,
            "111.111111",
            policy_id=policy_id,
        ),
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    requests = (
        CashSnapshotRequest(first),
        CashSnapshotRequest(second),
        CashSnapshotRequest(third),
    )

    expected = authenticated_daily_portfolio_series(
        tmp_path,
        portfolio,
        requests,
    )

    hostile = Context(
        prec=3,
        rounding=ROUND_UP,
        Emin=-2,
        Emax=2,
    )
    hostile.traps[Inexact] = True

    with localcontext(hostile):
        actual = authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            requests,
        )

        assert actual == expected
        assert getcontext().prec == 3
        assert getcontext().rounding == ROUND_UP
        assert getcontext().traps[Inexact]


def test_series_rejects_duplicate_snapshot_identity(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    first_event = _fake_snapshot(
        first,
        "100",
        policy_id=policy_id,
    )

    second_event = _fake_snapshot(
        second,
        "101",
        policy_id=policy_id,
    )

    second_event["snapshot_id"] = (
        first_event["snapshot_id"]
    )

    events = {
        first: first_event,
        second: second_event,
    }

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match="duplicate snapshot identity",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


def test_series_requires_performance_not_evaluated_snapshot_semantics(
    tmp_path,
    monkeypatch,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "101",
            policy_id=policy_id,
        ),
    }

    events[second]["performance_status"] = (
        "PERFORMANCE EVALUATED"
    )

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match="performance semantics",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "naive",
        "wrong_date",
    ],
)
def test_series_rejects_noncanonical_snapshot_recorded_at(
    tmp_path,
    monkeypatch,
    case,
):
    _, portfolio, base_at = setup_cash_snapshot(
        tmp_path,
        monkeypatch,
    )

    policy_id = _policy_id(
        tmp_path,
        portfolio,
    )

    first = base_at.date()
    second = first + timedelta(days=1)

    events = {
        first: _fake_snapshot(
            first,
            "100",
            policy_id=policy_id,
        ),
        second: _fake_snapshot(
            second,
            "101",
            policy_id=policy_id,
        ),
    }

    if case == "missing":
        events[second]["recorded_at"] = None
    elif case == "malformed":
        events[second]["recorded_at"] = "not-a-datetime"
    elif case == "naive":
        events[second]["recorded_at"] = (
            datetime.combine(
                second,
                time(12),
            ).isoformat()
        )
    else:
        events[second]["recorded_at"] = (
            datetime.combine(
                second + timedelta(days=1),
                time(12),
                tzinfo=timezone.utc,
            ).isoformat()
        )

    monkeypatch.setattr(
        shadow_daily_series,
        "audit_cash_only_daily_portfolio_snapshot",
        lambda directory, supplied_portfolio, day: events[day],
    )

    with pytest.raises(
        ValueError,
        match="recorded_at",
    ):
        authenticated_daily_portfolio_series(
            tmp_path,
            portfolio,
            (
                CashSnapshotRequest(first),
                CashSnapshotRequest(second),
            ),
        )
