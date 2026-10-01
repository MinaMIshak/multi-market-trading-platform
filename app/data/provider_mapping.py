"""Exact ISIN mapping of a provider's EGX listing to the security master.

For providers whose listing carries ``symbol`` and ``isin`` (TradingView
symbol search does). The ISIN is the identity: the egid security master's
``source_symbol_code`` is the ISIN of every EQUITY. Categories:

- ``matched``: one provider listing and one known equity share a valid ISIN.
  ``ticker_agrees`` records whether the provider ticker equals the canonical
  ticker; a disagreement is reported, not repaired;
- ``ambiguous``: an ISIN with several listings or several equities; never mapped;
- ``provider_only``: a valid ISIN that is not in the security master;
- ``unmatched_known``: a known equity with no listing;
- ``invalid``: a listing without a valid ISIN (ISO 6166 check digit).

Aliases are written only for matched rows, idempotently. A mapping is identity
evidence only: no prices, no membership and no rights.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.data.twelve_data_mapping import known_equities, valid_isin  # noqa: F401  (re-export)


def build_isin_mapping(provider_rows: list[dict], equities: list[dict]) -> dict:
    by_isin = defaultdict(list)
    for equity in equities:
        by_isin[str(equity["isin"]).strip().upper()].append(equity)
    listings, invalid = defaultdict(list), []
    for row in provider_rows:
        isin = str(row.get("isin") or "").strip().upper()
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or not valid_isin(isin):
            invalid.append({"symbol": symbol, "isin": isin or None, "name": row.get("description")})
            continue
        listings[isin].append({"symbol": symbol, "name": row.get("description") or row.get("name"),
                               "type": row.get("type")})
    report = {"matched": [], "ambiguous": [], "provider_only": [], "unmatched_known": [],
              "invalid": invalid}
    for isin, items in sorted(listings.items()):
        known = by_isin.get(isin, [])
        if len(items) > 1 or len(known) > 1:
            report["ambiguous"].append({"isin": isin, "provider_symbols": [i["symbol"] for i in items],
                                        "tickers": [k["canonical_ticker"] for k in known]})
        elif not known:
            report["provider_only"].append({"isin": isin, **items[0]})
        else:
            item, equity = items[0], known[0]
            report["matched"].append({
                "ticker": equity["canonical_ticker"], "instrument_id": equity["instrument_id"],
                "isin": isin, "provider_symbol": item["symbol"], "provider_name": item["name"],
                "ticker_agrees": item["symbol"] == equity["canonical_ticker"]})
    covered = {m["isin"] for m in report["matched"]} | {a["isin"] for a in report["ambiguous"]}
    for isin, known in sorted(by_isin.items()):
        if isin not in covered:
            report["unmatched_known"].extend({"ticker": k["canonical_ticker"], "isin": isin} for k in known)
    report["summary"] = {"known_equities": len(equities), "provider_rows": len(provider_rows),
                         **{key: len(value) for key, value in report.items() if isinstance(value, list)},
                         "ticker_disagreements": sum(1 for m in report["matched"] if not m["ticker_agrees"])}
    return report


def mapped_pairs(report: dict) -> list[tuple[str, str]]:
    return sorted((m["ticker"], m["provider_symbol"]) for m in report["matched"])


def apply_aliases(connection, report: dict, *, provider: str, alias_type: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for item in report["matched"]:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO instrument_aliases (instrument_id, provider, alias_type, "
            "alias_value, normalized_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (item["instrument_id"], provider, alias_type, item["provider_symbol"],
             item["provider_symbol"].strip().upper(), now))
        inserted += cursor.rowcount
    return inserted
