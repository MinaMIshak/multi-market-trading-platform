from __future__ import annotations

import html
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.base_trading_calendar import BaseTradingCalendarPolicy
from app.core.schedule import CalendarTruth
from app.runtime_state import resolved_path
from app.domain import MarketSession, MarketSessionStatus


def _session_truth(
    row: sqlite3.Row,
    market_date: date,
) -> tuple[MarketSession, CalendarTruth]:
    """Validate a market_sessions row against its payload; map to calendar truth."""
    session = MarketSession.model_validate_json(
        row["payload_json"]
    )

    if session.market_date != market_date:
        raise ValueError(
            "market-session payload date "
            "does not match ledger date"
        )

    if row["status"] != session.status.value:
        raise ValueError(
            "market-session status column "
            "does not match canonical payload"
        )

    if session.status == MarketSessionStatus.VERIFIED:
        return session, CalendarTruth.VERIFIED_TRADING_DAY

    if session.status in {
        MarketSessionStatus.HOLIDAY,
        MarketSessionStatus.WEEKEND,
    }:
        return session, CalendarTruth.VERIFIED_NON_TRADING_DAY

    return session, CalendarTruth.UNVERIFIED


def _daily_freshness(
    newest_market_date: date,
    as_of: date,
    truths: dict[date, CalendarTruth],
) -> str:
    """CURRENT/STALE from verified sessions plus the fixed Fri/Sat EGX weekend.

    STALE: a verified trading session lies strictly between the newest bar
    and as_of. CURRENT: every date in that gap is a verified non-trading day.
    Otherwise UNKNOWN (incomplete calendar, or bars dated after as_of).
    """
    if newest_market_date > as_of:
        return "UNKNOWN"

    gap = [
        newest_market_date + timedelta(days=offset)
        for offset in range(1, (as_of - newest_market_date).days)
    ]

    # Dates without a stored session row fall back to the deterministic weekly
    # boundary only: Friday/Saturday are known EGX weekends (the same rule the
    # calendar backfill writes). Sunday-Thursday without evidence stay unverified.
    policy = BaseTradingCalendarPolicy()
    truths = {
        **{
            day: CalendarTruth.VERIFIED_NON_TRADING_DAY
            for day in gap
            if day not in truths
            and policy.classify(day) == MarketSessionStatus.WEEKEND
        },
        **truths,
    }

    if any(
        truths.get(day) == CalendarTruth.VERIFIED_TRADING_DAY
        for day in gap
    ):
        return "STALE"

    if all(
        truths.get(day) == CalendarTruth.VERIFIED_NON_TRADING_DAY
        for day in gap
    ):
        return "CURRENT"

    return "UNKNOWN"


