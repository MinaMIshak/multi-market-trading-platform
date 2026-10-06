"""Learning and forecast views: TODAY upside, last-session review, PERFORMANCE, RESEARCH, SYSTEM.

Renders the recorded learning report only. Forecasts are research, separate
from technical class, trade eligibility and the Paper/Shadow action. A
probability never guarantees a return. LIVE MONEY DISABLED.
"""
import json
from html import escape
from pathlib import Path

from app.runtime_state import resolved_path

SCHEMA = "learning-report-v1"
MAX_BYTES = 16 * 1024 * 1024


def load_learning():
    configured = resolved_path("learning")
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
    out = {"status": "AVAILABLE", "generated_at": report["generated_at"], "build_revision": report.get("build_revision")}
    for market, m in report["markets"].items():
        out[market] = {"latest_session": m.get("latest_session"), "champion": m.get("champion"),
                       "challengers": m.get("challengers"), "frozen_files": (m.get("forecast") or {}).get("frozen_files"),
                       "walkforward": (m.get("walkforward") or {}).get("generated_at") or "NOT_RUN_YET"}
    return out


def _u(value):
    return "UNKNOWN" if value is None or value == "" else str(value)


def _pct(value, digits=1):
    return "UNKNOWN" if value is None else f"{value * 100:+.{digits}f}%"


def _prob(item, key):
    p = (item.get("p") or {}).get(key)
    if p is None:
        return "INSUFFICIENT_SAMPLE"
    note = "" if (item.get("p_status") or {}).get(key) == "OK" else " (few events)"
    return f"{p * 100:.1f}%{note}"


def _table(headers, rows, numeric=()):
    head = "".join(f'<th{" class=\"num\"" if i in numeric else ""}>{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i in numeric else ""}>{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body or "<tr><td colspan=99>none</td></tr>"}</tbody></table></div>')


def render_today(report, market, *, ranking=None, experiment=None):
    if report is None or market not in report.get("markets", {}):
        return (f'<section aria-label="{market} upside forecasts"><h2>Top upside forecasts — next 1–3 sessions ({market})</h2>'
                '<p>UNAVAILABLE: no learning report.</p></section>')
    m = report["markets"][market]
    forecast = m.get("forecast") or {}
    technical = {r["ticker"]: r for r in (ranking or {}).get("symbols", [])}
    trade = (experiment or {}).get("current") or {}
    rows = []
    for item in forecast.get("upside_top", [])[:15]:
        tech = technical.get(item["ticker"], {})
        view = trade.get(item["ticker"], {})
        eligibility = view.get("eligibility") if view.get("eligibility") not in (None, "NOT_APPLICABLE") else "—"
        rows.append([str(item["forecast_rank"]),
                     f'<span class="tk">{escape(item["ticker"])}</span><span class="co">{escape(_u(item.get("sector")))}</span>',
                     escape(_u(tech.get("classification"))), escape(_u(eligibility)),
                     escape(_pct(item.get("expected_r1"), 2)), escape(_pct(item.get("expected_r3"), 2)),
                     escape(_prob(item, "5")), escape(_prob(item, "10")), escape(_prob(item, "20")),
                     escape(item["confidence"]), escape(f'{item["rvol"]:.2f}×' if item.get("rvol") else "UNKNOWN"),
                     escape(f'{(item.get("turnover20") or 0) / 1e6:.1f}M'),
                     escape(_pct(item.get("expected_mae3"), 1))])
    head = ["#", "Symbol", "Technical class", "Trade eligibility (V2D)", "E[r] 1s", "E[r] 3s", "P(+5%) 3s",
            "P(+10%) 3s", "P(+20%) 3s", "Confidence", "RVOL", "Liquidity/day", "Exp. adverse 3s"]
    note = (f'Model {escape(m["champion"])} (champion; ties broken by FC-RIDGE-v1), feature schema '
            f'{escape(m["feature_schema"])}, feature snapshot session {escape(forecast.get("session") or "")}, '
            f'report {escape(report["generated_at"][:16].replace("T", " "))} UTC. Liquid symbols only '
            f'({forecast.get("illiquid_excluded", 0)} illiquid excluded). Research forecast: not a candidate, '
            'not a fill, not a guarantee. Confidence is UNVALIDATED until walk-forward evidence exists.')
    return (f'<section aria-label="{market} upside forecasts"><h2>Top upside forecasts — next 1–3 sessions ({market})</h2>'
            f'<p class="muted">{note}</p>' + _table(head, rows, (0, 4, 5, 6, 7, 8, 10, 11, 12)) + "</section>")


