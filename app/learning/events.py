"""Structured event taxonomy from admitted context evidence (EVENT-TAX-v1).

Sources, all from the context report (app/context/run.py):
- EGX official disclosures and financial-statement disclosures, mapped to
  companies by ISIN;
- Federal Reserve monetary-policy releases;
- context regimes: Brent, gold, USD/EGP, the Fed's last change, and shipping
  chokepoints.

Each event records its type, the event or publication time, ``first_seen`` (the
platform's retrieval time), the source and reference, the region, the mapped
sectors or companies, an expected-direction hypothesis, confidence and horizon.
Classification is keyword and rule based: auditable, never an LLM. Sector
mappings are explicit hypotheses, never claims of causation. An event can be
attributed to a forecast only when its first-seen time is at or before the
forecast cutoff.
"""
from __future__ import annotations

import re

VERSION = "EVENT-TAX-v1"
KEYWORDS = (
    ("DIVIDEND", r"dividend|coupon|cash distribution"),
    ("CAPITAL_INCREASE", r"capital increase|rights issue|increase (of|in) (the )?(issued|paid)"),
    ("ACQUISITION", r"acquisition|acquire|takeover|tender offer|mandatory offer"),
    ("MERGER", r"merger|merge"),
    ("GOVERNMENT_CONTRACT", r"contract|award|tender"),
    ("REGULATORY_CHANGE", r"fra decree|decree|regulation|listing rules"),
)
SECTOR_HYPOTHESES = {
    "BRENT_MOVE": {"sectors": ["Energy", "Petrochemicals", "Chemicals", "Transport"], "direction": "MIXED",
                   "note": "energy producers vs fuel-cost users; hypothesis only"},
    "GOLD_MOVE": {"sectors": ["Basic Resources", "Mining"], "direction": "SAME_AS_MOVE", "note": "hypothesis only"},
    "CURRENCY_EVENT": {"sectors": ["Exporters", "Importers", "Banks"], "direction": "MIXED",
                       "note": "EGP weakness helps exporters and hurts importers; hypothesis only"},
    "INTEREST_RATE_HIKE": {"sectors": ["Banks", "Real Estate"], "direction": "MIXED", "note": "hypothesis only"},
    "INTEREST_RATE_CUT": {"sectors": ["Real Estate", "Construction"], "direction": "POSITIVE", "note": "hypothesis only"},
    "RED_SEA_DISRUPTION": {"sectors": ["Shipping & Transportation Services", "Logistics"], "direction": "NEGATIVE",
                           "note": "Suez revenue and trade routes; hypothesis only"},
    "ENERGY_PRICE_SHOCK": {"sectors": ["Energy", "Industrials"], "direction": "MIXED", "note": "hypothesis only"},
}


def classify_disclosure(heading, section=""):
    text = f"{heading} {section}".lower()
    for event_type, pattern in KEYWORDS:
        if re.search(pattern, text):
            return event_type
    return "MAJOR_COMPANY_DISCLOSURE"