def _read_security_master(con: sqlite3.Connection) -> dict:
    """Aggregate identity/reference rows; never prices or dated membership."""
    totals = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            MAX(updated_at) AS latest_updated_at,
            MAX(source_market_date) AS latest_source_market_date
        FROM canonical_instruments
        """
    ).fetchone()

    by_type = con.execute(
        """
        SELECT instrument_type, COUNT(*) AS count
        FROM canonical_instruments
        GROUP BY instrument_type
        ORDER BY instrument_type
        """
    ).fetchall()

    providers = con.execute(
        """
        SELECT DISTINCT source_provider
        FROM canonical_instruments
        ORDER BY source_provider
        """
    ).fetchall()

    return {
        "total_instruments": totals["total"],
        "by_type": {
            row["instrument_type"]: row["count"]
            for row in by_type
        },
        "source_providers": [
            row["source_provider"]
            for row in providers
        ],
        "latest_snapshot_updated_at": totals["latest_updated_at"],
        "latest_source_market_date": totals["latest_source_market_date"],
    }


def load_security_master_summary(
    database_path: Path | None = None,
) -> dict | None:
    """Read-only identity summary for the product shell; None when unreadable."""
    path = database_path if database_path is not None else Path(resolved_path("database"))

    try:
        with closing(sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
        )) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            return _read_security_master(con)
    except Exception:
        return None


def load_validated_daily_observations(
    database_path: Path | None = None,
    market_date: date | None = None,
) -> list[dict] | None:
    """Read-only VALIDATED daily-canonical artifact rows; None when unreadable.

    Dated historical observations with calendar-derived freshness as of the
    Cairo market date; no rights, readiness or signal claim.
    """
    path = database_path if database_path is not None else Path(resolved_path("database"))

    as_of = (
        market_date
        if market_date is not None
        else datetime.now(
            ZoneInfo("Africa/Cairo")
        ).date()
    )

    try:
        with closing(sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
        )) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            rows = con.execute(
                """
                SELECT
                    canonical_symbol,
                    provider,
                    source_snapshot_date,
                    oldest_market_date,
                    newest_market_date,
                    valid_bar_count,
                    quarantined_bar_count
                FROM daily_canonical_artifacts
                WHERE status='VALIDATED'
                ORDER BY canonical_symbol, provider
                """
            ).fetchall()

            truths = {}
            for session_row in con.execute(
                """
                SELECT market_date, status, payload_json
                FROM market_sessions
                WHERE market_date <= ?
                """,
                (as_of.isoformat(),),
            ).fetchall():
                session_date = date.fromisoformat(
                    session_row["market_date"]
                )
                _, truths[session_date] = _session_truth(
                    session_row,
                    session_date,
                )

            return [
                {
                    **dict(row),
                    "freshness": _daily_freshness(
                        date.fromisoformat(row["newest_market_date"]),
                        as_of,
                        truths,
                    ),
                }
                for row in rows
            ]
    except Exception:
        return None


def load_today_state(
    database_path: Path | None = None,
    market_date: date | None = None,
) -> dict:
    path = database_path if database_path is not None else Path(resolved_path("database"))

    target_market_date = (
        market_date
        if market_date is not None
        else datetime.now(
            ZoneInfo("Africa/Cairo")
        ).date()
    )

    state = {
        "available": False,
        "symbols": [],
        "counts": {},
        "security_master": None,
        "error": None,
    }

    try:
        with closing(sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
        )) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")

            rows = con.execute(
                """
                SELECT
                    canonical_symbol,
                    provider,
                    provider_symbol,
                    source_snapshot_date,
                    oldest_market_date,
                    newest_market_date,
                    valid_bar_count,
                    quarantined_bar_count,
                    status
                FROM daily_canonical_artifacts
                WHERE status='VALIDATED'
                ORDER BY canonical_symbol
                """
            ).fetchall()

            symbols = [
                dict(row) for row in rows
            ]

            market_session_row = con.execute(
                """
                SELECT
                    status,
                    payload_json
                FROM market_sessions
                WHERE market_date = ?
                """,
                (
                    target_market_date.isoformat(),
                ),
            ).fetchone()

            market_session = {
                "market_date":
                    target_market_date.isoformat(),
                "status": None,
                "calendar_truth":
                    CalendarTruth.UNVERIFIED.value,
                "data_verified_at": None,
            }

            if market_session_row is not None:
                session, calendar_truth = _session_truth(
                    market_session_row,
                    target_market_date,
                )

                market_session = {
                    "market_date":
                        session.market_date.isoformat(),
                    "status":
                        session.status.value,
                    "calendar_truth":
                        calendar_truth.value,
                    "data_verified_at": (
                        session
                        .data_verified_at
                        .isoformat()
                        if (
                            session.data_verified_at
                            is not None
                        )
                        else None
                    ),
                }

            counts = {
                "ingestions": con.execute(
                    "SELECT COUNT(*) "
                    "FROM data_ingestions"
                ).fetchone()[0],
                "daily_artifacts": len(rows),
                "daily_sources": con.execute(
                    "SELECT COUNT(*) "
                    "FROM daily_canonical_sources"
                ).fetchone()[0],
                "index_artifacts": con.execute(
                    "SELECT COUNT(*) "
                    "FROM canonical_data_artifacts"
                ).fetchone()[0],
                "quarantined": sum(
                    row["quarantined_bar_count"]
                    for row in rows
                ),
            }

            security_master = _read_security_master(con)

            integrity = con.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]

            if integrity != "ok":
                raise ValueError("database integrity check failed")

        state.update(
            available=True,
            symbols=symbols,
            counts=counts,
            security_master=security_master,
            integrity=integrity,
            market_session=market_session,
        )

    except Exception as exc:
        state["error"] = type(exc).__name__

    return state


def render_today_dashboard(
    state: dict,
    *,
    shadow_state: dict | None = None,
) -> str:
    now = datetime.now(
        ZoneInfo("Africa/Cairo")
    )

    counts = state.get("counts", {})
    symbols = state.get("symbols", [])

    market_session = (
        state.get("market_session")
        or {}
    )

    market_status = (
        market_session.get("status")
        or "NO RECORD"
    )

    calendar_truth = market_session.get(
        "calendar_truth",
        CalendarTruth.UNVERIFIED.value,
    )

    security_master = state.get("security_master")

    cards = [
        # Rows are per-snapshot artifacts; one symbol may have several.
        (
            "Validated Symbols",
            len({r["canonical_symbol"] for r in symbols}),
        ),
        (
            "Daily Artifacts",
            counts.get("daily_artifacts", 0),
        ),
        (
            "Index Artifacts",
            counts.get("index_artifacts", 0),
        ),
        (
            "Quarantined Bars",
            counts.get("quarantined", 0),
        ),
        (
            "Market Session",
            market_status,
        ),
        (
            "Calendar Truth",
            calendar_truth,
        ),
        (
            "Security Master Identities",
            security_master["total_instruments"]
            if security_master is not None
            else "NOT AVAILABLE",
        ),
    ]

    card_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(str(value))}</div>
        </div>
        """
        for label, value in cards
    )

    row_html = "".join(
        f"""
        <tr>
          <td class="ticker">{html.escape(r["canonical_symbol"])}</td>
          <td><span class="ok">VALIDATED</span></td>
          <td>{html.escape(r["provider"])}</td>
          <td>{html.escape(r["newest_market_date"])}</td>
          <td>{html.escape(str(r["valid_bar_count"]))}</td>
          <td>{html.escape(str(r["quarantined_bar_count"]))}</td>
          <td><span class="watch">NO ADMITTED SETUP</span></td>
        </tr>
        """
        for r in symbols
    )

    if not row_html:
        row_html = """
        <tr>
          <td colspan="7">
            No validated daily data available.
          </td>
        </tr>
        """

    db_state = (
        "ONLINE"
        if state.get("available")
        else "UNAVAILABLE"
    )

    integrity = state.get(
        "integrity",
        "unknown",
    )

    collection = (shadow_state or {}).get("collection")
    if collection is None:
        paper_content = '<p>UNAVAILABLE / NO AUDITED COLLECTION</p>'
    else:
        paper_content = (
            f'<p>Frozen {html.escape(collection["market"])} record for '
            f'{html.escape(collection["market_date"])} · '
            f'Information cutoff: {html.escape(collection["information_cutoff"])}</p>'
            '<ul>' + ''.join(
                f'<li>{html.escape(item["ticker"])} · {html.escape(item["decision_status"])}</li>'
                for item in collection['candidates']
            ) + '</ul>'
        )
        if not collection['candidates']:
            paper_content += '<p>Explicit frozen collection contains zero candidates; NOT SCORED.</p>'
    if security_master is None:
        security_master_html = (
            "<p>NOT AVAILABLE. Security master identities were not read this cycle.</p>"
        )
    else:
        type_rows = "".join(
            f"<li>{html.escape(str(instrument_type))}: "
            f"{html.escape(str(count))}</li>"
            for instrument_type, count in security_master["by_type"].items()
        ) or "<li>No instruments recorded.</li>"

        providers = (
            ", ".join(
                html.escape(str(p))
                for p in security_master["source_providers"]
            )
            or "none"
        )

        def _or_unknown(value):
            return "UNKNOWN" if value is None else html.escape(str(value))

        security_master_html = f"""
        <p>{html.escape(str(security_master["total_instruments"]))} instruments
           recorded from source(s): {providers}.</p>
        <ul>{type_rows}</ul>
        <p>Latest identity snapshot capture:
           {_or_unknown(security_master["latest_snapshot_updated_at"])} ·
           Latest source market date:
           {_or_unknown(security_master["latest_source_market_date"])}</p>
        """

    missed = (shadow_state or {}).get('missed')
    if missed is not None:
        paper_content += (
            f'<p>MISSED / NOT SCORED: {html.escape(missed["market"])} '
            f'{html.escape(missed["market_date"])} · Record {html.escape(missed["record_id"])} · '
            f'{html.escape(missed["reason"])}. No candidates reconstructed.</p>'
        )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EGX Trading Platform</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#071019;color:#e9f0f5;font-family:Arial,sans-serif}}
