"""Context time series: session-aware freshness, summaries, reconciliation, co-movement.

Observations are (date, Decimal) pairs, strictly ascending, never filled.
Freshness compares the latest observation with the latest *expected* session
of the series' own calendar:

- ``NYSE``: the NYSE rule calendar (app/us/nyse_calendar.py);
- ``EGX``: Sunday–Thursday (Friday/Saturday weekend; holidays are not
  verified here, so a holiday can read STALE and the report says which date
  was expected);
- ``FX``: Monday–Friday, with 25 December and 1 January closed (global FX and
  spot-metal convention);
- ``PUBLICATION``: no session calendar; CURRENT within ``max_age_days`` of the
  publisher's normal lag (official statistics).

No market truth is derived from context series; they never create a candidate.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from math import sqrt

from app.us import nyse_calendar


def is_expected_session(day: date, calendar: str) -> bool:
    if calendar == "NYSE":
        return nyse_calendar.is_session(day)
    if calendar == "EGX":
        return day.weekday() not in (4, 5)
    if calendar == "FX":
        return day.weekday() < 5 and (day.month, day.day) not in ((12, 25), (1, 1))
    raise ValueError(f"unknown calendar {calendar}")


def expected_latest(as_of_trade_date: date, calendar: str) -> date:
    """Latest session strictly before the current (incomplete) trade date."""
    day = as_of_trade_date - timedelta(days=1)
    while not is_expected_session(day, calendar):
        day -= timedelta(days=1)
    return day


def _change(new, old, unit):
    if old is None:
        return None, None
    delta = new - old
    pct = None if unit in ("%", "pct") or old == 0 else (delta / old * 100).quantize(Decimal("0.01"))
    return str(delta), (None if pct is None else str(pct))


def summarize(observations, *, as_of: date, calendar: str, unit: str, max_age_days: int | None = None,
              current_trade_date: date | None = None, lookback: int = 20):
    """Point-in-time summary; observations dated after ``as_of`` are excluded."""
    known = [(day, value) for day, value in observations if day <= as_of]
    excluded = len(observations) - len(known)
    if not known:
        return {"status": "UNAVAILABLE", "reason": "no observation on or before as-of", "excluded_future": excluded}
    day, value = known[-1]
    change_1, change_1_pct = _change(value, known[-2][1] if len(known) > 1 else None, unit)
    change_n, change_n_pct = _change(value, known[-1 - lookback][1] if len(known) > lookback else None, unit)
    summary = {"status": "AVAILABLE", "latest_date": day.isoformat(), "latest_value": str(value),
               "change_1": change_1, "change_1_pct": change_1_pct, f"change_{lookback}": change_n,
               f"change_{lookback}_pct": change_n_pct, "observations": len(known), "excluded_future": excluded,
               "calendar": calendar}
    if calendar == "PUBLICATION":
        age = (as_of - day).days
        summary.update(age_days=age, freshness="CURRENT" if age <= max_age_days else "STALE",
                       expected_latest=None)
    else:
        expected = expected_latest(current_trade_date or as_of + timedelta(days=1), calendar)
        summary.update(age_days=(as_of - day).days, expected_latest=expected.isoformat(),
                       freshness="CURRENT" if day >= expected else "STALE")
    return summary


def reconcile(primary, verification, *, tolerance_pct: Decimal, same_date_required=True):
    """AGREE / DISCREPANT / UNVERIFIED between two independent observations."""
    if not primary or not verification:
        return {"status": "UNVERIFIED", "reason": "an observation is missing"}
    (p_day, p_value), (v_day, v_value) = primary, verification
    if same_date_required and p_day != v_day:
        return {"status": "UNVERIFIED", "reason": f"dates differ ({p_day} vs {v_day})"}
    if p_value == 0:
        return {"status": "UNVERIFIED", "reason": "primary value is zero"}
    diff = (v_value - p_value) / p_value * 100
    return {"status": "AGREE" if abs(diff) <= tolerance_pct else "DISCREPANT",
            "difference_pct": str(diff.quantize(Decimal("0.001"))), "tolerance_pct": str(tolerance_pct),
            "primary": [p_day.isoformat(), str(p_value)], "verification": [v_day.isoformat(), str(v_value)]}


def returns(observations):
    """{date: simple return vs the previous observation} (consecutive observations only)."""
    return {day: float(value / previous - 1) for (prev_day, previous), (day, value)
            in zip(observations, observations[1:]) if previous}


def correlation(a, b, *, window: int, min_overlap: int):
    """Pearson correlation of returns on common dates only (last ``window``)."""
    common = sorted(set(a) & set(b))[-window:]
    if len(common) < min_overlap:
        return {"value": None, "n": len(common), "reason": f"fewer than {min_overlap} common dates"}
    x, y = [a[d] for d in common], [b[d] for d in common]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sx = sqrt(sum((v - mx) ** 2 for v in x))
    sy = sqrt(sum((v - my) ** 2 for v in y))
    if not sx or not sy:
        return {"value": None, "n": len(common), "reason": "zero variance"}
    value = sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy)
    return {"value": round(value, 3), "n": len(common), "start": common[0].isoformat(), "end": common[-1].isoformat()}
