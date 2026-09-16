"""Truthful research-validation presentation; no evaluation is executed here."""
from __future__ import annotations

import html


LABEL = "EXPERIMENTAL / PAPER ONLY"


def load_research_state() -> dict:
    """
    Return the current empirical-validation UI state.

    M8/US8 engineering exists, but no admissible authentic PIT package
    or empirical runtime research report is currently available.
    """
    return {
        "label": LABEL,
        "available": False,
        "engine_status": "ENGINE READY",
        "canonical_pit_admission": "NO_GO",
        "runtime_report_status": "NO EMPIRICAL RESEARCH REPORT AVAILABLE",
        "development_oos": "NOT EXECUTED",
        "bootstrap_confidence": "NOT EXECUTED",
        "frozen_holdout": "NOT EXECUTED",
        "scenario_validation": "NOT EXECUTED",
        "final_evidence": "NOT EVALUATED",
        "next_gate": "ADMISSIBLE AUTHENTIC PIT PACKAGE",
        "reason": (
            "The M8 / US8 validation engine is implemented, but canonical "
            "PIT admission remains NO_GO. Real walk-forward, bootstrap, "
            "scenario and frozen-holdout validation have not been executed "
            "on an admissible authentic package."
        ),
    }


def _nav() -> str:
    return """
    <a class="tab" href="/">TODAY</a>
    <span class="tab">LIVE</span>
    <span class="tab">PRE-SURGE</span>
    <span class="tab">SWING</span>
    <a class="tab" href="/performance">PERFORMANCE</a>
    <a class="tab" href="/portfolio">PORTFOLIO</a>
    <a class="tab active" href="/research">RESEARCH</a>
    <a class="tab" href="/system">SYSTEM</a>
    """


def render_research_dashboard(
    state: dict | None = None,
) -> str:
    """Render research readiness only; never run validation from the UI."""
    state = load_research_state() if state is None else state

    cards = (
        ("Validation Engine", state["engine_status"]),
        ("Canonical PIT", state["canonical_pit_admission"]),
        ("Empirical Report", state["runtime_report_status"]),
        ("Final Evidence", state["final_evidence"]),
    )

    cards_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(str(value))}</div>
        </div>
        """
        for label, value in cards
    )

    stages = (
        (
            "Development OOS",
            state["development_oos"],
            "Purged rolling walk-forward development evaluation.",
        ),
        (
            "Bootstrap / Confidence",
            state["bootstrap_confidence"],
            "Development-only uncertainty analysis over completed OOS evidence.",
        ),
        (
            "Scenario Validation",
            state["scenario_validation"],
            "Declared execution-cost and slippage scenario coverage.",
        ),
        (
            "Frozen Holdout",
            state["frozen_holdout"],
            "Explicit future holdout evaluation after the development cycle.",
        ),
    )

    rows = "".join(
        f"""
        <tr>
          <td class="stage">{html.escape(stage)}</td>
          <td><span class="watch">{html.escape(status)}</span></td>
          <td>{html.escape(description)}</td>
        </tr>
        """
        for stage, status, description in stages
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EGX Research Validation</title>
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
.value{{font-size:18px;margin-top:9px;font-weight:700}}
.panel{{margin-top:20px;overflow:hidden}}
.panelhead{{padding:18px 20px;border-bottom:1px solid #1e3241}}
.panelbody{{padding:18px 20px}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:14px 18px;border-bottom:1px solid #172937;font-size:13px;vertical-align:top}}
th{{color:#758a9a;font-weight:600}}
.stage{{font-weight:700;white-space:nowrap}}
.watch{{color:#e9b95f}}
.stop{{color:#ef8d8d}}
.notice{{margin-top:20px;padding:16px 18px;border:1px solid #493d20;background:#211c10;border-radius:9px;color:#e5c36c}}
@media(max-width:900px){{
  main,header,nav{{padding-left:16px;padding-right:16px}}
  .grid{{grid-template-columns:repeat(2,1fr)}}
  table{{min-width:760px}}
  .panel{{overflow:auto}}
}}
</style>
</head>
<body>
<header>
  <div class="top">
    <div>
      <h1>EGX Trading Platform</h1>
      <div class="sub">Research · M8 / US8 Validation</div>
    </div>
    <div class="badge">{html.escape(str(state["label"]))}</div>
  </div>
</header>

<nav>{_nav()}</nav>

<main>
  <div class="grid">{cards_html}</div>

  <section class="panel">
    <div class="panelhead">
      <strong>Empirical Validation Pipeline</strong>
    </div>
    <table>
      <thead>
        <tr>
          <th>Stage</th>
          <th>Status</th>
          <th>Meaning</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </section>

  <section class="panel">
    <div class="panelhead">
      <strong>Current Gate</strong>
    </div>
    <div class="panelbody">
      <p>
        <strong>Next required input:</strong>
        {html.escape(str(state["next_gate"]))}
      </p>
      <p>{html.escape(str(state["reason"]))}</p>
    </div>
  </section>

  <div class="notice">
    This page does not run research evaluation and does not synthesize
    OOS results, holdout results, bootstrap confidence intervals,
    hit rate, expectancy, profit factor, alpha, or profitability.
    Engineering readiness is not empirical validation or real-money readiness.
  </div>
</main>
</body>
</html>"""
