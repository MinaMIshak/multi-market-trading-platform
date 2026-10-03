"""EGX champion vs challenger experiment (EGX-EXP-v1) views: PERFORMANCE, LIVE, SYSTEM, ticker details.

Renders the recorded experiment report only. The champion (V1) stays the
champion; nothing here promotes an arm. LIVE MONEY DISABLED.
"""
import json
from html import escape
from pathlib import Path

from app.runtime_state import resolved_path

SCHEMA = "egx-experiment-report-v1"
MAX_BYTES = 16 * 1024 * 1024
METRICS = (("eligible_setups", "Eligible setups"), ("blocked", "Blocked"), ("simulated_trades", "Simulated trades"),
           ("closed", "Closed"), ("checkpoint", "Sample checkpoint"), ("win_rate", "Win rate"),
           ("expectancy_r", "Expectancy (R)"), ("expectancy_ci95_approx", "Expectancy 95% CI (approx.)"),
           ("median_r", "Median R"), ("average_winner_r", "Average winner (R)"),
           ("average_loser_r", "Average loser (R)"), ("profit_factor", "Profit factor"),
           ("cumulative_r", "Cumulative R"), ("max_drawdown_r", "Max drawdown (R)"), ("t1_hit_rate", "T1 hit rate"),
           ("t2_hit_rate", "T2 hit rate"), ("stop_hit_rate", "Stop hit rate"), ("average_mae_r", "Average MAE (R)"),
           ("average_mfe_r", "Average MFE (R)"), ("average_holding_sessions", "Average holding (sessions)"),
           ("expectancy_r_double_costs", "Expectancy at 2× costs (R)"), ("pnl_egp_closed", "V2D closed P&L (EGP)"),
           ("return_on_model_capital_pct", "V2D return on model capital (%)"))


def load_experiment():
    configured = resolved_path("egx_experiment")
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
    if type(report) is not dict or report.get("schema") != SCHEMA or report.get("live_money") is not False:
        return None
    return report


def summary(report):
    if report is None:
        return {"status": "UNAVAILABLE"}
    return {"status": "AVAILABLE", "strategy": report["strategy"], "config_version": report["config_version"],
            "session": report["session"], "generated_at": report["generated_at"], "champion": report["champion"],
            "health": report["health"]["status"], "decisions": report["health"]["decisions"],
            "closed_by_arm": {arm: perf.get("closed") for arm, perf in report["performance"].items()}}


def _u(value):
    return "UNKNOWN" if value is None or value == "" else str(value)


def attach(ranking_report, experiment):
    """Copy of the EGX ranking report whose rows carry the V2D view for the same session (never the class)."""
    if ranking_report is None or experiment is None or experiment.get("session") != ranking_report.get("session"):
        return ranking_report
    view = experiment.get("current") or {}
    return {**ranking_report, "symbols": [{**r, "experiment": view[r["ticker"]]} if r.get("ticker") in view else r
                                          for r in ranking_report["symbols"]]}


def cell(view):
    """Compact TODAY/SWING cell: eligibility and final shadow action."""
    if not view or view.get("eligibility") == "NOT_APPLICABLE":
        return ("", "")
    tone = {"ELIGIBLE": "STRONG_CANDIDATE", "BLOCKED": "WATCHLIST"}.get(view["eligibility"], "NO_TRADE")
    reason = "" if view.get("attribution") in (None, "PASS") else view["attribution"]
    return (f'<span class="badge {tone}">{escape(view["eligibility"])}</span>'
            f'<span class="co">{escape(view["final_action"])}{(" · " + escape(reason)) if reason else ""}</span>',
            view["final_action"])


def explanation(view):
    """Full gate explanation for the row details."""
    if not view or view.get("eligibility") == "NOT_APPLICABLE":
        return ""
    liquidity, gate = view.get("liquidity") or {}, view.get("liquidity_gate") or {}
    res, size, entry = view.get("resistance_gate") or {}, view.get("sizing") or {}, view.get("entry_state") or {}
    lines = [f"<b>Challenger {escape(view['arm'])} ({escape(_u(view.get('config_version')))}):</b> technical "
             f"{escape(view['technical_class'])} {escape(_u(view.get('technical_score')))} · eligibility "
             f"{escape(view['eligibility'])} · action {escape(view['final_action'])}",
             f"<b>Liquidity:</b> {escape(_u(gate.get('result')))} — volume {escape(_u(liquidity.get('volume')))}, "
             f"20s avg {escape(_u(liquidity.get('avg_volume_20')))}, rel. {escape(_u(liquidity.get('relative_volume')))}×, "
             f"turnover {escape(_u(liquidity.get('turnover_egp')))} EGP, 20s avg turnover "
             f"{escape(_u(liquidity.get('avg_turnover_20_egp')))} EGP, position "
             f"{escape(_u(gate.get('position_pct_of_avg_turnover')))}% of avg turnover (max {escape(_u(gate.get('max_pct')))}%)",
             f"<b>Resistance:</b> {escape(_u(res.get('result')))} — nearest {escape(_u(res.get('nearest_resistance')))} "
             f"({escape(_u(res.get('resistance_date')))}), room {escape(_u(res.get('room_r')))}R vs required "
             f"{escape(_u(res.get('required_r')))}R (entry {escape(_u(res.get('intended_entry')))}, stop {escape(_u(res.get('stop')))})",
             f"<b>Sizing:</b> {escape(_u(size.get('quantity')))} shares, position {escape(_u(size.get('position_value_egp')))} EGP, "
             f"planned risk {escape(_u(size.get('planned_risk_egp')))} EGP ({escape(_u(size.get('risk_pct_per_trade')))}% of "
             f"{escape(_u(size.get('model_capital_egp')))} model capital)",
             f"<b>Entry:</b> {escape(_u(entry.get('state') or view.get('lifecycle_status')))} · expiry "
             f"{escape(_u(entry.get('expiry_session')))} · gap vs zone top {escape(_u(entry.get('gap_vs_zone_top_pct')))}% "
             f"(max chase {escape(_u(entry.get('max_chase_pct')))}%)"]
    return "<br>" + "<br>".join(lines)


