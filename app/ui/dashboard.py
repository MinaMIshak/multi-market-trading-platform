"""Public dashboard layer: session context, KPI cards, quick insights, candidate table.

Renders the recorded EGX-RANK-v1 report only. Values the engine did not
produce show as UNKNOWN; nothing is recomputed or invented here. Diagnostics
(receipts, readiness, raw observations) live in collapsed sections or in
SYSTEM. A candidate is not a fill; LIVE MONEY DISABLED.
"""
from collections import Counter
from html import escape

CLASS_ORDER = ("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST", "NO_TRADE")
CLASS_LABEL = {"STRONG_CANDIDATE": "Strong", "CANDIDATE": "Candidate", "WATCHLIST": "Watchlist",
               "NO_TRADE": "No trade"}
CLASS_TEXT = {
    "STRONG_CANDIDATE": "All hard gates pass, score ≥ 75, uptrend, 20-session breakout and volume ≥ 1.5× average.",
    "CANDIDATE": "All hard gates pass, score ≥ 60 and uptrend.",
    "WATCHLIST": "All hard gates pass; score ≥ 45 or uptrend, but not a candidate.",
    "NO_TRADE": "A hard gate failed (reason shown) or the rules found a downtrend / low score.",
}

# Shared theme for every product page (and SYSTEM). Calm slate base, one soft
# accent, muted status colours; amber is reserved for the safety label.
STYLE = """
:root{--bg:#0b1118;--panel:#121a23;--panel-2:#0f161e;--border:#223041;--text:#e6edf3;
--text-2:#a8b3bf;--muted:#6e7c8a;--accent:#8fb3f5;--strong:#3fb68b;--candidate:#5b9cf0;
--watch:#d6a84a;--notrade:#9a6b6b;--safety:#e3b341}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;
max-width:1240px;margin:0 auto;padding:20px 24px 48px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:22px;font-weight:600;margin:4px 0 2px}h2{font-size:17px;font-weight:600;margin:28px 0 10px}
h3{font-size:15px;font-weight:600}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
nav a{padding:5px 12px;border:1px solid var(--border);border-radius:999px;color:var(--text-2);font-size:13px}
nav a[aria-current]{background:var(--panel);color:var(--text);border-color:var(--accent);text-decoration:none}
article{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin:16px 0}
p{margin:8px 0}.muted{color:var(--muted);font-size:13px}.secondary{color:var(--text-2)}
.safety{color:var(--safety);font-weight:600;font-size:13px;letter-spacing:.02em}
.pagehead{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px}
.ctx{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1px;background:var(--border);
border:1px solid var(--border);border-radius:12px;overflow:hidden;margin:14px 0 6px}
.ctx div{background:var(--panel);padding:10px 14px}.ctx .k{font-size:11px;text-transform:uppercase;
letter-spacing:.06em;color:var(--muted)}.ctx .v{font-size:15px;font-weight:600;margin-top:2px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px;
display:flex;flex-direction:column;justify-content:space-between;min-height:92px}
.card .n{font-size:30px;font-weight:650;line-height:1.1}.card .l{font-size:12px;color:var(--text-2);margin-top:6px}
.insights{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:12px;margin:8px 0 18px}
@media(max-width:820px){.insights{grid-template-columns:1fr 1fr}}
.insight{background:var(--panel-2);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.insight .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.insight .v{font-size:18px;font-weight:600;margin-top:4px}.insight .s{font-size:12px;color:var(--text-2)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px;font-weight:600;
border:1px solid transparent;white-space:nowrap}
.badge.STRONG_CANDIDATE{color:var(--strong);border-color:rgba(63,182,139,.45);background:rgba(63,182,139,.12)}
.badge.CANDIDATE{color:var(--candidate);border-color:rgba(91,156,240,.45);background:rgba(91,156,240,.12)}
.badge.WATCHLIST{color:var(--watch);border-color:rgba(214,168,74,.45);background:rgba(214,168,74,.12)}
.badge.NO_TRADE{color:var(--notrade);border-color:rgba(154,107,107,.45);background:rgba(154,107,107,.10)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 10px}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:10px;overflow:hidden}
.seg button{background:var(--panel-2);color:var(--text-2);border:0;border-right:1px solid var(--border);
padding:6px 12px;font-size:13px;cursor:pointer}.seg button:last-child{border-right:0}
.seg button[aria-pressed=true]{background:var(--panel);color:var(--text);box-shadow:inset 0 -2px 0 var(--accent)}
.search{background:var(--panel-2);border:1px solid var(--border);border-radius:10px;color:var(--text);
padding:6px 10px;font-size:13px;min-width:220px}
.table-wrap{max-height:70vh;overflow:auto;border:1px solid var(--border);border-radius:12px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px}
th,td{padding:9px 12px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
thead th{position:sticky;top:0;background:var(--panel);z-index:1;font-size:11.5px;text-transform:uppercase;
letter-spacing:.05em;color:var(--text-2);font-weight:600;white-space:nowrap}
th[data-sort]{cursor:pointer}th[data-sort]:after{content:" ↕";color:var(--muted);font-size:10px}
tbody tr:nth-child(even){background:rgba(255,255,255,.015)}tbody tr:hover{background:rgba(143,179,245,.06)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tk{font-weight:650;font-size:14px}.co{display:block;color:var(--muted);font-size:12px}
td details{border:0;background:none;padding:0;margin:0}td details summary{color:var(--accent);font-size:12px;font-weight:500}
td details div{color:var(--text-2);font-size:12px;margin-top:6px;max-width:420px;white-space:normal}
details{background:var(--panel-2);border:1px solid var(--border);border-radius:12px;padding:10px 16px;margin:12px 0}
summary{cursor:pointer;font-weight:600;color:var(--text-2)}details[open] summary{margin-bottom:8px}
.legend li{margin:4px 0}.table-scroll{overflow-x:auto}
footer{margin-top:36px;color:var(--muted);font-size:13px}
"""

