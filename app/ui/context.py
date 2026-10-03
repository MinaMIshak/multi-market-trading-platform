"""RESEARCH context panels: regime, rates, FX, gold, Brent, indices, events, geopolitics, fundamentals.

Renders the recorded context report only (app/context/run.py). Evidence
classes stay visible: market quote, official fact, reference rate, media
narrative. Missing values are UNKNOWN. Context never creates a candidate.
"""
import json
from html import escape
from pathlib import Path

from app.runtime_state import resolved_path

SCHEMA = "context-report-v1"
MAX_BYTES = 8 * 1024 * 1024


def load_context():
    configured = resolved_path("context")
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
    markets = report.get("markets") or {}
    return {"status": "AVAILABLE", "generated_at": report["generated_at"],
            "markets_available": sum(1 for m in markets.values() if m.get("status") == "AVAILABLE"),
            "markets": len(markets), "egx_risk": ((report.get("regimes") or {}).get("risk") or {}).get("EGX", {})
            .get("label"), "us_risk": ((report.get("regimes") or {}).get("risk") or {}).get("US", {}).get("label")}


def _u(value):
    return "UNKNOWN" if value is None or value == "" else str(value)


def _signed(value, suffix=""):
    if value is None:
        return "UNKNOWN"
    text = str(value)
    return (text if text.startswith("-") else "+" + text) + suffix


def _badge(label):
    tone = {"RISK_ON": "STRONG_CANDIDATE", "LOW": "STRONG_CANDIDATE", "NORMAL": "STRONG_CANDIDATE",
            "AGREE": "STRONG_CANDIDATE", "CURRENT": "STRONG_CANDIDATE", "AVAILABLE": "STRONG_CANDIDATE",
            "NEUTRAL": "CANDIDATE", "HOLD": "CANDIDATE", "STABLE": "CANDIDATE", "FLAT": "CANDIDATE",
            "POSITIVE": "CANDIDATE", "ELEVATED": "WATCHLIST", "RISING": "WATCHLIST", "FALLING": "WATCHLIST",
            "EGP_WEAKENING": "WATCHLIST", "EGP_STRENGTHENING": "WATCHLIST", "EASING": "CANDIDATE",
            "TIGHTENING": "WATCHLIST", "STALE": "WATCHLIST", "REPORTED_NOT_RECONCILED": "WATCHLIST",
            "UNVERIFIED": "WATCHLIST"}.get(label, "NO_TRADE")
    return f'<span class="badge {tone}">{escape(_u(label))}</span>'


def _table(headers, rows, numeric=()):
    head = "".join(f'<th{" class=\"num\"" if i in numeric else ""}>{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i in numeric else ""}>{cell}</td>'
                                    for i, cell in enumerate(row)) + "</tr>" for row in rows)
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body or "<tr><td colspan=99>none</td></tr>"}</tbody></table></div>')


def _market_rows(report, keys):
    rows = []
    for key in keys:
        item = (report.get("markets") or {}).get(key)
        if not item:
            continue
        if item.get("status") != "AVAILABLE":
            rows.append([f'<span class="tk">{escape(item["title"])}</span><span class="co">{escape(item["symbol"])}</span>',
                         "UNKNOWN", "UNKNOWN", "UNKNOWN", f'UNAVAILABLE: {escape(_u(item.get("reason")))}',
                         escape(item.get("nature", ""))])
            continue
        rows.append([f'<span class="tk">{escape(item["title"])}</span><span class="co">{escape(item["symbol"])} · '
                     f'{escape(item["unit"])}</span>', escape(item["latest_value"]), escape(item["latest_date"]),
                     escape(_signed(item.get("change_20_pct"), "%")),
                     f'{_badge(item["freshness"])} <span class="co">expected {escape(_u(item.get("expected_latest")))}</span>',
                     escape(item.get("nature", "").replace("_", " ").lower())])
    return rows


def _macro_rows(report, ids):
    rows = []
    for row in (report.get("macro") or {}).get("series", []):
        if row["series_id"] not in ids:
            continue
        if row.get("status") != "AVAILABLE":
            rows.append([escape(row["title"]), "UNKNOWN", "UNKNOWN", "UNKNOWN",
                         f'UNAVAILABLE: {escape(_u(row.get("reason")))}', escape(row["publisher"])])
            continue
        suffix = "pp" if row["unit"] == "%" else ""
        rows.append([f'<span class="tk">{escape(row["title"])}</span><span class="co">{escape(row["series_id"])}</span>',
                     escape(row["latest_value"]), escape(row["latest_date"]),
                     escape(_signed(row.get("change_20"), suffix)), _badge(row["freshness"]), escape(row["publisher"])])
    return rows


