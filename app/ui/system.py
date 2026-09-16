"""Truthful engineering/data-readiness UI; no trading analytics are computed."""
from __future__ import annotations

import html


LABEL = "EXPERIMENTAL / PAPER ONLY"

BLOCKERS = (
    "Source historical availability is not proven.",
    "Approved human review bindings are absent.",
    "Complete exact-date XNYS universe evidence is absent.",
    "Exact-date identity evidence across the interval is absent.",
    "Complete bounded corporate-action coverage is absent.",
    "Every-calendar-date evidenced XNYS session records are absent.",
)

BOUNDARIES = (
    (
        "R1 Historical Evidence",
        "ENGINE READY",
        "Authentic historical timing and approved review still required.",
    ),
    (
        "US1 Historical Identity",
        "NO_GO",
        "Exact-date stable listing identity evidence is absent.",
    ),
    (
        "US2 Historical Sessions",
        "NO_GO",
        "Complete every-calendar-date XNYS session evidence is absent.",
    ),
    (
        "US3 Historical Daily",
        "RETAINED / NOT ADMITTED",
        "Authentic retained raw price bytes exist, but PIT admission remains blocked.",
    ),
    (
        "US4 Corporate Actions",
        "NO_GO",
        "Complete bounded action coverage and explicit negative coverage are absent.",
    ),
    (
        "US5A Historical Universe",
        "NO_GO",
        "Complete survivorship-safe exact-date universe evidence is absent.",
    ),
    (
        "US5B Retrospective PIT",
        "BLOCKED BY UPSTREAM",
        "Composition engine is implemented but required upstream evidence is incomplete.",
    ),
    (
        "US7B Historical Intraday",
        "NOT QUALIFIED",
        "Authentic opening-origin intraday execution evidence is not qualified.",
    ),
    (
        "US7C Paper Replay",
        "ENGINE READY / DATA BLOCKED",
        "Replay is implemented; historical execution claims still require authentic inputs and costs.",
    ),
    (
        "M7 Performance",
        "ENGINE READY",
        "Engineering surface is implemented; empirical performance is not established.",
    ),
    (
        "M8 / US8 Validation",
        "ENGINE READY / EMPIRICAL NOT RUN",
        "Walk-forward and holdout validation have not run on an admissible authentic package.",
    ),
    (
        "Shadow Portfolio",
        "NOT SCORED",
        "Authenticated valuation plumbing is implemented but remains valuation-only.",
    ),
)


def load_system_state() -> dict:
    """Return the explicit readiness snapshot currently documented by ER1."""
    return {
        "label": LABEL,
        "engineering_status": "ENGINEERING HANDOFF COMPLETE",
        "canonical_pit_admission": "NO_GO",
        "empirical_validation": "NOT RUN",
        "next_gate": "AUTHORIZED AUTHENTIC DATA / EVIDENCE",
        "blockers": BLOCKERS,
        "boundaries": BOUNDARIES,
    }


def _status_class(status: str) -> str:
    if status == "ENGINE READY":
        return "ok"
    if "NO_GO" in status or "BLOCKED" in status:
        return "stop"
    return "watch"


def _nav() -> str:
    return """
    <a class="tab" href="/">TODAY</a>
    <span class="tab">LIVE</span>
    <span class="tab">PRE-SURGE</span>
    <span class="tab">SWING</span>
    <a class="tab" href="/performance">PERFORMANCE</a>
    <span class="tab">RESEARCH</span>
    <a class="tab active" href="/system">SYSTEM</a>
    """


def render_system_dashboard(state: dict | None = None) -> str:
    """Render readiness facts only; never calculate signals or performance."""
    state = load_system_state() if state is None else state

    cards = (
        ("Engineering", state["engineering_status"]),
        ("Canonical PIT", state["canonical_pit_admission"]),
        ("Empirical Validation", state["empirical_validation"]),
        ("Next Gate", state["next_gate"]),
    )

    card_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(str(value))}</div>
        </div>
        """
        for label, value in cards
    )

    blocker_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in state["blockers"]
    )

    rows = "".join(
        f"""
        <tr>
          <td class="boundary">{html.escape(boundary)}</td>
          <td>
            <span class="{_status_class(status)}">
              {html.escape(status)}
            </span>
          </td>
          <td>{html.escape(detail)}</td>
        </tr>
        """
        for boundary, status, detail in state["boundaries"]
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EGX System Readiness</title>
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
.panel{{margin-top:20px;overflow:hidden}}
.panelhead{{padding:18px 20px;border-bottom:1px solid #1e3241}}
.panelbody{{padding:18px 20px}}
.label{{color:#7f93a3;font-size:12px;text-transform:uppercase}}
.value{{font-size:19px;margin-top:9px;font-weight:700}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:14px 18px;border-bottom:1px solid #172937;font-size:13px;vertical-align:top}}
th{{color:#758a9a;font-weight:600}}
.boundary{{font-weight:700;white-space:nowrap}}
.ok{{color:#75d6a0}}
.watch{{color:#e9b95f}}
.stop{{color:#ef8d8d}}
.notice{{margin-top:20px;padding:16px 18px;border:1px solid #493d20;background:#211c10;border-radius:9px;color:#e5c36c}}
ul{{margin:0;padding-left:22px}}
li{{margin:8px 0}}
@media(max-width:900px){{
  main,header,nav{{padding-left:16px;padding-right:16px}}
  .grid{{grid-template-columns:repeat(2,1fr)}}
  table{{min-width:900px}}
  .panel{{overflow:auto}}
}}
</style>
</head>
<body>
<header>
  <div class="top">
    <div>
      <h1>EGX Trading Platform</h1>
      <div class="sub">System · Data Readiness</div>
    </div>
    <div class="badge">{html.escape(state["label"])}</div>
  </div>
</header>

<nav>{_nav()}</nav>

<main>
  <div class="grid">{card_html}</div>

  <section class="panel">
    <div class="panelhead">
      <strong>Canonical Readiness Boundaries</strong>
    </div>
    <table>
      <thead>
        <tr>
          <th>Boundary</th>
          <th>Status</th>
          <th>Meaning</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </section>

  <section class="panel">
    <div class="panelhead">
      <strong>Current Admission Blockers</strong>
    </div>
    <div class="panelbody">
      <ul>{blocker_html}</ul>
    </div>
  </section>

  <div class="notice">
    Engineering readiness is not empirical strategy validation.
    This page does not calculate signals, recommendations, returns,
    hit rate, profitability, alpha, or real-money readiness.
    Canonical PIT and empirical validation remain blocked until
    admissible authentic evidence is available.
  </div>
</main>
</body>
</html>"""