def extract(context, *, market):
    if not context:
        return {"by_isin": {}, "events": [], "summary": {"status": "UNAVAILABLE", "version": VERSION,
                                                         "reason": "no context report"}}
    events = []
    egx = market == "EGX"
    disclosures = ((context.get("events") or {}).get("egx_disclosures") or {})
    first_seen = None
    if isinstance(disclosures.get("provenance"), list) and disclosures["provenance"]:
        first_seen = disclosures["provenance"][0].get("retrieved_at")
    if egx and disclosures.get("status") == "AVAILABLE":
        for record in disclosures.get("records", []):
            events.append({"event_type": classify_disclosure(record.get("heading", ""), record.get("section", "")),
                           "event_timestamp": record.get("published_at"), "published_at": record.get("published_at"),
                           "first_seen": first_seen, "source": "EGX official disclosures", "reference": record.get("code"),
                           "documents": record.get("documents", [])[:2], "region": "Egypt", "isin": record.get("isin"),
                           "heading": record.get("heading"), "direction": "UNSPECIFIED", "confidence": "SOURCED_FACT",
                           "horizon": "1-5 sessions"})
    statements = ((context.get("fundamentals") or {}).get("egx_financial_statements") or {})
    if egx and statements.get("status") == "AVAILABLE":
        for record in statements.get("records", []):
            change = record.get("net_result_change_pct")
            if change is None:
                continue
            events.append({"event_type": "EARNINGS_BEAT" if float(change) > 0 else "EARNINGS_MISS",
                           "event_timestamp": record.get("published_at"), "published_at": record.get("published_at"),
                           "first_seen": first_seen, "source": "EGX financial-statement disclosure",
                           "reference": record.get("code"), "region": "Egypt", "isin": record.get("isin"),
                           "heading": record.get("heading"), "direction": "POSITIVE" if float(change) > 0 else "NEGATIVE",
                           "confidence": "SOURCED_FACT",
                           "detail": f"net result {change}% vs disclosed comparative (not vs expectations)",
                           "horizon": "1-5 sessions"})
    regimes = context.get("regimes") or {}
    generated = context.get("generated_at")
    for key, event_type in (("brent", "BRENT_MOVE"), ("gold", "GOLD_MOVE")):
        item = regimes.get(key) or {}
        if item.get("label") in ("RISING", "FALLING"):
            events.append({"event_type": event_type, "event_timestamp": item.get("date"), "published_at": item.get("date"),
                           "first_seen": generated, "source": "context regime (market quotes)", "region": "Global",
                           "detail": f"{item.get('change_pct')}% over 20 sessions", **_hypothesis(event_type)})
    if (regimes.get("egp") or {}).get("label") in ("EGP_WEAKENING", "EGP_STRENGTHENING"):
        item = regimes["egp"]
        events.append({"event_type": "CURRENCY_EVENT", "event_timestamp": item.get("date"), "published_at": item.get("date"),
                       "first_seen": generated, "source": "context regime (USD/EGP market rate)", "region": "Egypt",
                       "detail": f"USD/EGP {item.get('change_pct')}% over 20 sessions", **_hypothesis("CURRENCY_EVENT")})
    fed = regimes.get("fed") or {}
    if fed.get("label") in ("TIGHTENING", "EASING") and fed.get("last_change"):
        event_type = "INTEREST_RATE_HIKE" if fed["label"] == "TIGHTENING" else "INTEREST_RATE_CUT"
        events.append({"event_type": event_type, "event_timestamp": fed["last_change"], "published_at": fed["last_change"],
                       "first_seen": generated, "source": "Federal Reserve target range (FRED)", "region": "United States",
                       "detail": f"{fed.get('from')} -> {fed.get('to')}", **_hypothesis(event_type)})
    for name, item in (regimes.get("chokepoints") or {}).items():
        if item.get("label") == "DISRUPTED":
            event_type = "RED_SEA_DISRUPTION" if name in ("Suez Canal", "Bab el-Mandeb Strait") else "ENERGY_PRICE_SHOCK"
            events.append({"event_type": event_type, "event_timestamp": item.get("date"), "published_at": item.get("date"),
                           "first_seen": generated, "source": f"IMF PortWatch ({name})", "region": name,
                           "detail": f"7-day transits {item.get('change_7d_vs_90d_pct')}% vs 90-day mean",
                           **_hypothesis(event_type)})
    by_isin = {}
    for event in events:
        if event.get("isin"):
            by_isin.setdefault(event["isin"], []).append(event)
    counts = {}
    for event in events:
        counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
    return {"by_isin": by_isin, "events": events,
            "summary": {"status": "AVAILABLE", "version": VERSION, "counts": counts, "total": len(events),
                        "recent": sorted(events, key=lambda e: e.get("published_at") or "", reverse=True)[:25],
                        "model_inputs": "NOT_YET: event history too short for out-of-sample evaluation",
                        "causality": "hypothesis mappings only; no causal claim"}}


def _hypothesis(event_type):
    item = SECTOR_HYPOTHESES.get(event_type, {})
    return {"mapped_sectors": item.get("sectors", []), "direction": item.get("direction", "UNSPECIFIED"),
            "confidence": "HYPOTHESIS", "mapping_note": item.get("note"), "horizon": "1-20 sessions"}