def _reconciliation(report, prefix):
    rows = []
    for item in report.get("reconciliation") or []:
        if not item["check"].startswith(prefix):
            continue
        detail = (f'Δ {escape(_u(item.get("difference_pct")))}% (tolerance {escape(_u(item.get("tolerance_pct")))}%)'
                  if item.get("difference_pct") is not None else escape(_u(item.get("reason") or item.get("note"))))
        if item.get("futures_minus_spot_pct_same_date") is not None:
            detail = (f'futures − spot on {escape(item["eia_spot_date"])}: '
                      f'{escape(item["futures_minus_spot_pct_same_date"])}% · {escape(item["note"])}')
        rows.append(f'<li>{escape(item["check"])}: {_badge(item["status"])} {detail}</li>')
    return f'<ul>{"".join(rows)}</ul>' if rows else ""


def _section(title, body, note=""):
    return (f'<article><h2>{escape(title)}</h2>' + (f'<p class="muted">{escape(note)}</p>' if note else "")
            + body + "</article>")


def render_context(report):
    if report is None:
        return ('<section aria-label="Research context"><h2>Research context</h2><p>UNAVAILABLE: no verified '
                'context report in the runtime state.</p></section>')
    regimes, out = report.get("regimes") or {}, []
    risk = regimes.get("risk") or {}
    tiles = []
    for name, item in (("US equity regime", regimes.get("us_equity")), ("EGX equity regime", regimes.get("egx_equity")),
                       ("EGX context risk", risk.get("EGX")), ("US context risk", risk.get("US")),
                       ("Fed stance", regimes.get("fed")), ("EGP", regimes.get("egp")),
                       ("Gold (20s)", regimes.get("gold")), ("Brent (20s)", regimes.get("brent")),
                       ("Suez transits", regimes.get("suez")), ("Treasury curve", regimes.get("curve"))):
        item = item or {}
        sub = item.get("rule") or item.get("reason") or ""
        tiles.append(f'<div class="insight"><div class="k">{escape(name)}</div><div class="v">{_badge(item.get("label"))}'
                     f'</div><div class="s">{escape(sub)}</div></div>')
    flags = "".join(f"<li>{escape(market)}: {escape(', '.join(item.get('flags') or []) or 'no adverse flags')}</li>"
                    for market, item in risk.items())
    out.append(_section("Market regime", f'<section class="cards">{"".join(tiles)}</section><ul>{flags}</ul>',
                        f'{report.get("regime_version")} · as of {report["as_of"]} · context only: no regime or headline '
                        'creates, upgrades or vetoes a candidate.'))
    headers = ["Series", "Latest", "Date", "Δ 20", "Freshness", "Source / nature"]
    rates = _table(headers, _macro_rows(report, {"DFEDTARU", "DFEDTARL", "DFF", "DGS2", "DGS10"}), (1, 3))
    derived = "".join(f'<li>{escape(d["metric"])}: <b>{escape(_u(d.get("value")))} {escape(d["unit"])}</b> '
                      f'({escape(d["shape"])})</li>' for d in (report.get("macro") or {}).get("derived", []))
    egypt = (report.get("rates") or {}).get("egypt") or {}
    egypt_rows = [[escape(s["title"]), escape(_u((s.get("latest") or [None, None])[1])),
                   escape(_u((s.get("latest") or [None])[0])), "",
                   f'{_badge(s.get("freshness"))} <span class="co">{escape(_u(s.get("age_months")))} months old</span>',
                   "IMF IFS (official, reported by CBE)"] for s in (egypt.get("series") or {}).values()]
    releases = ((report.get("rates") or {}).get("fed_releases") or {}).get("releases") or []
    fed_list = "".join(f'<li>{escape(r["published_at"][:16].replace("T", " "))} ET · '
                       f'<a href="{escape(r["url"], quote=True)}" rel="noopener">{escape(r["title"])}</a></li>'
                       for r in releases[:6])
    out.append(_section("Rates and monetary policy",
                        rates + f"<ul>{derived}</ul>" + _reconciliation(report, "Fed")
                        + "<h3>Egypt</h3>" + (_table(headers, egypt_rows) if egypt_rows else
                                               f'<p>UNAVAILABLE: {escape(_u(egypt.get("reason")))}</p>')
                        + '<p class="muted">Current CBE policy rate: BLOCKED (cbe.org.eg rejects automated access); the '
                          'IMF series is official but lagged and is labelled with its age.</p>'
                        + f"<h3>Federal Reserve monetary-policy releases</h3><ul>{fed_list or '<li>UNAVAILABLE</li>'}</ul>"))
    fx_ref = report.get("fx_reference") or {}
    out.append(_section("FX", _table(headers, _market_rows(report, ["USDEGP"])
                                     + _macro_rows(report, {"DTWEXBGS", "DEXUSEU"}), (1, 3))
                        + f'<p>Reference USD/EGP (ExchangeRate-API, aggregated mid, not official): '
                          f'<b>{escape(_u(fx_ref.get("usd_egp")))}</b> at {escape(_u(fx_ref.get("updated_at")))}</p>'
                        + _reconciliation(report, "USD/EGP")
                        + '<p class="muted">Official CBE rate: BLOCKED (automated access rejected). A market or reference '
                          'rate is never presented as the official rate.</p>'))
    out.append(_section("Gold", _table(headers, _market_rows(report, ["XAUUSD", "GOLD_TVC"]), (1, 3))
                        + _reconciliation(report, "Gold")))
    out.append(_section("Brent / oil", _table(headers, _market_rows(report, ["UKOIL"])
                                              + _macro_rows(report, {"DCOILBRENTEU"}), (1, 3))
                        + _reconciliation(report, "Brent")))
    out.append(_section("Equity indices", _table(headers, _market_rows(report, ["EGX30", "SPX"])
                                              + _macro_rows(report, {"VIXCLS"}), (1, 3))
                        + (f'<p>EGX30 in USD terms, 60 common sessions: '
                           f'<b>{escape(_signed(regimes.get("egx_equity", {}).get("egx30_usd_change_60_pct"), "%"))}</b></p>'
                           if regimes.get("egx_equity", {}).get("egx30_usd_change_60_pct") is not None else "")))
    disclosures = (report.get("events") or {}).get("egx_disclosures") or {}
    rows = [[escape(r["published_at"][:16].replace("T", " ")), escape(r["heading"]), escape(_u(r.get("isin"))),
             escape(r["section"]), " ".join(f'<a href="{escape(u, quote=True)}" rel="noopener">pdf</a>'
                                            for u in r.get("documents", [])[:2])]
            for r in (disclosures.get("records") or [])[:15]]
    out.append(_section("News and catalysts",
                        (f'<h3>EGX disclosures (official exchange feed)</h3>'
                         + (_table(["Published (Cairo)", "Heading", "ISIN", "Section", "Docs"], rows)
                            if disclosures.get("status") == "AVAILABLE"
                            else f'<p>UNAVAILABLE: {escape(_u(disclosures.get("reason")))}</p>'))
                        + f"<h3>Central bank</h3><ul>{fed_list or '<li>UNAVAILABLE</li>'}</ul>"
                        + '<p class="muted">US company filings (SEC EDGAR): BLOCKED until an SEC contact is configured. '
                          'CBE releases: BLOCKED (automated access rejected).</p>',
                        "Official disclosures are facts about what was published; they are not interpreted as "
                        "positive or negative."))
    geo = report.get("geopolitics") or {}
    ships = (geo.get("shipping") or {}).get("chokepoints") or {}
    ship_rows = [[escape(name), escape(_u(item.get("latest_date"))), escape(_u(item.get("mean_7d"))),
                  escape(_u(item.get("mean_90d"))), escape(_signed(item.get("change_7d_vs_90d_pct"), "%")),
                  escape(_signed(item.get("change_7d_vs_year_ago_pct"), "%")),
                  _badge((regimes.get("chokepoints") or {}).get(name, {}).get("label"))]
                 for name, item in ships.items()]
    sanctions = geo.get("sanctions") or {}
    narrative = geo.get("narrative") or {}
    themes = ""
    for theme, item in (narrative.get("themes") or {}).items():
        if item.get("status") != "AVAILABLE":
            themes += f'<li>{escape(theme)}: UNAVAILABLE ({escape(_u(item.get("reason")))})</li>'
            continue
        links = "".join(f'<li><a href="{escape(a["url"], quote=True)}" rel="noopener">{escape(a["title"])}</a> '
                        f'<span class="co">{escape(_u(a.get("domain")))} · {escape(_u(a.get("seen_at")))}</span></li>'
                        for a in item["articles"][:5])
        themes += f"<li>{escape(theme)}<ul>{links or '<li>no articles</li>'}</ul></li>"
    out.append(_section("Geopolitical context",
                        "<h3>Shipping chokepoints (IMF PortWatch, daily transit calls)</h3>"
                        + _table(["Chokepoint", "Latest", "7-day mean", "90-day mean", "7d vs 90d", "7d vs year ago",
                                  "Label"], ship_rows, (2, 3, 4, 5))
                        + (f'<h3>Sanctions (OFAC SDN)</h3><p>{escape(_u(sanctions.get("entries")))} entries; largest '
                           f'programs: {escape(", ".join(f"{p} {n}" for p, n in sanctions.get("top_programs", [])[:6]))}. '
                           f'{escape(_u(sanctions.get("note")))}</p>' if sanctions.get("status") == "AVAILABLE"
                           else f'<p>OFAC: UNAVAILABLE ({escape(_u(sanctions.get("reason")))})</p>')
                        + f"<h3>Media narrative (GDELT)</h3><ul>{themes}</ul>",
                        "Structured facts (transits, list counts) are separate from media narrative, which is never "
                        "treated as verified."))
    statements = ((report.get("fundamentals") or {}).get("egx_financial_statements") or {})
    fin_rows = [[escape(r["published_at"][:10]), escape(r["heading"]), escape(_u(r.get("basis"))),
                 escape(_u(r.get("period_end"))), escape(_u(r.get("net_result"))),
                 escape(_u(r.get("comparative_net_result"))), escape(_signed(r.get("net_result_change_pct"), "%")),
                 escape(_u(r.get("audit_status"))), escape(r.get("parse_status", ""))]
                for r in (statements.get("records") or [])[:20]]
    us_sec = (report.get("fundamentals") or {}).get("us_sec") or {}
    out.append(_section("Fundamentals",
                        "<h3>EGX financial-statement disclosures (official exchange feed)</h3>"
                        + (_table(["Published", "Heading", "Basis", "Period end", "Net result", "Comparative", "Change",
                                   "Audit", "Parse"], fin_rows, (4, 5, 6))
                           if statements.get("status") == "AVAILABLE"
                           else f'<p>UNAVAILABLE: {escape(_u(statements.get("reason")))}</p>')
                        + f'<p class="muted">Amounts are in the disclosure currency, scaled by the stated unit. Rows '
                          f'the parser cannot read are shown as UNPARSED_LAYOUT, never estimated. US (SEC XBRL): '
                          f'{escape(us_sec.get("status", "UNKNOWN"))}. {escape(_u(us_sec.get("reason")))}</p>'))
    cm_rows = [[escape(c["pair"]), escape(_u(c.get("value"))), escape(str(c.get("n", 0))),
                escape(f'{c.get("start", "")} → {c.get("end", "")}' if c.get("start") else _u(c.get("reason")))]
               for c in report.get("cross_market") or []]
    out.append(_section("Cross-market evidence", _table(["Pair", "Correlation (60 common sessions)", "n", "Window"],
                                                        cm_rows, (1, 2)),
                        "Daily return correlations on common dates only (yields use changes); descriptive, not causal "
                        "or predictive."))
    prov = "".join(f'<li>{escape(key)}: {escape(_u(item.get("raw_sha256")))} · {escape(_u(item.get("retrieved_at")))} · '
                   f'{escape(_u(item.get("rights")))}</li>' for key, item in (report.get("markets") or {}).items())
    out.append(f'<details><summary>Sources, freshness and provenance</summary><ul>{prov}</ul>'
               '<p class="muted">Decision matrix: docs/SOURCE_DECISION_MATRIX.md. Market quotes via TradingView carry '
               'NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED; official series name their publisher.</p></details>')
    return f'<section aria-label="Research context">{"".join(out)}</section>'