def render_review(report, market):
    if report is None or market not in report.get("markets", {}):
        return ""
    w = report["markets"][market].get("winners") or {}
    if w.get("status") != "AVAILABLE":
        return ""
    rows = []
    for item in w["winners"][:10]:
        v1 = item.get("prior_v1") or {}
        fc = item.get("prior_forecast") or {}
        rows.append([str(item["rank"]), f'<span class="tk">{escape(item["ticker"])}</span>'
                     f'<span class="co">{escape(_u(item.get("sector")))}</span>',
                     escape(_pct(item["actual_return"], 1)), escape(_u(v1.get("v1_rank"))),
                     escape(_u(v1.get("technical_class"))), escape(_u(fc.get("forecast_rank")) if fc else item["prior_forecast_status"]),
                     escape(_pct(fc.get("expected_r3"), 2) if fc else "—"),
                     escape(_prob(fc, "5") if fc else "—"), escape(_prob(fc, "10") if fc else "—"),
                     escape(_prob(fc, "20") if fc else "—"),
                     f'<span class="badge {"STRONG_CANDIDATE" if item["caught"] else "WATCHLIST"}">'
                     f'{"CAUGHT" if item["caught"] else "MISSED"}</span>',
                     escape(", ".join(item["miss_reasons"]) or "—"),
                     escape("; ".join(e.get("event_type", "") for e in item["known_events_before_cutoff"]) or "none known")])
    head = ["#", "Symbol", "Actual", "Prior V1 rank", "Prior class", "Prior forecast rank", "Prior E[r] 3s", "Prior P(+5%)",
            "Prior P(+10%)", "Prior P(+20%)", "Caught?", "Miss reasons", "Events known before cutoff"]
    return (f'<section aria-label="{market} last session review"><h2>Last session\'s top winners — what did we know '
            f'beforehand? ({market} {escape(w["session"])})</h2><p class="muted">Prior state is from session '
            f'{escape(w["prior_session"])} only: features from bars on or before it, the archived V1 ranking and the frozen '
            f'forecast. Caught in top 10: {w["caught_top10"]}/10. V1 archive for the prior session: '
            f'{"yes" if w["v1_archive_available"] else "no"}; frozen forecast: {"yes" if w["frozen_forecast_available"] else "no (forecasting began later)"}.</p>'
            + _table(head, rows, (0, 2, 3, 5, 6, 7, 8, 9)) + "</section>")


def render_performance(report):
    if report is None:
        return '<section aria-label="Forecast performance"><h2>Forecast performance</h2><p>UNAVAILABLE</p></section>'
    out = '<section aria-label="Forecast performance"><h2>Forecast performance</h2>'
    for market, m in report["markets"].items():
        wf = m.get("walkforward")
        out += f'<h3>{escape(market)} · walk-forward (out-of-sample, chronological)</h3>'
        if not wf:
            out += '<p>NOT_RUN_YET: the weekly walk-forward job has not produced results; confidence stays UNVALIDATED.</p>'
        else:
            rows = []
            for name, result in wf["models"].items():
                for h in ("1", "2", "3"):
                    metrics = result["metrics"].get(h) or result["metrics"].get(int(h)) or {}
                    calibration = metrics.get("calibration") or {}
                    def cal(t):
                        c = calibration.get(t) or {}
                        return (f'{c.get("brier")} vs {c.get("brier_climatology")} ({c.get("events")} ev)'
                                if c else "UNKNOWN")
                    rows.append([escape(name), h, escape(_u(metrics.get("status"))), escape(_u(metrics.get("n"))),
                                 escape(_u(metrics.get("mae"))), escape(_u(metrics.get("spearman_mean"))),
                                 escape(_u((metrics.get("precision_at_k") or {}).get("10") or (metrics.get("precision_at_k") or {}).get(10))),
                                 escape(_u(metrics.get("recall_top10_in_pred10"))),
                                 escape(_u((metrics.get("topk_mean_return") or {}).get("10") or (metrics.get("topk_mean_return") or {}).get(10))),
                                 escape(_u(metrics.get("benchmark_mean_return"))), escape(cal("5")), escape(cal("10")),
                                 escape(cal("20"))])
            out += (f'<p class="muted">Evaluation {escape(_u((list(wf["models"].values())[0] or {}).get("evaluation_start")))} → '
                    f'{escape(_u((list(wf["models"].values())[0] or {}).get("evaluation_end")))}; folds by calendar month, '
                    f'expanding window, labels matured before each fold only. Run {escape(wf["generated_at"][:16])}. '
                    f'{escape(wf.get("promotion", ""))}. {escape(m.get("survivorship_note", ""))}.</p>'
                    + _table(["Model", "h", "Status", "n", "MAE", "Spearman", "P@10", "Recall top-10", "Top-10 mean r",
                              "Benchmark r", "Brier +5% (vs base)", "Brier +10%", "Brier +20%"], rows, (3, 4, 5, 6, 7, 8, 9)))
        live = m.get("live_scoring") or {}
        out += (f'<p>Live frozen forecasts: {live.get("frozen_forecast_files", 0)} files; matured scoring: '
                + ", ".join(f'{escape(k)} n={escape(str((v.get(3) or v.get("3") or {}).get("n", 0)))}'
                            for k, v in (live.get("models") or {}).items()) + ' (INSUFFICIENT_SAMPLE until ≥ 20 ranked sessions).</p>')
    return out + "</section>"


