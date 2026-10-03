"""Macro and cross-asset context panel (RESEARCH): renders the recorded report only."""
import json
from html import escape
from pathlib import Path

from app.research.macro import BLOCKED, valid_report
from app.runtime_state import resolved_path

MAX_BYTES = 1024 * 1024


def load_macro():
    configured = resolved_path("macro")
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
    return report if valid_report(report) else None


def summary(report):
    if report is None:
        return {"status": "UNAVAILABLE"}
    rows = report["series"]
    return {"status": "AVAILABLE", "as_of": report["as_of"], "generated_at": report["generated_at"],
            "available": sum(1 for row in rows if row.get("status") == "AVAILABLE"),
            "current": sum(1 for row in rows if row.get("freshness") == "CURRENT"), "series": len(rows)}


def _u(value):
    return "UNKNOWN" if value is None else str(value)


def _signed(value, suffix=""):
    if value is None:
        return "UNKNOWN"
    return (value if value.startswith("-") else "+" + value) + suffix


def _blocked_list(items):
    return "<ul>" + "".join(f"<li><b>{escape(item['capability'])}</b>: {escape(item['reason'])}</li>"
                            for item in items) + "</ul>"


def render_macro(report):
    title = "<h2>Macro and cross-asset context</h2>"
    if report is None:
        return (f'<section aria-label="Macro context">{title}<p>UNAVAILABLE: no verified macro report in the '
                'runtime state.</p><h3>Not yet sourced</h3>'
                + _blocked_list([{"capability": n, "reason": r} for n, r in BLOCKED]) + "</section>")
    rows = ""
    for row in report["series"]:
        if row.get("status") == "AVAILABLE":
            pct = "pp" if row["unit"] == "%" else ""
            change_1 = _signed(row.get("change_1"), pct) + (f" ({_signed(row['change_1_pct'], '%')})"
                                                             if row.get("change_1_pct") else "")
            change_20 = _signed(row.get("change_20"), pct) + (f" ({_signed(row['change_20_pct'], '%')})"
                                                               if row.get("change_20_pct") else "")
            cells = (f'<td class="num">{escape(row["latest_value"])}</td><td>{escape(row["latest_date"])}</td>'
                     f'<td class="num">{escape(change_1)}</td><td class="num">{escape(change_20)}</td>'
                     f'<td>{escape(row["freshness"])} · {row["age_days"]}d</td>')
        else:
            cells = (f'<td class="num">UNKNOWN</td><td colspan="3">UNAVAILABLE: '
                     f'{escape(_u(row.get("reason")))}</td><td>UNKNOWN</td>')
        rows += (f'<tr><td><span class="tk">{escape(row["title"])}</span>'
                 f'<span class="co">{escape(row["series_id"])} · {escape(row["unit"])}</span></td>{cells}'
                 f'<td><span class="co">{escape(row["publisher"])}</span></td></tr>')
    derived = "".join(
        f'<li>{escape(item["metric"])}: <b>{escape(_u(item["value"]))} {escape(item["unit"])}</b> '
        f'({escape(item["shape"])}{", " + escape(item["date"]) if item.get("date") else ""})'
        f'{" — " + escape(item["reason"]) if item.get("reason") else ""}</li>' for item in report["derived"])
    return (f'<section aria-label="Macro context">{title}'
            f'<p class="muted">As of {escape(report["as_of"])} · generated '
            f'{escape(report["generated_at"][:16].replace("T", " "))} UTC · published U.S. government series '
            f'delivered by FRED; publisher shown per row. Context only: no macro value creates, upgrades or '
            f'vetoes a candidate.</p>'
            '<div class="table-wrap"><table aria-label="Macro series"><thead><tr><th>Series</th>'
            '<th class="num">Latest</th><th>Date</th><th class="num">Δ 1 obs</th><th class="num">Δ 20 obs</th>'
            f'<th>Freshness</th><th>Publisher</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<h3>Derived</h3><ul>{derived}</ul>'
            f'<details><summary>Not yet sourced ({len(report["blocked"])})</summary>'
            f'{_blocked_list(report["blocked"])}</details>'
            '<details><summary>Provenance</summary><ul>'
            + "".join(f'<li>{escape(row["series_id"])}: {escape(_u(row.get("url")))} · sha256 '
                      f'{escape(_u(row.get("raw_sha256")))} · retrieved {escape(_u(row.get("retrieved_at")))}'
                      f' · {escape(row["rights"])}</li>' for row in report["series"])
            + '</ul></details></section>')
