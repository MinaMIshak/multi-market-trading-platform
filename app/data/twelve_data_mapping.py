"""Map Twelve Data XCAI equities to the EGX security master by ISIN.

Twelve Data's XCAI ``symbol`` is the instrument ISIN, sometimes followed by a
currency suffix (``EGS38461C017.EGP``). The egid security master's
``source_symbol_code`` is the ISIN for every EQUITY. Matching is exact, never
by name:

- ``matched_exact``: the provider symbol equals a known equity's ISIN;
- ``matched_suffix``: the provider symbol is ``<ISIN>.EGP``, the ISIN is a known
  equity, and no exact listing exists for it (reported separately);
- ``ambiguous``: the ISIN maps to several instruments, or to several provider
  symbols; never mapped;
- ``provider_only``: a provider symbol with no known equity;
- ``unmatched_known``: a known equity with no provider symbol.

ISINs must pass the ISO 6166 check digit. Aliases are written only for
matched rows, idempotently (``INSERT OR IGNORE``), as provider ``twelve_data``,
type ``TWELVE_DATA_SYMBOL``. A security-master refresh deletes aliases, so the
refresh job re-applies the mapping before every run. A mapping is identity
evidence only: no prices, no membership claim, no admission.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re

PROVIDER = "twelve_data"
ALIAS_TYPE = "TWELVE_DATA_SYMBOL"
SUFFIXES = (".EGP",)
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def valid_isin(value: str) -> bool:
    if not isinstance(value, str) or not ISIN.match(value):
        return False
    digits = "".join(str(int(char, 36)) for char in value[:-1])
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2 == 0:
            number *= 2
            number = number - 9 if number > 9 else number
        total += number
    return (10 - total % 10) % 10 == int(value[-1])


def _base_isin(symbol: str) -> tuple[str, str | None]:
    for suffix in SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)], suffix
    return symbol, None


def known_equities(connection) -> list[dict]:
    return [dict(row) for row in connection.execute(
        "SELECT instrument_id, canonical_ticker, source_symbol_code AS isin, name_en "
        "FROM canonical_instruments WHERE instrument_type='EQUITY' ORDER BY canonical_ticker")]


def build_mapping(provider_rows: list[dict], equities: list[dict]) -> dict:
    by_isin = defaultdict(list)
    for equity in equities:
        by_isin[str(equity["isin"]).strip().upper()].append(equity)
    listings = defaultdict(list)
    invalid = []
    for row in provider_rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        base, suffix = _base_isin(symbol)
        if not valid_isin(base):
            invalid.append({"symbol": symbol, "name": row.get("name")})
            continue
        listings[base].append({"symbol": symbol, "suffix": suffix, "name": row.get("name"),
                               "type": row.get("type")})
    report = {"matched_exact": [], "matched_suffix": [], "ambiguous": [],
              "provider_only": [], "unmatched_known": [], "invalid_provider_symbols": invalid}
    for isin, items in sorted(listings.items()):
        known = by_isin.get(isin, [])
        exact = [item for item in items if item["suffix"] is None]
        if len(known) > 1 or len(exact) > 1 or (not exact and len(items) > 1):
            report["ambiguous"].append({"isin": isin, "provider_symbols": [i["symbol"] for i in items],
                                        "tickers": [k["canonical_ticker"] for k in known]})
            continue
        if not known:
            report["provider_only"].extend({"isin": isin, **item} for item in items)
            continue
        chosen = exact[0] if exact else items[0]
        entry = {"ticker": known[0]["canonical_ticker"], "instrument_id": known[0]["instrument_id"],
                 "isin": isin, "provider_symbol": chosen["symbol"], "provider_name": chosen["name"],
                 "provider_type": chosen["type"]}
        report["matched_exact" if chosen["suffix"] is None else "matched_suffix"].append(entry)
    mapped = {item["isin"] for item in report["matched_exact"] + report["matched_suffix"]}
    ambiguous = {item["isin"] for item in report["ambiguous"]}
    for isin, known in sorted(by_isin.items()):
        if isin not in mapped and isin not in ambiguous:
            report["unmatched_known"].extend(
                {"ticker": k["canonical_ticker"], "isin": isin} for k in known)
    report["summary"] = {
        "known_equities": len(equities), "provider_rows": len(provider_rows),
        **{key: len(value) for key, value in report.items() if isinstance(value, list)},
    }
    report["summary"]["matched"] = (report["summary"]["matched_exact"]
                                    + report["summary"]["matched_suffix"])
    return report


def targets(report: dict) -> list[tuple[str, str]]:
    """(canonical ticker, provider symbol) for every matched equity, sorted."""
    return sorted((item["ticker"], item["provider_symbol"])
                  for item in report["matched_exact"] + report["matched_suffix"])


def apply_aliases(connection, report: dict) -> int:
    """Idempotently record matched provider symbols as aliases; returns rows inserted."""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for item in report["matched_exact"] + report["matched_suffix"]:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO instrument_aliases (instrument_id, provider, alias_type, "
            "alias_value, normalized_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (item["instrument_id"], PROVIDER, ALIAS_TYPE, item["provider_symbol"],
             item["provider_symbol"].strip().upper(), now))
        inserted += cursor.rowcount
    return inserted