def render_research(report):
    if report is None:
        return ""
    out = ""
    for market, m in report["markets"].items():
        sectors = (m.get("sectors") or {}).get("sectors", [])
        rows = [[escape(s["sector"]), str(s["members"]), escape(_pct(s["return_1"], 2)), escape(_pct(s["return_5"], 2)),
                 escape(_pct(s.get("relative_to_universe_1"), 2)), f'{s["advancing"]}/{s["declining"]}',
                 escape(_u(s.get("turnover_acceleration"))), escape(f'{s["turnover_share"] * 100:.1f}%'),
                 str(s["breakouts"]), str(s["high_rvol_names"])] for s in sectors[:15]]
        out += (f'<article><h2>Sector rotation and market-flow proxies ({escape(market)} '
                f'{escape(_u((m.get("sectors") or {}).get("session")))})</h2><p class="muted">'
                f'{escape((m.get("sectors") or {}).get("terminology", ""))}. Benchmark: '
                f'{escape((m.get("sectors") or {}).get("benchmark", ""))}.</p>'
                + _table(["Sector", "Members", "Return 1s", "Return 5s", "vs universe", "Adv/Dec", "Turnover accel.",
                          "Turnover share", "Breakouts", "High-RVOL"], rows, (1, 2, 3, 4, 6, 7, 8, 9)))
        events = m.get("events") or {}
        recent = "".join(f'<li>{escape(_u(e.get("published_at"))[:16])} · <b>{escape(e["event_type"])}</b> · '
                         f'{escape(_u(e.get("heading") or e.get("detail")))} <span class="co">{escape(_u(e.get("source")))} · '
                         f'{escape(_u(e.get("confidence")))}{" · sectors: " + escape(", ".join(e.get("mapped_sectors") or [])) if e.get("mapped_sectors") else ""}'
                         f'</span></li>' for e in (events.get("recent") or [])[:12])
        out += (f'<details><summary>Events and themes ({escape(market)}): '
                f'{escape(", ".join(f"{k} {v}" for k, v in (events.get("counts") or {}).items()))}</summary>'
                f'<p class="muted">{escape(_u(events.get("causality")))}. Model inputs: {escape(_u(events.get("model_inputs")))}.</p>'
                f'<ul>{recent}</ul></details></article>')
    return out