SCRIPT = """<script>
(function(){
  function num(v){ return v === '' || v === undefined ? -Infinity : parseFloat(v); }
  document.querySelectorAll('table[data-sortable]').forEach(function(table){
    table.querySelectorAll('th[data-sort]').forEach(function(th, index){
      th.addEventListener('click', function(){
        var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
        var dir = th.getAttribute('data-dir') === 'desc' ? 1 : -1;
        th.setAttribute('data-dir', dir === 1 ? 'asc' : 'desc');
        var numeric = th.getAttribute('data-sort') === 'num';
        rows.sort(function(a, b){
          var x = a.cells[index].getAttribute('data-v'), y = b.cells[index].getAttribute('data-v');
          return numeric ? (num(x) - num(y)) * dir : String(x).localeCompare(String(y)) * dir;
        });
        rows.forEach(function(r){ body.appendChild(r); });
      });
    });
  });
  document.querySelectorAll('[data-table-controls]').forEach(function(ctl){
    var table = document.getElementById(ctl.getAttribute('data-table-controls'));
    var buttons = ctl.querySelectorAll('button[data-filter]'), search = ctl.querySelector('input.search');
    var state = {filter: ctl.getAttribute('data-default') || 'ACTIONABLE', text: ''};
    function apply(){
      Array.prototype.forEach.call(table.tBodies[0].rows, function(r){
        var cls = r.getAttribute('data-class') || '', hay = (r.getAttribute('data-search') || '').toLowerCase();
        var okClass = state.filter === 'ALL' || (state.filter === 'ACTIONABLE' ? !(cls === 'NO_TRADE') : cls === state.filter);
        r.style.display = okClass && hay.indexOf(state.text) >= 0 ? '' : 'none';
      });
      buttons.forEach(function(b){ b.setAttribute('aria-pressed', b.getAttribute('data-filter') === state.filter); });
    }
    buttons.forEach(function(b){ b.addEventListener('click', function(){ state.filter = b.getAttribute('data-filter'); apply(); }); });
    if (search){ search.addEventListener('input', function(){ state.text = search.value.trim().toLowerCase(); apply(); }); }
    apply();
  });
})();
</script>"""


