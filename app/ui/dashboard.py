"""Public dashboard layer: session context, summary cards, candidate table.

Renders the recorded EGX-RANK-v1 report only. Values the engine did not
produce show as UNKNOWN; nothing is recomputed or invented here. Diagnostics
(receipts, readiness, raw observations) belong in collapsed sections or in
SYSTEM, not above the research output. A candidate is not a fill; LIVE MONEY
DISABLED.
"""
from collections import Counter
from html import escape

CLASS_ORDER = ("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST", "NO_TRADE")
CLASS_TEXT = {
    "STRONG_CANDIDATE": "All hard gates pass, score ≥ 75, uptrend, 20-session breakout and volume ≥ 1.5× average.",
    "CANDIDATE": "All hard gates pass, score ≥ 60 and uptrend.",
    "WATCHLIST": "All hard gates pass; score ≥ 45 or uptrend, but not a candidate.",
    "NO_TRADE": "A hard gate failed (reason shown) or the rules found a downtrend / low score.",
}

STYLE = (
    ".ctx{display:flex;flex-wrap:wrap;gap:8px 24px;background:#0d1b26;border:1px solid #1e3241;"
    "border-radius:10px;padding:12px 16px;margin:12px 0}.ctx b{color:#8bd5b0}"
    ".safety{color:#f2c94c;font-weight:bold}"
    ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:16px 0}"
    ".card{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:12px}"
    ".card .n{font-size:28px;font-weight:bold}.card .l{font-size:13px;color:#9fb3c2}"
    ".table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}"
    "th,td{padding:6px 8px;border-bottom:1px solid #1e3241;text-align:left;vertical-align:top}"
    "th[data-sort]{cursor:pointer;white-space:nowrap}th[data-sort]:after{content:' ⇅';color:#5d7486}"
    ".badge{display:inline-block;padding:2px 6px;border-radius:6px;font-size:12px;font-weight:bold}"
    ".STRONG_CANDIDATE{background:#1f6f43}.CANDIDATE{background:#25576f}"
    ".WATCHLIST{background:#5b5222}.NO_TRADE{background:#4a2a2a}"
    ".filters{margin:8px 0}.filters label{margin-right:12px}.muted{color:#9fb3c2;font-size:13px}"
    "details{background:#0b1620;border:1px solid #1e3241;border-radius:10px;padding:8px 14px;margin:12px 0}"
    "summary{cursor:pointer;font-weight:bold}"
)

SCRIPT = """<script>
(function(){
  document.querySelectorAll('table[data-sortable]').forEach(function(table){
    table.querySelectorAll('th[data-sort]').forEach(function(th, index){
      th.addEventListener('click', function(){
        var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
        var dir = th.getAttribute('data-dir') === 'asc' ? -1 : 1;
        th.setAttribute('data-dir', dir === 1 ? 'asc' : 'desc');
        var numeric = th.getAttribute('data-sort') === 'num';
        rows.sort(function(a, b){
          var x = a.cells[index].getAttribute('data-v'), y = b.cells[index].getAttribute('data-v');
          if (numeric){ x = x === '' ? -Infinity : parseFloat(x); y = y === '' ? -Infinity : parseFloat(y);
            return (x - y) * dir; }
          return String(x).localeCompare(String(y)) * dir;
        });
        rows.forEach(function(r){ body.appendChild(r); });
      });
    });
  });
  document.querySelectorAll('.filters input[type=checkbox]').forEach(function(box){
    box.addEventListener('change', function(){
      var table = document.getElementById(box.getAttribute('data-table'));
      var shown = {};
      document.querySelectorAll('.filters input[data-table="' + table.id + '"]').forEach(function(b){
        shown[b.value] = b.checked; });
      Array.prototype.forEach.call(table.tBodies[0].rows, function(r){
        r.style.display = shown[r.getAttribute('data-class')] ? '' : 'none'; });
    });
  });
})();
</script>"""


def _u(value):
    return "UNKNOWN" if value is None or value == "" else value


def _cell(display, sort_value=None):
    display = _u(display)
    sort_value = "" if sort_value is None else sort_value
    return f'<td data-v="{escape(str(sort_value), quote=True)}">{escape(str(display))}</td>'


def _millions(value):
    return None if value is None else f"{value / 1_000_000:.1f}M"


def render_session_context(report, coverage=None):
    if report is None:
        return ('<div class="ctx" aria-label="Session context"><span><b>Ranking</b>: UNAVAILABLE '
                '(no ranking report connected)</span><span class="safety">LIVE MONEY DISABLED</span></div>')
    fresh = Counter(r.get("freshness") or "UNKNOWN" for r in report["symbols"])
    freshness = ", ".join(f"{k} {v}" for k, v in sorted(fresh.items()))
    items = [
        ("Latest completed session", report.get("based_on_session") or report["session"]),
        ("Next expected session", report.get("next_expected_session")),
        ("Data freshness", freshness),
        ("Source", f"{report['provider']} · {report['admission']} · {report['licensing']}"),
        ("Rule", report["rank_version"]),
        ("Generated", report["generated_at"]),
    ]
    spans = "".join(f"<span><b>{escape(k)}</b>: {escape(str(_u(v)))}</span>" for k, v in items)
    note = report.get("prepared_note")
    basis = report.get("next_session_basis")
    return ('<div class="ctx" aria-label="Session context">' + spans
            + '<span class="safety">LIVE MONEY DISABLED · Paper/Shadow research · a candidate is not a fill</span>'
            + '</div>'
            + (f'<p class="muted">{escape(note)} {escape("(" + basis + ")") if basis else ""}</p>' if note else ""))


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
    labels = [("scanned", "Scanned (evaluated)"), ("admitted", "Admitted-source symbols"),
              ("current", "Admitted & session-current"), ("ranked", "Ranked (scored)"),
              ("candidates", "Candidates"), ("strong", "Strong candidates")]
    values = summary_counts(report, coverage)
    cards = "".join(f'<div class="card"><div class="n">{escape(str(_u(values[key])))}</div>'
                    f'<div class="l">{escape(label)}</div></div>' for key, label in labels)
    return f'<section class="cards" aria-label="Summary">{cards}</section>'


