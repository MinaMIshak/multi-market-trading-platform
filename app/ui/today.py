from __future__ import annotations

import html
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def load_today_state() -> dict:
    path = Path(
        os.getenv(
            "EGX_DB_PATH",
            "/app/data/platform.db",
        )
    )

    state = {
        "available": False,
        "symbols": [],
        "counts": {},
        "error": None,
    }

    try:
        con = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
        )
        con.row_factory = sqlite3.Row

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

        state["symbols"] = [
            dict(row) for row in rows
        ]

        state["counts"] = {
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

        state["integrity"] = con.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

        state["available"] = True
        con.close()

    except Exception as exc:
        state["error"] = type(exc).__name__

    return state


def render_today_dashboard(
    state: dict,
) -> str:
    now = datetime.now(
        ZoneInfo("Africa/Cairo")
    )

    counts = state.get("counts", {})
    symbols = state.get("symbols", [])

    cards = [
        ("Validated Symbols", len(symbols)),
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
    ]

    card_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{value}</div>
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
          <td>{r["valid_bar_count"]}</td>
          <td>{r["quarantined_bar_count"]}</td>
          <td><span class="watch">NO SETUP ENGINE</span></td>
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
  <a class="tab" href="/portfolio">PORTFOLIO</a>
  <a class="tab" href="/research">RESEARCH</a>
  <a class="tab" href="/system">SYSTEM</a>
</nav>

<main>
  <div class="grid">{card_html}</div>

  <section class="panel">
    <div class="panelhead">
      <strong>Validated Daily Universe</strong>
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

  <div class="notice">
    Trading signals are intentionally disabled until the validated
    strategy engine is connected. No BUY/SELL recommendation shown here
    is synthetic or inferred.
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