def _u(value):
    return "UNKNOWN" if value is None or value == "" else value


def _cell(display, sort_value=None, numeric=False):
    display = _u(display)
    sort_value = "" if sort_value is None else sort_value
    css = ' class="num"' if numeric else ""
    return f'<td{css} data-v="{escape(str(sort_value), quote=True)}">{escape(str(display))}</td>'


def _millions(value):
    return None if value is None else f"{value / 1_000_000:.1f}M"


def _weekday(iso):
    from datetime import date
    try:
        return date.fromisoformat(iso).strftime("%a")
    except (TypeError, ValueError):
        return None


def render_session_context(report, coverage=None):
    if report is None:
        return ('<section class="ctx" aria-label="Session context"><div><div class="k">Ranking</div>'
                '<div class="v">UNAVAILABLE</div></div><div><div class="k">Safety</div>'
                '<div class="v safety">LIVE MONEY DISABLED</div></div></section>')
    fresh = Counter(r.get("freshness") or "UNKNOWN" for r in report["symbols"])
    current = fresh.get("CURRENT", 0)
    session = report.get("based_on_session") or report["session"]
    nxt = report.get("next_expected_session")
    items = [
        ("Latest completed session", f"{_weekday(session) or ''} {session}".strip()),
        ("Next expected session", f"{_weekday(nxt) or ''} {_u(nxt)}".strip()),
        ("Data freshness", f"{current} of {len(report['symbols'])} current"),
        ("Source", report["provider"].replace("_", " ")),
        ("Rule", report["rank_version"]),
        ("Generated", report["generated_at"][:16].replace("T", " ") + " UTC"),
    ]
    cells = "".join(f'<div><div class="k">{escape(k)}</div><div class="v">{escape(str(v))}</div></div>'
                    for k, v in items)
    licence = (f'{report["admission"]} · {report["licensing"]}'
               + (f' · next session {report["next_session_basis"].split(":")[0].lower()}'
                  if report.get("next_session_basis") else ""))
    return (f'<section class="ctx" aria-label="Session context">{cells}</section>'
            '<p><span class="safety">LIVE MONEY DISABLED · Paper/Shadow research · a candidate is not a fill</span></p>'
            f'<p class="muted">Source status: {escape(licence)}. '
            f'{escape(report.get("prepared_note") or "")}</p>')


def summary_counts(report, coverage=None):
    levels = {row["level"]: row["count"] for row in (coverage or [])}
    if report is None:
        return {"scanned": levels.get("scanned_symbols"), "admitted": levels.get("admitted_symbols"),
                "current": levels.get("admitted_current_symbols"), "ranked": None,
                "candidates": None, "strong": None}
    counts = report["counts"]
    return {"scanned": report["symbols_ranked"],
            "admitted": levels.get("admitted_symbols"),
            "current": levels.get("admitted_current_symbols"),
            "ranked": sum(1 for r in report["symbols"] if r.get("score") is not None),
            "candidates": counts.get("STRONG_CANDIDATE", 0) + counts.get("CANDIDATE", 0),
            "strong": counts.get("STRONG_CANDIDATE", 0)}


def render_cards(report, coverage=None):
    labels = [("scanned", "Scanned"), ("admitted", "Admitted source"), ("current", "Admitted & current"),
              ("ranked", "Ranked"), ("candidates", "Candidates"), ("strong", "Strong candidates")]
    values = summary_counts(report, coverage)
    cards = "".join(f'<div class="card"><div class="n">{escape(str(_u(values[key])))}</div>'
                    f'<div class="l">{escape(label)}</div></div>' for key, label in labels)
    return f'<section class="cards" aria-label="Summary">{cards}</section>'