def render_system(report):
    if report is None:
        return "<h2>Learning and forecasting</h2><p>UNAVAILABLE</p>"
    rows = []
    for market, m in report["markets"].items():
        wf = m.get("walkforward") or {}
        rows.append(f"<tr><th>{escape(market)}</th><td>champion {escape(_u(m.get('champion')))}; challengers "
                    f"{escape(', '.join(m.get('challengers') or []))}; data {escape(_u(m.get('data_version')))}; features "
                    f"{escape(_u(m.get('feature_schema')))}; training window {escape(_u(m['history'].get('first_session')))} → "
                    f"{escape(_u(m['history'].get('last_session')))}; last observed outcome session "
                    f"{escape(_u(m.get('latest_session')))}; frozen forecast files {escape(_u((m.get('forecast') or {}).get('frozen_files')))}; "
                    f"walk-forward {escape(_u(wf.get('generated_at')) if wf else 'NOT_RUN_YET')}; labels "
                    f"{escape(json.dumps(m.get('labels')))}</td></tr>")
    from app.learning.registry import REGISTRY
    registry = "".join(f"<tr><th>{escape(k)}</th><td>{escape(json.dumps(v, default=str))}</td></tr>"
                       for k, v in REGISTRY.items())
    rows.append(f'<tr><th>Registry</th><td><table aria-label="Model registry"><tbody>{registry}</tbody></table></td></tr>')
    return (f'<h2>Learning and forecasting</h2><p>Report {escape(report["generated_at"])} · build '
            f'{escape(_u(report.get("build_revision")))} · {escape(report.get("use", ""))}</p>'
            f'<table aria-label="Learning audit"><tbody>{"".join(rows)}</tbody></table>')


def attach_fusion(report, learning, market):
    """Copy of a ranking report whose rows carry the same market's same-session Decision-Fusion snapshot."""
    fused = (((learning or {}).get("markets") or {}).get(market) or {}).get("fusion") or {}
    if report is None or not fused or fused.get("session") != report.get("session"):
        return report
    symbols = fused.get("symbols") or {}
    return {**report, "symbols": [{**r, "fusion": symbols[r["ticker"]]} if r.get("ticker") in symbols else r
                                  for r in report["symbols"]]}


def render_top_opportunities(report, market, *, ranking=None, experiment=None):
    title = f"Top {market} quant opportunities — next 1–3 sessions"
    fused = (((report or {}).get("markets") or {}).get(market) or {}).get("fusion")
    if not fused:
        return f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p>UNAVAILABLE: no Decision-Fusion output.</p></section>'
    technical = {r["ticker"]: r for r in (ranking or {}).get("symbols", [])}
    trade = (experiment or {}).get("current") or {}
    rows = []
    for ticker in fused.get("top", [])[:15]:
        s = fused["symbols"][ticker]
        p = (s.get("forecast") or {}).get("p") or {}
        if market == "US":
            eligibility = f'{_u(s.get("trade_eligibility"))} / {_u(s.get("final_action"))}'
        else:
            view = trade.get(ticker) or {}
            eligibility = (f'{view.get("eligibility")} / {view.get("final_action")}'
                           if view.get("eligibility") not in (None, "NOT_APPLICABLE") else
                           f'{_u(technical.get(ticker, {}).get("classification"))} (no V2D decision)')
        positives = ", ".join(f"{k} {v}" for k, v in s["explanation"].items() if v.startswith("+")) or "—"
        risks = [k for k, v in s["explanation"].items() if v in ("−",)]
        if market == "US" and s.get("gate_failures"):
            risks = s["gate_failures"] + risks
        vol = (s.get("risk") or {}).get("volatility20")
        if vol and vol > 0.04:
            risks.append(f"high volatility {vol * 100:.1f}%/day")
        catalyst = "; ".join(c.get("event_type") or "" for c in s.get("catalysts") or []) or "none known"
        rows.append([str(s.get("opportunity_rank")), f'<span class="tk">{escape(ticker)}</span><span class="co">'
                     f'{escape(_u(s.get("sector")))}{" · " + escape(s["industry"]) if s.get("industry") else ""}</span>',
                     escape(_u(s["opportunity"])), escape(_u(s["technical_score"])), escape(s["confidence"]),
                     escape(_pct((s.get("forecast") or {}).get("expected_r3"), 2)),
                     escape(_prob({"p": p, "p_status": (s.get("forecast") or {}).get("p_status")}, "5")),
                     escape(_prob({"p": p, "p_status": (s.get("forecast") or {}).get("p_status")}, "10")),
                     escape(_prob({"p": p, "p_status": (s.get("forecast") or {}).get("p_status")}, "20")),
                     escape(_u(s.get("earnings_state")) if market == "US" else "—"), escape(catalyst),
                     escape(eligibility), escape(positives), escape(", ".join(risks) or "none flagged")])
    head = ["#", "Symbol", "Opportunity", "Tech", "Confidence", "E[r] 3s", "P(+5%)", "P(+10%)", "P(+20%)",
            "Earnings", "Catalyst", "Trade eligibility / action", "Key evidence", "Main risk"]
    note = (f'EXPERIMENTAL {escape(fused["strategy"])} ({escape(fused["config"])}) · reported arm '
            f'{escape(fused["arm"])}: {escape(fused["arm_status"])} · session {escape(fused["session"])}. The '
            'Opportunity Score ranks research quality; hard trade gates are applied separately and are never '
            'overridden. Technical Score / the technical arm stays the champion.')
    return (f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p class="muted">{note}</p>'
            + _table(head, rows, (0, 2, 3, 5, 6, 7, 8)) + "</section>")


