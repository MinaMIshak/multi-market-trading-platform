"""US research output (US-RANK-v1) for TODAY / SWING / PRE-SURGE / PERFORMANCE.

Renders the recorded US ranking report only, with the shared dashboard
components. US evidence is never merged with EGX evidence. LIVE MONEY DISABLED.
"""
import json
from collections import Counter
from html import escape
from pathlib import Path

from app.runtime_state import resolved_path
from app.ui.dashboard import (details, render_candidate_table, render_cards, render_insights, render_legend,
                              render_no_trade, render_session_context)

SCHEMA = "us-ranking-report-v1"
MAX_BYTES = 16 * 1024 * 1024


def load_us_ranking():
    configured = resolved_path("us_ranking")
    if not configured or not Path(configured).is_absolute():
        return None
    try:
        with Path(configured).open("rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            return None
        report = json.loads(payload)
    except (OSError, ValueError):
        return None
    if (type(report) is not dict or report.get("schema") != SCHEMA or report.get("live_money") is not False
            or report.get("market") != "US" or not isinstance(report.get("symbols"), list)):
        return None
    return report


def summary(report):
    if report is None:
        return {"status": "UNAVAILABLE"}
    return {"status": "AVAILABLE", "rank_version": report["rank_version"], "session": report["session"],
            "generated_at": report["generated_at"], "provider": report["provider"], "admission": report["admission"],
            "licensing": report["licensing"], "symbols_ranked": report["symbols_ranked"], "counts": report["counts"],
            "acquisition": report.get("acquisition"), "freshness": report.get("freshness"),
            "sessions": (report.get("sessions") or {}).get("status"),
            "cross_check": (report.get("cross_check") or {}).get("counts"), "performance": report.get("performance")}


def coverage(report):
    """Card inputs derived from the US report itself (US has no shared coverage reader)."""
    if report is None:
        return None
    acquired = (report.get("acquisition") or {}).get("ACQUIRED", 0)
    current = Counter(r.get("freshness") for r in report["symbols"]).get("CURRENT", 0)
    return [{"level": "admitted_symbols", "count": acquired if report["admission"] == "ADMITTED" else 0},
            {"level": "admitted_current_symbols", "count": current if report["admission"] == "ADMITTED" else 0}]


def _evidence(report):
    universe, sessions, checks = report.get("universe") or {}, report.get("sessions") or {}, report.get("cross_check") or {}
    rows = [("Universe rule", universe.get("rule")), ("Universe size / eligible",
                                                      f'{universe.get("size")} / {universe.get("eligible")}'),
            ("Liquidity floor (30-day avg $ volume)", universe.get("floor_avg_dollar_volume_30d")),
            ("Acquisition", ", ".join(f"{k} {v}" for k, v in (report.get("acquisition") or {}).items())),
            ("Session verification", f'{sessions.get("status")} · expected {sessions.get("expected_session")} · '
                                     f'conflicts {len(sessions.get("conflicts") or {})}'),
            ("Cross-check (Yahoo sample)", ", ".join(f"{k} {v}" for k, v in (checks.get("counts") or {}).items())
             + f' · tolerance {checks.get("tolerance_pct")}%'),
            ("Costs (lifecycle simulation)", f'slippage {report["costs"]["slippage"]}, commission '
                                             f'{report["costs"]["commission"]} per side')]
    return ("<table><tbody>" + "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in rows)
            + "</tbody></table>")


def _lifecycles(report):
    rows = "".join(f'<tr><td><span class="tk">{escape(item["ticker"])}</span></td><td>{escape(item["session"])}</td>'
                   f'<td><span class="badge {escape(item["classification"])}">{escape(item["classification"])}</span></td>'
                   f'<td>{escape(item["status"])}</td><td class="num">{escape(str(item.get("entry") or "—"))}</td>'
                   f'<td class="num">{escape(str(item.get("net_return_pct") or "—"))}</td></tr>'
                   for item in report.get("lifecycles") or [])
    return ('<section aria-label="US Paper/Shadow lifecycles"><h2>US system-generated Paper/Shadow candidates</h2>'
            '<p class="muted">Simulated from bars after the candidate session only; PENDING_ENTRY until the next '
            'NYSE session. A candidate is not a fill.</p><div class="table-wrap"><table><thead><tr><th>Ticker</th>'
            '<th>Session</th><th>Class</th><th>Status</th><th class="num">Entry</th><th class="num">Net %</th></tr>'
            f'</thead><tbody>{rows or "<tr><td colspan=6>none yet</td></tr>"}</tbody></table></div></section>')


def _performance(report):
    perf = report.get("performance") or {}
    items = "".join(f"<li>{escape(str(k))}: {escape(str(v))}</li>" for k, v in perf.items())
    return f'<section aria-label="US performance"><h2>US Paper/Shadow performance</h2><ul>{items}</ul></section>'


def _volume_screen(report):
    rows = [r for r in report["symbols"] if (r.get("volume_ratio_20") or 0) >= 1.5 and (r.get("momentum_20_pct") or 0) > 0]
    body = "".join(f'<tr><td><span class="tk">{escape(r["ticker"])}</span><span class="co">{escape(r.get("company") or "")}'
                   f'</span></td><td class="num">{r["volume_ratio_20"]}×</td><td class="num">{r["momentum_20_pct"]}</td>'
                   f'<td><span class="badge {escape(r["classification"])}">{escape(r["classification"])}</span></td></tr>'
                   for r in rows)
    return ('<section aria-label="US volume expansion screen"><h2>US volume and momentum expansion screen</h2>'
            '<p class="muted">Volume ≥ 1.5× its 20-session average with positive 20-session momentum. Explainable '
            'screen, not a prediction.</p><div class="table-wrap"><table><thead><tr><th>Symbol</th>'
            '<th class="num">Volume ×20s</th><th class="num">Mom. 20s %</th><th>Class</th></tr></thead>'
            f'<tbody>{body or "<tr><td colspan=4>none</td></tr>"}</tbody></table></div></section>')


def render_us(report, section):
    if report is None:
        return ('<section aria-label="US research"><h2>US</h2><p>US ranking: UNAVAILABLE (no verified US ranking '
                'report in the runtime state). US readiness is not claimed.</p></section>')
    content = '<section aria-label="US research"><h2>US market (NYSE / Nasdaq)</h2>'
    content += render_session_context(report)
    if section in ("TODAY", "SWING"):
        content += render_cards(report, coverage(report))
        content += render_insights(report)
        content += render_candidate_table(report, table_id=f"us-{section.lower()}-candidates",
                                          title="US candidates and watchlist")
        content += render_legend().replace("EGX-RANK-v1", "US-RANK-v1 (EGX-RANK-v1 rules, US liquidity profile)")
        content += render_no_trade(report)
    if section == "SWING":
        content += _lifecycles(report)
    if section == "PRE-SURGE":
        content += _volume_screen(report)
    if section == "PERFORMANCE":
        content += _performance(report)
    content += details("US data evidence (universe, sessions, cross-check, costs)", _evidence(report))
    return content + "</section>"