def render_insights(report):
    if report is None:
        return ""
    actionable = [r for r in report["symbols"] if r.get("classification") in ("STRONG_CANDIDATE", "CANDIDATE")]
    top = max(actionable, key=lambda r: (r.get("score") or 0), default=None)
    counts = report["counts"]
    if top:
        top_html = (f'<div class="v"><span class="tk">{escape(top["ticker"])}</span> '
                    f'<span class="badge {escape(top["classification"])}">{CLASS_LABEL[top["classification"]]}</span></div>'
                    f'<div class="s">{escape(str(top.get("company") or ""))} · score {escape(str(_u(top.get("score"))))}'
                    f' · entry {escape("–".join(top["entry_zone"]) if top.get("entry_zone") else "UNKNOWN")}'
                    f' · stop {escape(str(_u(top.get("stop"))))}</div>')
    else:
        top_html = '<div class="v">None</div><div class="s">No symbol passed all candidate rules.</div>'
    nxt = report.get("next_expected_session")
    blocks = [f'<div class="insight"><div class="k">Top idea</div>{top_html}</div>']
    for cls in ("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST"):
        blocks.append(f'<div class="insight"><div class="k">{CLASS_LABEL[cls]}</div>'
                      f'<div class="v">{counts.get(cls, 0)}</div></div>')
    blocks.append(f'<div class="insight"><div class="k">Next evaluation</div>'
                  f'<div class="v">{escape(str(_u(nxt)))}</div><div class="s">{escape(_weekday(nxt) or "")} · expected</div></div>')
    return f'<section class="insights" aria-label="Quick insights">{"".join(blocks)}</section>'


COLUMNS = [
    ("Symbol", "text", False), ("Class", "num", False), ("Score", "num", True), ("Price", "num", True),
    ("Entry zone", "num", True), ("Stop", "num", True), ("Target 1", "num", True), ("Target 2", "num", True),
    ("R:R", "num", True), ("Trend", "text", False), ("Mom. 20s %", "num", True), ("Vol ×20s", "num", True),
    ("RS (pp)", "num", True), ("Liquidity/day", "num", True), ("Evidence", "text", False),
]


def _row(record):
    cls = record.get("classification") or "NO_TRADE"
    zone = record.get("entry_zone")
    reason = record.get("selection_reason") if cls != "NO_TRADE" else ", ".join(record.get("rejection_reasons") or [])
    bars, quarantined = record.get("history_bars"), record.get("quarantined_rows")
    quality = None if bars is None else f"{bars} bars, {quarantined or 0} quarantined"
    volume = record.get("volume_ratio_20")
    volume_text = None if volume is None else f"{volume}×{' ✓' if record.get('volume_confirmation') else ''}"
    rr = (None if record.get("risk_reward_t1") is None
          else f"{record['risk_reward_t1']} / {record.get('risk_reward_t2')}")
    ticker, company = record.get("ticker") or "UNKNOWN", record.get("company") or ""
    evidence = (f'<details><summary>details</summary><div>'
                f'<b>Why:</b> {escape(str(_u(reason or None)))}<br>'
                f'<b>History:</b> {escape(str(_u(quality)))}<br>'
                f'<b>Freshness:</b> {escape(str(_u(record.get("freshness"))))} · '
                f'<b>Evidence date:</b> {escape(str(_u(record.get("evidence_snapshot_date") or record.get("last_market_session"))))}<br>'
                f'<b>Warnings:</b> {escape(", ".join(record.get("data_warnings") or []) or "none")}'
                f'</div></details>')
    cells = [
        f'<td data-v="{escape(ticker, quote=True)}"><span class="tk">{escape(ticker)}</span>'
        f'<span class="co">{escape(company)}</span></td>',
        f'<td data-v="{CLASS_ORDER.index(cls) if cls in CLASS_ORDER else 9}">'
        f'<span class="badge {escape(cls)}">{escape(cls)}</span></td>',
        _cell(record.get("score"), record.get("score"), True),
        _cell(record.get("price"), record.get("price"), True),
        _cell(None if not zone else f"{zone[0]}–{zone[1]}", zone[0] if zone else None, True),
        _cell(record.get("stop"), record.get("stop"), True),
        _cell(record.get("target_1"), record.get("target_1"), True),
        _cell(record.get("target_2"), record.get("target_2"), True),
        _cell(rr, record.get("risk_reward_t1"), True),
        _cell(record.get("trend"), record.get("trend")),
        _cell(record.get("momentum_20_pct"), record.get("momentum_20_pct"), True),
        _cell(volume_text, volume, True),
        _cell(record.get("relative_strength_20_pp"), record.get("relative_strength_20_pp"), True),
        _cell(_millions(record.get("liquidity_avg_traded_value_20")), record.get("liquidity_avg_traded_value_20"), True),
        f'<td>{evidence}</td>',
    ]
    search = escape(f"{ticker} {company}", quote=True)
    return f'<tr data-class="{escape(cls)}" data-search="{search}">' + "".join(cells) + "</tr>"