def render_performance(report):
    if report is None:
        return ('<section aria-label="Champion vs challenger"><h2>Champion vs challenger (EGX-EXP-v1)</h2>'
                '<p>UNAVAILABLE: no experiment report.</p></section>')
    arms = list(report["performance"])
    head = "".join(f'<th class="num">{escape(arm)}</th>' for arm in arms)
    rows = ""
    for key, label in METRICS:
        values = [report["performance"][arm].get(key) for arm in arms]
        if all(v is None for v in values):
            continue
        rows += f"<tr><th>{escape(label)}</th>" + "".join(f'<td class="num">{escape(_u(v))}</td>' for v in values) + "</tr>"
    attr = ""
    for arm, item in report.get("attribution", {}).items():
        codes = ", ".join(f"{k} {v}" for k, v in item["codes_all_candidates"].items())
        effects = "; ".join(f"{k}: {v['v1_winners_excluded']} V1 winners excluded, {v['v1_losers_avoided']} V1 losers "
                            f"avoided, {v['v1_r_excluded']}R" for k, v in item["by_code"].items()) or "no closed V1 trades yet"
        attr += f"<li><b>{escape(arm)}</b>: {escape(codes)}<br><span class=\"co\">{escape(effects)}</span></li>"
    arms_list = "".join(f"<li><b>{escape(k)}</b>: {escape(v)}</li>" for k, v in report["arms"].items())
    return (f'<section aria-label="Champion vs challenger"><h2>Champion vs challenger ({escape(report["strategy"])})</h2>'
            f'<p class="muted">{escape(report["promotion_policy"])} Champion: {escape(report["champion"])}. Config '
            f'{escape(report["config_version"])}. Net R after the V1 cost model; 2× costs shown as a sensitivity.</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Metric</th>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
            f'<h3>Gate attribution</h3><ul>{attr}</ul><details><summary>Arm definitions</summary><ul>{arms_list}</ul>'
            '</details></section>')


def render_live(report):
    if report is None:
        return ""
    labels = {"PENDING_ENTRY": "waiting for entry", "WAITING_FOR_ENTRY": "waiting for entry", "OPEN": "entered",
              "CHASE_BLOCKED": "chase blocked", "SETUP_EXPIRED": "expired", "BELOW_ENTRY": "expired (below entry)",
              "INVALIDATED": "invalidated", "CLOSED_STOP": "stopped", "CLOSED_T2": "T2", "CLOSED_BREAKEVEN": "T1 then breakeven",
              "CLOSED_TIME": "time exit", "BLOCKED": "blocked (not traded)", "NO_FILL_GAP_DOWN": "no fill (gap down)",
              "NO_FILL_GAP_UP": "no fill (gap up)"}
    by_candidate = {}
    for arm, rows in report["rows"].items():
        for row in rows:
            key = (row["session"], row["ticker"])
            by_candidate.setdefault(key, {})[arm] = row
    arms = list(report["rows"])
    head = "".join(f"<th>{escape(a)}</th>" for a in arms)
    body = ""
    for (session, ticker), item in sorted(by_candidate.items(), reverse=True)[:60]:
        cells = "".join(f'<td>{escape(labels.get(item[a]["status"], item[a]["status"]))}'
                        f'{"<span class=co>" + escape(item[a]["attribution"]) + "</span>" if item[a].get("attribution") not in (None, "PASS") else ""}'
                        f"</td>" if a in item else "<td>—</td>" for a in arms)
        body += f'<tr><td><span class="tk">{escape(ticker)}</span><span class="co">{escape(session)}</span></td>{cells}</tr>'
    return ('<section aria-label="Paper/Shadow lifecycle states"><h2>EGX Paper/Shadow lifecycle states by arm</h2>'
            '<p class="muted">Simulated from completed sessions only (end-of-day evidence; not real-time).</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Candidate</th>{head}</tr></thead><tbody>{body or "<tr><td>none</td></tr>"}'
            '</tbody></table></div></section>')


def render_system(report):
    if report is None:
        return "<h2>Strategy experiment</h2><p>UNAVAILABLE</p>"
    config = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in report["config"].items()
                     if k != "rationale")
    rationale = "".join(f"<li><b>{escape(k)}</b>: {escape(v)}</li>" for k, v in report["config"].get("rationale", {}).items())
    health = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in report["health"].items())
    return (f'<h2>Strategy experiment: {escape(report["strategy"])} · {escape(report["config_version"])}</h2>'
            f'<p>Champion {escape(report["champion"])} (EGX-RANK-v1, unchanged). {escape(report["promotion_policy"])}</p>'
            f'<table aria-label="Experiment health"><tbody>{health}</tbody></table>'
            f'<table aria-label="Experiment configuration"><tbody>{config}</tbody></table>'
            f'<details><summary>Threshold rationale</summary><ul>{rationale}</ul></details>')
