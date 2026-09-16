"""Truthful shadow-portfolio presentation; no valuation is invented."""
from __future__ import annotations

import html


LABEL = "EXPERIMENTAL / PAPER ONLY"


def load_portfolio_state() -> dict:
    """
    Return the current UI runtime state.

    No authenticated runtime shadow snapshot reader/path is configured yet,
    so portfolio values remain explicitly unavailable rather than inferred.
    """
    return {
        "label": LABEL,
        "available": False,
        "source_status": "NO AUTHENTICATED RUNTIME SNAPSHOT SOURCE",
        "valuation_status": "UNAVAILABLE",
        "performance_status": "VALUATION ONLY / NOT SCORED",
        "gross_marked_nav": None,
        "cash": None,
        "currency": None,
        "open_position_count": None,
        "snapshot_date_utc": None,
        "reason": (
            "The authenticated shadow valuation engine is implemented, "
            "but this UI has no configured runtime source for audited "
            "daily portfolio snapshots."
        ),
    }


def _display(value: object) -> str:
    if value is None:
        return "UNAVAILABLE"
    return html.escape(str(value))


def _nav() -> str:
    return """
    <a class="tab" href="/">TODAY</a>
    <span class="tab">LIVE</span>
    <span class="tab">PRE-SURGE</span>
    <span class="tab">SWING</span>
    <a class="tab" href="/performance">PERFORMANCE</a>
    <a class="tab active" href="/portfolio">PORTFOLIO</a>
    <span class="tab">RESEARCH</span>
    <a class="tab" href="/system">SYSTEM</a>
    """


def render_portfolio_dashboard(
    state: dict | None = None,
) -> str:
    """Render supplied authenticated state or an explicit unavailable state."""
    state = load_portfolio_state() if state is None else state

    cards = (
        ("Gross Marked NAV", _display(state.get("gross_marked_nav"))),
        ("Cash", _display(state.get("cash"))),
        ("Open Positions", _display(state.get("open_position_count"))),
        ("Currency", _display(state.get("currency"))),
    )

    cards_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{value}</div>
        </div>
        """
        for label, value in cards
    )

    snapshot = _display(state.get("snapshot_date_utc"))

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EGX Shadow Portfolio</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#071019;color:#e9f0f5;font-family:Arial,sans-serif}}
header{{padding:26px 34px;background:#0b1620;border-bottom:1px solid #1d2b38}}
.top{{display:flex;justify-content:space-between;gap:20px;align-items:center}}
h1{{margin:0;font-size:25px}}
.sub{{color:#8395a5;margin-top:7px}}
.badge{{padding:8px 12px;border:1px solid #493d20;border-radius:20px;color:#e5c36c;font-size:12px}}
nav{{display:flex;gap:8px;padding:14px 34px;background:#0b1620;border-bottom:1px solid #1d2b38;overflow:auto}}
.tab{{padding:9px 13px;color:#748696;font-size:12px;white-space:nowrap;text-decoration:none}}
.active{{color:white;background:#173147;border-radius:7px}}
main{{padding:28px 34px;max-width:1500px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.card,.panel{{background:#0d1b26;border:1px solid #1e3241;border-radius:10px}}
.card{{padding:18px}}
.label{{color:#7f93a3;font-size:12px;text-transform:uppercase}}
.value{{font-size:22px;margin-top:9px;font-weight:700}}
.panel{{margin-top:20px;padding:20px}}
.status{{display:grid;grid-template-columns:220px 1fr;gap:12px}}
.status dt{{color:#7f93a3}}
.status dd{{margin:0}}
.watch{{color:#e9b95f}}
.notice{{margin-top:20px;padding:16px 18px;border:1px solid #493d20;background:#211c10;border-radius:9px;color:#e5c36c}}
@media(max-width:900px){{
  main,header,nav{{padding-left:16px;padding-right:16px}}
  .grid{{grid-template-columns:repeat(2,1fr)}}
  .status{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<header>
  <div class="top">
    <div>
      <h1>EGX Trading Platform</h1>
      <div class="sub">Shadow Portfolio · Authenticated Valuation</div>
    </div>
    <div class="badge">{html.escape(str(state["label"]))}</div>
  </div>
</header>

<nav>{_nav()}</nav>

<main>
  <div class="grid">{cards_html}</div>

  <section class="panel">
    <h2>Portfolio Data State</h2>
    <dl class="status">
      <dt>Runtime source</dt>
      <dd class="watch">{html.escape(str(state["source_status"]))}</dd>

      <dt>Valuation</dt>
      <dd>{html.escape(str(state["valuation_status"]))}</dd>

      <dt>Performance</dt>
      <dd>{html.escape(str(state["performance_status"]))}</dd>

      <dt>Snapshot date UTC</dt>
      <dd>{snapshot}</dd>
    </dl>

    <p>{html.escape(str(state["reason"]))}</p>
  </section>

  <div class="notice">
    No NAV, cash balance, position count, return, P&amp;L, hit rate,
    or performance metric is synthesized when an authenticated runtime
    snapshot is unavailable. Shadow portfolio valuation remains
    EXPERIMENTAL / PAPER ONLY and NOT SCORED.
  </div>
</main>
</body>
</html>"""
