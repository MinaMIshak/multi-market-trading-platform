"""EGX ranking report (EGX-RANK-v1) reader and renderers for TODAY / SWING / PRE-SURGE / PERFORMANCE.

The report is produced by app/egx_ranking_run.py and resolved as runtime input
``ranking`` (explicit path or snapshot bundle). It is shown as recorded:
nothing is recomputed, and a missing or malformed report shows UNAVAILABLE.
Classifications are Paper/Shadow research. A candidate is not a fill, and
LIVE_MONEY is disabled.
"""
from html import escape
import json
from pathlib import Path

from app.runtime_state import resolved_path

MAX_BYTES = 16 * 1024 * 1024
CLASS_ORDER = ("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST", "NO_TRADE")


def load_ranking():
    configured = resolved_path("ranking")
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
    if (type(report) is not dict or report.get("schema") != "egx-ranking-report-v1"
            or report.get("live_money") is not False or not isinstance(report.get("symbols"), list)):
        return None
    return report


def summary(report):
    if report is None:
        return {"status": "UNAVAILABLE"}
    return {"status": "AVAILABLE", "rank_version": report["rank_version"], "session": report["session"],
            "generated_at": report["generated_at"], "provider": report["provider"],
            "admission": report["admission"], "licensing": report["licensing"],
            "symbols_ranked": report["symbols_ranked"],
            "counts": {name: report["counts"].get(name, 0) for name in CLASS_ORDER},
            "performance": report.get("performance")}


def _cell(value):
    if value is None:
        return "<td>—</td>"
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value) if value else "—"
    return f"<td>{escape(str(value))}</td>"


def _header(report):
    return (f"<p>Rule {escape(report['rank_version'])} · session {escape(report['session'])} · "
            f"source {escape(report['provider'])} ({escape(report['admission'])}; "
            f"licensing: {escape(report['licensing'])}) · generated {escape(report['generated_at'])}. "
            "Paper/Shadow research classification only. A candidate is not a fill; "
            "LIVE MONEY DISABLED.</p>"
            + (f"<p>{escape(report['prepared_note'])} "
               f"({escape(report.get('next_session_basis') or '')})</p>"
               if report.get("prepared_note") else ""))


def _table(rows, columns, label):
    head = "".join(f'<th scope="col">{escape(title)}</th>' for title, _ in columns)
    body = "".join("<tr>" + "".join(_cell(row.get(key)) for _, key in columns) + "</tr>" for row in rows)
    return (f'<div class="table-scroll"><table aria-label="{escape(label)}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body or '<tr><td>none</td></tr>'}</tbody></table></div>")


EVIDENCE_COLUMNS = [
    ("Ticker", "ticker"), ("Company", "company"), ("Class", "classification"), ("Score", "score"),
    ("Freshness", "freshness"), ("Trend", "trend"), ("Momentum 20d %", "momentum_20_pct"),
    ("Breakout 20d", "breakout_20"), ("Volume ×20d", "volume_ratio_20"),
    ("Volume confirmed", "volume_confirmation"), ("Liquidity (avg value 20d, EGP)", "liquidity_avg_traded_value_20"),
    ("Entry zone", "entry_zone"), ("Stop", "stop"), ("Target 1", "target_1"), ("Target 2", "target_2"),
    ("R:R T1", "risk_reward_t1"), ("R:R T2", "risk_reward_t2"), ("Rejection reasons", "rejection_reasons"),
    ("Data warnings", "data_warnings"), ("Last session", "last_market_session"), ("Source", "source"),
]


def render_today(report):
    if report is None:
        return "<p>EGX ranking: UNAVAILABLE (no ranking report connected).</p>"
    counts = " · ".join(f"{name}: {report['counts'].get(name, 0)}" for name in CLASS_ORDER)
    top = [r for r in report["symbols"] if r.get("classification") != "NO_TRADE"][:10]
    return ('<section aria-label="EGX ranking summary"><h2>EGX ranking</h2>' + _header(report)
            + f"<p>{escape(counts)} · symbols ranked: {report['symbols_ranked']}</p>"
            + _table(top, EVIDENCE_COLUMNS[:5] + [("Entry zone", "entry_zone"), ("Stop", "stop"),
                                                   ("Target 1", "target_1"), ("Target 2", "target_2")],
                     "Top EGX classifications") + "</section>")


def render_swing(report):
    if report is None:
        return "<p>EGX ranking: UNAVAILABLE (no ranking report connected).</p>"
    lifecycles = report.get("lifecycles") or []
    life_columns = [("Candidate", "candidate_id"), ("Ticker", "ticker"), ("Session", "session"),
                    ("Class", "classification"), ("Status", "status"), ("Entry", "entry"),
                    ("R (gross)", "r_multiple_gross"), ("Net %", "net_return_pct"),
                    ("MAE R", "mae_r"), ("MFE R", "mfe_r"), ("Sessions", "sessions_held")]
    return ('<section aria-label="EGX ranking evidence"><h2>EGX ranking evidence (per symbol)</h2>'
            + _header(report) + _table(report["symbols"], EVIDENCE_COLUMNS, "EGX ranking evidence")
            + "<h2>System-generated Paper/Shadow candidates</h2>"
            "<p>Generated by the platform when the evidence prerequisites pass; human review is optional. "
            "Simulated next-session entry, costs and exits from stored bars only.</p>"
            + _table(lifecycles, life_columns, "System candidate lifecycles") + "</section>")


def render_pre_surge(report):
    if report is None:
        return ""
    rows = sorted((r for r in report["symbols"] if r.get("score") is not None
                   and (r.get("volume_ratio_20") or 0) >= 1.5 and (r.get("momentum_20_pct") or 0) > 0),
                  key=lambda r: (-(r.get("volume_ratio_20") or 0), r["ticker"]))
    columns = [("Ticker", "ticker"), ("Volume ×20d", "volume_ratio_20"), ("Momentum 20d %", "momentum_20_pct"),
               ("Breakout 20d", "breakout_20"), ("Trend", "trend"), ("Class", "classification"),
               ("Freshness", "freshness"), ("Rejection reasons", "rejection_reasons")]
    return ('<section aria-label="EGX volume expansion screen"><h2>Volume and momentum expansion screen</h2>'
            + _header(report) + "<p>Explainable screen: volume at least 1.5 × its 20-session average with "
            "positive 20-session momentum. It is NOT a prediction; class and rejection reasons still apply.</p>"
            + _table(rows, columns, "EGX volume expansion screen") + "</section>")


def render_performance(report):
    if report is None:
        return "<p>System Paper/Shadow performance: UNAVAILABLE.</p>"
    perf = report.get("performance") or {}
    rows = [{"metric": key, "value": value} for key, value in perf.items()]
    return ('<section aria-label="System Paper/Shadow performance"><h2>System Paper/Shadow performance</h2>'
            + _header(report) + "<p>Computed only from closed simulated lifecycles of system candidates. "
            "INSUFFICIENT_SAMPLE means fewer closed trades than the minimum sample; no metric is extrapolated.</p>"
            + _table(rows, [("Metric", "metric"), ("Value", "value")], "System performance") + "</section>")