header{{padding:26px 34px;border-bottom:1px solid #1d2b38;background:#0b1620}}
.top{{display:flex;justify-content:space-between;gap:20px;align-items:center}}
h1{{margin:0;font-size:25px}} .sub{{color:#8395a5;margin-top:7px}}
.badge{{padding:8px 12px;border:1px solid #2b4557;border-radius:20px;color:#8bd5b0;font-size:12px}}
nav{{display:flex;gap:8px;padding:14px 34px;background:#0b1620;border-bottom:1px solid #1d2b38;overflow:auto}}
.tab{{padding:9px 13px;color:#748696;font-size:12px;white-space:nowrap;text-decoration:none}}
.active{{color:white;background:#173147;border-radius:7px}}
main{{padding:28px 34px;max-width:1500px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.card{{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:18px}}
.label{{color:#7f93a3;font-size:12px;text-transform:uppercase}}
.value{{font-size:29px;margin-top:9px;font-weight:700}}
.panel{{margin-top:20px;background:#0d1b26;border:1px solid #1e3241;border-radius:10px;overflow:hidden}}
.panelhead{{padding:18px 20px;display:flex;justify-content:space-between;border-bottom:1px solid #1e3241}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:14px 18px;border-bottom:1px solid #172937;font-size:13px}}
th{{color:#758a9a;font-weight:600}}
.ticker{{font-weight:700;font-size:15px}}
.ok{{color:#75d6a0}} .watch{{color:#e9b95f}}
.notice{{margin-top:20px;padding:16px 18px;border:1px solid #493d20;background:#211c10;border-radius:9px;color:#e5c36c}}
.footer{{margin-top:18px;color:#657988;font-size:12px}}
@media(max-width:800px){{main,header,nav{{padding-left:16px;padding-right:16px}}.grid{{grid-template-columns:repeat(2,1fr)}}table{{min-width:800px}}.panel{{overflow:auto}}}}
</style>
</head>
<body>
<header>
  <div class="top">
    <div>
      <h1>EGX Trading Platform</h1>
      <div class="sub">Tomorrow-Ready · Paper / Observation Mode</div>
    </div>
    <div class="badge">{db_state} · DB {html.escape(str(integrity))}</div>
  </div>
</header>

<nav>
  <div class="tab active">TODAY</div>
  <div class="tab">LIVE</div>
  <div class="tab">PRE-SURGE</div>
  <div class="tab">SWING</div>
  <a class="tab" href="/performance">PERFORMANCE</a>
  <a class="tab" href="/shadow">PAPER CANDIDATES</a>
  <div class="tab">RESEARCH</div>
  <a class="tab" href="/system">SYSTEM</a>
</nav>

<main>
  <div class="grid">{card_html}</div>

  <section class="panel">
    <div class="panelhead">
      <strong>Validated Daily Data Inventory</strong>
      <span>{now.strftime("%Y-%m-%d %H:%M")} Cairo</span>
    </div>

    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Data State</th>
          <th>Provider</th>
          <th>Latest Bar</th>
          <th>Valid</th>
          <th>Quarantine</th>
          <th>Trading State</th>
        </tr>
      </thead>
      <tbody>{row_html}</tbody>
    </table>
  </section>

  <section class="panel" style="padding:18px 20px">
    <h2>Security master identities</h2>
    <p>Instrument identity/reference data only &mdash; ticker, name and provider
       alias mapping. NOT price, NOT a trading signal, and NOT proof of
       dated exchange membership or index constituency.</p>
    {security_master_html}
  </section>

  <section class="panel" style="padding:18px 20px">
    <h2>Frozen paper candidates</h2>
    <p>EXPERIMENTAL / PAPER ONLY. Freshness NOT ESTABLISHED.
       These dated declarations are not current signals, fills or positions.</p>
    {paper_content}
    <p><a href="/shadow">View audited candidates, execution observations and native portfolio</a></p>
  </section>

  <div class="notice">
    Structural data validation does not establish freshness or point-in-time
    universe eligibility. Research engines remain UNVALIDATED and execution-disabled.
    Current trading signals are unavailable; frozen paper admissions remain separate.
  </div>

  <div class="footer">
    Raw ingestions: {counts.get("ingestions", 0)}
    · Daily source links: {counts.get("daily_sources", 0)}
    · Read-only dashboard data path
  </div>
</main>
</body>
</html>"""


def today_state() -> dict:
    return load_today_state()