def render_fusion_performance(report):
    if report is None:
        return ""
    out = ""
    for market, m in (report.get("markets") or {}).items():
        summary = m.get("fusion_walkforward")
        title = f"{market} Decision-Fusion performance"
        if not summary:
            out += (f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p>NOT_RUN_YET: the periodic '
                    'walk-forward has not produced results; every arm is EXPERIMENTAL / INSUFFICIENT_SAMPLE.</p></section>')
            continue
        rows = []
        for arm, result in (summary.get("results") or {}).items():
            a = result.get("all") or {}
            excess = ", ".join(f"{k} {v:+.4f}" for k, v in (a.get("top10_excess_r3_vs") or {}).items()) or "—"
            rows.append([escape(arm), escape(_u(a.get("sample_label") or a.get("status"))), escape(_u(a.get("sessions"))),
                         escape(_u(a.get("spearman_mean"))), escape(_u(a.get("recall_top10_in_top10"))),
                         escape(_u(a.get("recall_top10_in_top20"))), escape(_u(a.get("recall_top20_in_top20"))),
                         escape(_u(a.get("precision_at_10"))), escape(_u(a.get("top10_mean_r1"))),
                         escape(_u(a.get("top10_mean_r3"))), escape(_u(a.get("benchmark_r3"))), escape(excess),
                         escape(_u(a.get("top10_mean_mae3"))), escape(_u(a.get("profit_factor_r3"))),
                         escape(_u(a.get("max_drawdown_cum_r1")))])
        increments = "".join(f"<li>{escape(k)}: " + escape(", ".join(f"{m2} {v:+.4f}" for m2, v in item.items())
                                                            if item.get("status") is None else item["status"]) + "</li>"
                             for k, item in (summary.get("incremental") or {}).items())
        live = m.get("fusion_live_scoring") or {}
        live_items = ", ".join(f'{a} {v["matured_sessions"]} sessions ({v["status"]})'
                               for a, v in (live.get("arms") or {}).items()) or "no matured frozen snapshots yet"
        research = json.dumps(summary.get("factor_research") or {}, indent=1, default=str)[:6000]
        gaps = json.dumps(summary.get("gap_research") or {}, indent=1, default=str)[:4000]
        out += (f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p class="muted">'
                f'{escape(_u(summary.get("strategy")))} {escape(_u(summary.get("config")))} · evaluation '
                f'{escape(_u(summary.get("evaluation_start")))} → {escape(_u(summary.get("evaluation_end")))} · '
                f'{escape(_u(summary.get("evaluated_rows")))} out-of-sample rows · run {escape(_u(summary.get("generated_at"))[:16])}. '
                f'{escape(_u(summary.get("promotion")))}. Market results are never combined.</p>'
                + _table(["Arm", "Sample", "Sessions", "Rank IC", "Recall top10@10", "Recall top10@20",
                          "Recall top20@20", "P@10", "Top-10 r1", "Top-10 r3", "Universe r3", "Excess r3 vs benchmarks",
                          "Top-10 MAE3", "Profit factor", "Max DD (cum r1)"], rows, tuple(range(2, 15)))
                + f'<h3>Incremental value</h3><ul>{increments}</ul><p>Live frozen snapshots: {escape(live_items)}.</p>'
                f'<details><summary>Redundancy, DF learned weights, regime and segment splits</summary><pre>'
                f'{escape(json.dumps({"redundancy": summary.get("redundancy"), "learned_weights": summary.get("df5_latest_weights"), "splits": {a: {k: v for k, v in r.items() if k != "all"} for a, r in (summary.get("results") or {}).items()}}, indent=1, default=str)[:8000])}'
                f'</pre></details><details><summary>Factor research (descriptive, in-sample)</summary><pre>{escape(research)}</pre></details>'
                + (f'<details><summary>Gap research (descriptive)</summary><pre>{escape(gaps)}</pre></details>'
                   if summary.get("gap_research") else "") + "</section>")
    return out
