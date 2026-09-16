"""Truthful M7 paper-performance presentation; no analytics are computed here."""
from __future__ import annotations

import html
from decimal import Decimal

from app.performance.models import PerformanceReport


NAV_ITEMS = (
    "TODAY",
    "LIVE",
    "PRE-SURGE",
    "SWING",
    "PERFORMANCE",
    "RESEARCH",
    "SYSTEM",
)


def _decimal(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    return html.escape(format(value, "f"))


def _nav() -> str:
    items = []
    for item in NAV_ITEMS:
        classes = "tab active" if item == "PERFORMANCE" else "tab"
        if item == "TODAY":
            items.append(f'<a class="{classes}" href="/">TODAY</a>')
        elif item == "PERFORMANCE":
            items.append(
                f'<a class="{classes}" href="/performance">PERFORMANCE</a>'
            )
        elif item == "SYSTEM":
            items.append(
                f'<a class="{classes}" href="/system">SYSTEM</a>'
            )
        else:
            items.append(f'<span class="{classes}">{html.escape(item)}</span>')
    return "".join(items)


def render_performance_dashboard(
    report: PerformanceReport | None = None,
) -> str:
    """Render already-computed M7 analytics without recomputing execution."""
    nav = _nav()

    if report is None:
        content = """
        <section class="panel empty">
          <h2>Performance unavailable</h2>
          <p>
            No canonical M7 paper-performance report is available.
          </p>
          <p>
            No synthetic trades, P&amp;L, Sharpe, Sortino, or recommendations
            are shown.
          </p>
        </section>
        """
    else:
        summary = report.summary
        drawdown = report.drawdown

        cards = (
            ("Observations", str(summary.total_observations)),
            ("Completed", str(summary.completed_count)),
            ("Net P&L", _decimal(summary.total_net_pnl)),
            ("Net Expectancy", _decimal(summary.net_expectancy)),
            ("Profit Factor", _decimal(summary.profit_factor)),
            ("Max Drawdown", _decimal(drawdown.max_drawdown_amount)),
        )

        card_html = "".join(
            f"""
            <div class="card">
              <div class="label">{html.escape(label)}</div>
              <div class="value">{value}</div>
            </div>
            """
            for label, value in cards
        )

        content = f"""
        <div class="grid">{card_html}</div>

        <section class="panel">
          <h2>Completed-trade economics</h2>
          <dl>
            <dt>Economic wins</dt><dd>{summary.win_count}</dd>
            <dt>Economic losses</dt><dd>{summary.loss_count}</dd>
            <dt>Breakeven</dt><dd>{summary.breakeven_count}</dd>
            <dt>Average win</dt><dd>{_decimal(summary.average_win)}</dd>
            <dt>Average loss</dt><dd>{_decimal(summary.average_loss)}</dd>
            <dt>R expectancy</dt><dd>{_decimal(summary.r_expectancy)}</dd>
            <dt>Average MAE</dt><dd>{_decimal(summary.average_mae)}</dd>
            <dt>Average MFE</dt><dd>{_decimal(summary.average_mfe)}</dd>
          </dl>
        </section>

        <section class="panel">
          <h2>Realized completed-trade equity</h2>
          <p>Starting equity: {_decimal(drawdown.starting_equity)}</p>
          <p>Ending equity: {_decimal(drawdown.ending_equity)}</p>
          <p>Peak equity: {_decimal(drawdown.peak_equity)}</p>
          <p>Max drawdown % (fraction): {_decimal(drawdown.max_drawdown_pct)}</p>
        </section>
        """

    if report is not None:
        def table(title, headers, rows):
            headings = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
            body = "".join("<tr>" + "".join(
                f"<td>{html.escape(str(v))}</td>" for v in row
            ) + "</tr>" for row in rows)
            return (f'<section class="panel"><h2>{title}</h2><table><thead><tr>{headings}'
                    f'</tr></thead><tbody>{body}</tbody></table></section>')

        content += table("Observation states", ("State", "Count"), (
            (name, getattr(report.summary, name.lower() + "_count"))
            for name in ("Completed", "Rejected", "No_fill", "Open", "Incomplete")
        ))
        c = report.monthly_consistency
        content += f'<section class="panel"><h2>Monthly consistency</h2><p>Evaluated: {c.evaluated_months}; positive: {c.positive_months}; negative: {c.negative_months}; flat: {c.flat_months}</p></section>'
        content += table("Monthly economics", ("Month", "Completed", "Net P&L", "Net expectancy"), (
            (m.month, m.summary.completed_count, _decimal(m.summary.total_net_pnl), _decimal(m.summary.net_expectancy))
            for m in report.months
        ))
        content += table("Regime consistency", ("Regime", "Observations", "Completed", "Net P&L", "Net expectancy"), (
            (r.market_regime.value, r.summary.total_observations, r.summary.completed_count,
             _decimal(r.summary.total_net_pnl), _decimal(r.summary.net_expectancy)) for r in report.regimes
        ))
        risk = report.risk_adjusted
        content += table("Periodic-return ratios", ("Metric", "Value", "Unavailable reason"), (
            ("Sharpe", _decimal(risk.sharpe), risk.sharpe_reason or ""),
            ("Sortino", _decimal(risk.sortino), risk.sortino_reason or ""),
        ))
        if risk.series is not None:
            series = risk.series
            content += table("Periodic basis", ("Series", "Seconds per period", "Risk-free per period", "Target per period", "Minimum samples", "Annualization factor"), (
                (series.series_id, series.period_seconds, series.risk_free_return,
                 series.sortino_target, series.minimum_samples,
                 series.annualization_factor if series.annualization_factor is not None else "None (per-period)"),
            ))
        content += table("MAE/MFE in R", ("Average MAE R", "Average MFE R"), (
            (_decimal(report.summary.average_mae_r), _decimal(report.summary.average_mfe_r)),
        ))
        if report.slippage_sensitivity:
            content += table("Slippage sensitivity (descriptive PAPER)",
                ("Scenario", "Completed", "Net P&L", "Expectancy", "Profit factor", "Drawdown", "Drawdown fraction", "Delta P&L", "Delta expectancy"), (
                    (r.scenario.scenario_id, r.completed_count, _decimal(r.total_net_pnl),
                     _decimal(r.net_expectancy), _decimal(r.profit_factor),
                     _decimal(r.max_drawdown_amount), _decimal(r.max_drawdown_pct),
                     _decimal(r.delta_total_net_pnl), _decimal(r.delta_net_expectancy))
                    for r in report.slippage_sensitivity
                ))
            content += f'<p>Baseline scenario: {html.escape(report.baseline_scenario_id or "UNAVAILABLE")}</p>'
        else:
            content += '<section class="panel">Slippage sensitivity UNAVAILABLE: no explicit scenarios supplied.</section>'

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EGX Paper Performance</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#071019;color:#e9f0f5;font-family:Arial,sans-serif}}
header{{padding:26px 34px;background:#0b1620;border-bottom:1px solid #1d2b38}}
nav{{display:flex;gap:8px;padding:14px 34px;background:#0b1620;border-bottom:1px solid #1d2b38;overflow:auto}}
.tab{{padding:9px 13px;color:#748696;font-size:12px;white-space:nowrap;text-decoration:none}}
.active{{color:white;background:#173147;border-radius:7px}}
main{{padding:28px 34px;max-width:1400px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.card,.panel{{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:18px}}
.panel{{margin-top:20px}}
.label{{color:#7f93a3;font-size:12px;text-transform:uppercase}}
.value{{font-size:25px;margin-top:9px;font-weight:700}}
.empty{{border-color:#493d20}}
.notice{{margin-top:20px;padding:16px;border:1px solid #493d20;background:#211c10;border-radius:9px;color:#e5c36c}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
dt{{color:#7f93a3}} dd{{margin:0}}
@media(max-width:800px){{main,header,nav{{padding-left:16px;padding-right:16px}}.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <h1>EGX Trading Platform</h1>
  <div>PAPER / Observation Mode · Performance</div>
</header>
<nav>{nav}</nav>
<main>
  {content}
  <div class="notice">
    These are deterministic paper-simulation measurements only.
    They are not strategy-validation, profitability, robustness,
    alpha, or real-money-readiness evidence.
  </div>
</main>
</body>
</html>"""