def render_candidate_table(report, *, table_id, classes=CLASS_ORDER, title="EGX candidates and watchlist",
                           default="ACTIONABLE"):
    if report is None:
        return f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p>UNAVAILABLE: no ranking report.</p></section>'
    rows = [r for r in report["symbols"] if (r.get("classification") or "NO_TRADE") in classes]
    counts = Counter(r.get("classification") or "NO_TRADE" for r in rows)
    filters = [("ACTIONABLE", f"Actionable ({sum(v for k, v in counts.items() if k != 'NO_TRADE')})"),
               ("ALL", f"All ({len(rows)})")] + [
        (c, f"{CLASS_LABEL[c]} ({counts.get(c, 0)})") for c in classes]
    buttons = "".join(f'<button type="button" data-filter="{key}" aria-pressed="{str(key == default).lower()}">'
                      f'{escape(label)}</button>' for key, label in filters)
    head = "".join(f'<th scope="col" data-sort="{kind}"{" class=\"num\"" if numeric else ""}>{escape(name)}</th>'
                   for name, kind, numeric in COLUMNS)
    body = "".join(_row(r) for r in rows) or f'<tr><td colspan="{len(COLUMNS)}">none</td></tr>'
    return (f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2>'
            f'<div class="toolbar" data-table-controls="{table_id}" data-default="{default}">'
            f'<div class="seg" role="group" aria-label="Filter by class">{buttons}</div>'
            f'<input class="search" type="search" placeholder="Search ticker or company" aria-label="Search">'
            f'<span class="muted">Click a header to sort · without JavaScript all rows are shown</span></div>'
            f'<div class="table-wrap"><table id="{table_id}" data-sortable aria-label="{escape(title)}">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>')


def render_legend():
    items = "".join(f'<li><span class="badge {c}">{c}</span> {escape(CLASS_TEXT[c])}</li>' for c in CLASS_ORDER)
    return ('<details><summary>How classifications work (EGX-RANK-v1)</summary><ul class="legend">' + items
            + '</ul><p class="muted">Entry zone = close ± 0.5 % (next eligible session only); stop = close − '
            'max(1.5 × ATR14, 3 %); T1 = +1.5 R, T2 = +3 R. RS = 20-session momentum minus the median of '
            'all ranked symbols for the same session. A score never overrides a failed hard gate.</p></details>')


def render_no_trade(report):
    """NO_TRADE rows are in the main table (No trade filter); this keeps the reason histogram."""
    if report is None:
        return ""
    rows = [r for r in report["symbols"] if r.get("classification") == "NO_TRADE"]
    reasons = Counter(reason.split(":")[0] for r in rows for reason in (r.get("rejection_reasons") or ["UNSPECIFIED"]))
    items = "".join(f"<li>{escape(k)}: {v}</li>" for k, v in reasons.most_common())
    return (f'<details><summary>Why {len(rows)} symbols are NO_TRADE</summary><ul>{items}</ul>'
            '<p class="muted">Use the “No trade” filter above to see each symbol and its reason.</p></details>')


def details(title, html):
    return f'<details><summary>{escape(title)}</summary>{html}</details>' if html else ""
