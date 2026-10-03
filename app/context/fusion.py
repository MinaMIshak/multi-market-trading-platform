"""Evidence fusion (FUSION-v1): context overlay for ranked symbols, kept separate from market truth.

The deterministic classification and score from market data are never
changed. The overlay adds, per symbol:

- ``context_adjustment``: a bounded integer in [-10, +5] from explicit rules;
- ``context_score``: score + adjustment (for ordering and review only);
- ``context_confidence``: HIGH / MEDIUM / LOW / UNKNOWN;
- ``notes``, ``catalysts`` and ``fundamentals``, each naming its evidence.

Rules (each one listed in ``notes`` when it fires):
- market context risk (CONTEXT-v1): LOW +3, ELEVATED 0, HIGH −7, UNKNOWN 0;
- equity regime RISK_OFF −3;
- EGX fundamentals, from the latest exchange financial-statement disclosure
  for the ISIN within 200 days: net result up vs the disclosed comparative
  +2, down −2, a net loss −3;
- catalysts (no score effect, event-risk notes only): an official disclosure
  for the ISIN within 5 days; a financial-results disclosure within 5 days.

One headline or one macro value can never create a candidate: the overlay
has no path to the classification, and media narrative is not an input.
"""
from __future__ import annotations

from datetime import datetime, timedelta

VERSION = "FUSION-v1"
MIN_ADJ, MAX_ADJ = -10, 5
RISK_POINTS = {"LOW": 3, "ELEVATED": 0, "HIGH": -7}


def _by_isin(records):
    out = {}
    for record in records or []:
        if record.get("isin"):
            out.setdefault(record["isin"], []).append(record)
    for items in out.values():
        items.sort(key=lambda r: r["published_at"], reverse=True)
    return out


def index_context(context):
    if not context:
        return None
    events = (context.get("events") or {}).get("egx_disclosures") or {}
    statements = (context.get("fundamentals") or {}).get("egx_financial_statements") or {}
    return {"regimes": context.get("regimes") or {}, "generated_at": context.get("generated_at"),
            "disclosures": _by_isin(events.get("records")) if events.get("status") == "AVAILABLE" else None,
            "statements": _by_isin(statements.get("records")) if statements.get("status") == "AVAILABLE" else None}


def overlay(record, *, market, index, as_of):
    if record.get("score") is None or record.get("classification") == "NO_TRADE":
        return None
    if index is None:
        return {"version": VERSION, "context_confidence": "UNKNOWN", "context_adjustment": 0,
                "context_score": record["score"], "notes": ["no verified context report"], "catalysts": []}
    regimes, notes, adjustment = index["regimes"], [], 0
    risk = ((regimes.get("risk") or {}).get(market) or {}).get("label", "UNKNOWN")
    points = RISK_POINTS.get(risk, 0)
    adjustment += points
    notes.append(f"{market} context risk {risk} ({points:+d})")
    equity = (regimes.get("egx_equity" if market == "EGX" else "us_equity") or {}).get("label")
    if equity == "RISK_OFF":
        adjustment -= 3
        notes.append("equity regime RISK_OFF (−3)")
    catalysts, fundamentals = [], None
    isin = record.get("isin")
    if market == "EGX" and isin:
        for item in (index["disclosures"] or {}).get(isin, []):
            published = datetime.fromisoformat(item["published_at"]).date()
            if (as_of - published).days <= 5:
                catalysts.append({"type": "RECENT_DISCLOSURE", "published_at": item["published_at"],
                                  "heading": item["heading"], "evidence": "OFFICIAL_FACT"})
        statements = [s for s in (index["statements"] or {}).get(isin, [])
                      if s.get("parse_status") == "PARSED"
                      and (as_of - datetime.fromisoformat(s["published_at"]).date()) <= timedelta(days=200)]
        if statements:
            latest = statements[0]
            fundamentals = {key: latest.get(key) for key in (
                "published_at", "basis", "period_end", "net_result", "comparative_net_result",
                "net_result_change_pct", "audit_status", "currency")}
            net, prior = float(latest["net_result"]), float(latest["comparative_net_result"])
            if net < 0:
                adjustment -= 3
                notes.append("latest disclosed net result is a loss (−3)")
            elif net > prior:
                adjustment += 2
                notes.append("net result up vs disclosed comparative (+2)")
            elif net < prior:
                adjustment -= 2
                notes.append("net result down vs disclosed comparative (−2)")
            if (as_of - datetime.fromisoformat(latest["published_at"]).date()).days <= 5:
                catalysts.append({"type": "EARNINGS_EVENT", "published_at": latest["published_at"],
                                  "heading": latest.get("heading"), "evidence": "OFFICIAL_FACT"})
    adjustment = max(MIN_ADJ, min(MAX_ADJ, adjustment))
    confidence = ("UNKNOWN" if risk == "UNKNOWN" else "HIGH" if adjustment >= 3 else
                  "LOW" if adjustment <= -5 else "MEDIUM")
    return {"version": VERSION, "context_confidence": confidence, "context_adjustment": adjustment,
            "context_score": round(record["score"] + adjustment, 2), "notes": notes, "catalysts": catalysts,
            "fundamentals": fundamentals, "context_generated_at": index.get("generated_at")}


def apply(report, *, market, context, as_of):
    """A copy of ``report`` whose symbols carry ``context``; the classification is untouched."""
    if report is None:
        return None
    index = index_context(context)
    symbols = []
    for record in report["symbols"]:
        fused = overlay(record, market=market, index=index, as_of=as_of)
        symbols.append({**record, "context": fused} if fused else record)
    return {**report, "symbols": symbols, "fusion_version": VERSION}
