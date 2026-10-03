"""Macro and cross-asset context: rates, USD, Brent (research only).

Every value is a published observation from a U.S. government statistical
series, delivered as CSV by FRED (Federal Reserve Bank of St. Louis), with the
original publisher attributed. The raw response is stored immutably (SHA-256)
before parsing. Nothing is interpolated, forward-filled or estimated. A missing
value is skipped and reported, never invented. Observations dated after the
report's as-of date are excluded (point in time). FRED may revise history, so
`retrieved_at` is the known-at time and these reports are not backtest inputs.

Context only: no macro value creates, upgrades or vetoes a candidate.
LIVE_MONEY=DISABLED.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

SCHEMA = "macro-context-report-v1"
DELIVERY = "FRED, Federal Reserve Bank of St. Louis (fredgraph.csv)"
RIGHTS = "US_GOVERNMENT_PUBLIC_DATA_ATTRIBUTION_REQUIRED"
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
HISTORY_DAYS = 400
CHANGE_LOOKBACK = 20


@dataclass(frozen=True)
class MacroSeries:
    series_id: str
    title: str
    unit: str
    publisher: str
    group: str
    # Calendar days after which the latest observation is STALE. Set from the
    # publisher's normal lag (H.15 next business day; EIA Brent and H.10 weekly).
    max_age_days: int


SERIES = (
    MacroSeries("DFF", "US effective federal funds rate", "%", "Federal Reserve Board (H.15)", "Rates", 5),
    MacroSeries("DGS2", "US Treasury 2-year yield (constant maturity)", "%", "Federal Reserve Board (H.15)",
                "Rates", 5),
    MacroSeries("DGS10", "US Treasury 10-year yield (constant maturity)", "%", "Federal Reserve Board (H.15)",
                "Rates", 5),
    MacroSeries("DTWEXBGS", "Nominal broad US dollar index", "index, Jan 2006 = 100",
                "Federal Reserve Board (H.10)", "FX", 10),
    MacroSeries("DEXUSEU", "US dollars per euro", "USD", "Federal Reserve Board (H.10)", "FX", 10),
    MacroSeries("DCOILBRENTEU", "Brent crude oil spot (Europe)", "USD per barrel",
                "U.S. Energy Information Administration", "Commodities", 10),
)
BY_ID = {series.series_id: series for series in SERIES}

# Capabilities in the mission scope with no admitted lawful free source yet.
BLOCKED = (
    ("Gold spot", "No rights-clear free daily source reviewed: LBMA prices are licensed, and FRED no longer "
                  "carries them."),
    ("USD/EGP and CBE policy rate", "Central Bank of Egypt publication terms not reviewed; not admitted."),
    ("News and catalysts", "No admitted news or disclosure feed with reviewed terms."),
    ("Geopolitical intelligence", "No admitted source; nothing is inferred."),
    ("Fundamentals (EGX and US)", "No admitted filings or fundamentals source connected to the platform."),
)


class MacroParseError(ValueError):
    pass


def parse_fred_csv(text, series_id):
    """[(date, Decimal)] ascending; blank or '.' values are skipped and counted."""
    lines = [line.strip() for line in text.strip().splitlines()]
    if not lines or lines[0].split(",") not in (["observation_date", series_id], ["DATE", series_id]):
        raise MacroParseError(f"unexpected header for {series_id}")
    observations, missing, previous = [], 0, None
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            raise MacroParseError(f"malformed row for {series_id}")
        try:
            day = date.fromisoformat(parts[0])
        except ValueError as exc:
            raise MacroParseError(f"bad date for {series_id}") from exc
        if previous is not None and day <= previous:
            raise MacroParseError(f"dates not strictly ascending for {series_id}")
        previous = day
        if parts[1] in ("", "."):
            missing += 1
            continue
        try:
            value = Decimal(parts[1])
        except InvalidOperation as exc:
            raise MacroParseError(f"bad value for {series_id}") from exc
        if not value.is_finite():
            raise MacroParseError(f"non-finite value for {series_id}")
        observations.append((day, value))
    return observations, missing


def _change(new, old, unit):
    if old is None:
        return None, None
    delta = new - old
    pct = None if unit == "%" or old == 0 else (delta / old * 100).quantize(Decimal("0.01"))
    return str(delta), (None if pct is None else str(pct))


def summarize(series, observations, as_of):
    """Point-in-time summary of one series as of `as_of` (later dates excluded)."""
    known = [(day, value) for day, value in observations if day <= as_of]
    excluded_future = len(observations) - len(known)
    if not known:
        return {"status": "UNAVAILABLE", "reason": "no observation on or before as-of",
                "excluded_future": excluded_future}
    day, value = known[-1]
    previous = known[-2][1] if len(known) > 1 else None
    lookback = known[-1 - CHANGE_LOOKBACK][1] if len(known) > CHANGE_LOOKBACK else None
    change_1, change_1_pct = _change(value, previous, series.unit)
    change_20, change_20_pct = _change(value, lookback, series.unit)
    age = (as_of - day).days
    return {"status": "AVAILABLE", "latest_date": day.isoformat(), "latest_value": str(value),
            "change_1": change_1, "change_1_pct": change_1_pct,
            "change_20": change_20, "change_20_pct": change_20_pct,
            "observations": len(known), "excluded_future": excluded_future, "age_days": age,
            "freshness": "CURRENT" if age <= series.max_age_days else "STALE"}


def derived_metrics(summaries):
    """Only metrics computed from same-dated published values."""
    two, ten = summaries.get("DGS2") or {}, summaries.get("DGS10") or {}
    if two.get("status") == "AVAILABLE" and ten.get("status") == "AVAILABLE" \
            and two["latest_date"] == ten["latest_date"]:
        spread = (Decimal(ten["latest_value"]) - Decimal(two["latest_value"])) * 100
        return [{"metric": "US 2s10s Treasury curve", "value": f"{spread:.0f}", "unit": "bp",
                 "date": ten["latest_date"], "inputs": ["DGS10", "DGS2"],
                 "shape": "INVERTED" if spread < 0 else "POSITIVE" if spread > 0 else "FLAT"}]
    return [{"metric": "US 2s10s Treasury curve", "value": None, "unit": "bp", "date": None,
             "inputs": ["DGS10", "DGS2"], "shape": "UNKNOWN",
             "reason": "2-year and 10-year latest observations are missing or not same-dated"}]


def build_report(fetched, *, as_of, generated_at):
    """fetched: series_id -> {text, sha256, retrieved_at, url} or {error}."""
    if generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    rows, summaries = [], {}
    for series in SERIES:
        source = fetched.get(series.series_id) or {"error": "not fetched"}
        row = {"series_id": series.series_id, "title": series.title, "unit": series.unit,
               "publisher": series.publisher, "group": series.group, "delivery": DELIVERY,
               "rights": RIGHTS, "max_age_days": series.max_age_days,
               "url": source.get("url"), "raw_sha256": source.get("sha256"),
               "retrieved_at": source.get("retrieved_at")}
        if "error" in source:
            summary = {"status": "UNAVAILABLE", "reason": str(source["error"])[:300]}
        else:
            try:
                observations, missing = parse_fred_csv(source["text"], series.series_id)
            except MacroParseError as exc:
                summary = {"status": "UNAVAILABLE", "reason": f"parse failed: {exc}"}
            else:
                summary = {**summarize(series, observations, as_of), "missing_values_skipped": missing}
        summaries[series.series_id] = summary
        rows.append({**row, **summary})
    return {"schema": SCHEMA, "as_of": as_of.isoformat(), "generated_at": generated_at.isoformat(),
            "live_money": False, "use": "CONTEXT_ONLY_NOT_A_SIGNAL", "series": rows,
            "derived": derived_metrics(summaries),
            "blocked": [{"capability": name, "reason": reason} for name, reason in BLOCKED]}


def valid_report(report):
    return (type(report) is dict and report.get("schema") == SCHEMA and report.get("live_money") is False
            and isinstance(report.get("series"), list) and isinstance(report.get("blocked"), list))