COLUMNS = [
    ("Ticker", "text"), ("Company", "text"), ("Class", "text"), ("Score", "num"), ("Price", "num"),
    ("Entry zone", "num"), ("Stop", "num"), ("Target 1", "num"), ("Target 2", "num"), ("R:R T1/T2", "num"),
    ("Trend", "text"), ("Momentum 20s %", "num"), ("Volume ×20s", "num"), ("Liquidity EGP/day", "num"),
    ("RS vs median (pp)", "num"), ("History quality", "num"), ("Freshness", "text"),
    ("Evidence date", "text"), ("Why selected / rejected", "text"), ("Warnings", "text"),
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
    cells = [
        _cell(record.get("ticker"), record.get("ticker")),
        _cell(record.get("company"), record.get("company")),
        f'<td data-v="{CLASS_ORDER.index(cls) if cls in CLASS_ORDER else 9}">'
        f'<span class="badge {escape(cls)}">{escape(cls)}</span></td>',
        _cell(record.get("score"), record.get("score")),
        _cell(record.get("price"), record.get("price")),
        _cell(None if not zone else f"{zone[0]}–{zone[1]}", zone[0] if zone else None),
        _cell(record.get("stop"), record.get("stop")),
        _cell(record.get("target_1"), record.get("target_1")),
        _cell(record.get("target_2"), record.get("target_2")),
        _cell(rr, record.get("risk_reward_t1")),
        _cell(record.get("trend"), record.get("trend")),
        _cell(record.get("momentum_20_pct"), record.get("momentum_20_pct")),
        _cell(volume_text, volume),
        _cell(_millions(record.get("liquidity_avg_traded_value_20")), record.get("liquidity_avg_traded_value_20")),
        _cell(record.get("relative_strength_20_pp"), record.get("relative_strength_20_pp")),
        _cell(quality, bars),
        _cell(record.get("freshness"), record.get("freshness")),
        _cell(record.get("evidence_snapshot_date") or record.get("last_market_session"),
              record.get("evidence_snapshot_date")),
        _cell(reason or None, reason),
        _cell(", ".join(record.get("data_warnings") or []) or "none", ""),
    ]
    return f'<tr data-class="{escape(cls)}">' + "".join(cells) + "</tr>"


def render_candidate_table(report, *, table_id, classes=("STRONG_CANDIDATE", "CANDIDATE", "WATCHLIST"),
                           title="Candidates and watchlist"):
    if report is None:
        return f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2><p>UNAVAILABLE: no ranking report.</p></section>'
    rows = [r for r in report["symbols"] if (r.get("classification") or "NO_TRADE") in classes]
    checks = "".join(
        f'<label><input type="checkbox" data-table="{table_id}" value="{c}" checked> '
        f'<span class="badge {c}">{c}</span> ({sum(1 for r in rows if r.get("classification") == c)})</label>'
        for c in classes)
    head = "".join(f'<th scope="col" data-sort="{kind}">{escape(name)}</th>' for name, kind in COLUMNS)
    body = "".join(_row(r) for r in rows) or f'<tr><td colspan="{len(COLUMNS)}">none</td></tr>'
    return (f'<section aria-label="{escape(title)}"><h2>{escape(title)}</h2>'
            f'<div class="filters">{checks}<span class="muted">Click a column header to sort.</span></div>'
            f'<div class="table-scroll"><table id="{table_id}" data-sortable aria-label="{escape(title)}">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>')


def render_legend():
    items = "".join(f'<li><span class="badge {c}">{c}</span> {escape(CLASS_TEXT[c])}</li>' for c in CLASS_ORDER)
    return ('<details><summary>Classification rules (EGX-RANK-v1)</summary><ul>' + items
            + '</ul><p class="muted">Entry zone = close ± 0.5 % (next eligible session only); stop = close − '
            'max(1.5 × ATR14, 3 %); T1 = +1.5 R, T2 = +3 R. RS = 20-session momentum minus the median of '
            'all ranked symbols for the same session. Scores never override a failed hard gate.</p></details>')


def render_no_trade(report):
    if report is None:
        return ""
    rows = [r for r in report["symbols"] if r.get("classification") == "NO_TRADE"]
    reasons = Counter(reason.split(":")[0] for r in rows for reason in (r.get("rejection_reasons") or ["UNSPECIFIED"]))
    summary = ", ".join(f"{k} {v}" for k, v in reasons.most_common())
    return ('<details><summary>NO_TRADE symbols (' + str(len(rows)) + ') · ' + escape(summary) + '</summary>'
            + render_candidate_table(report, table_id="no-trade", classes=("NO_TRADE",), title="NO_TRADE symbols")
            + '</details>')


def details(title, html):
    return f'<details><summary>{escape(title)}</summary>{html}</details>' if html else ""
