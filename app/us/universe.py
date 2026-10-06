"""US security master and research universe (US-UNIVERSE-v1).

Security master (official listing evidence): Nasdaq Trader symbol directories
``nasdaqlisted.txt`` (Nasdaq) and ``otherlisted.txt`` (NYSE, NYSE American,
NYSE Arca, Cboe and others). Test issues are excluded; ETFs are flagged.

Research universe (liquidity screen): TradingView's US scanner lists primary
common stocks on NASDAQ / NYSE / AMEX with the 30-day average volume and the
last close. Members are the top ``size`` by 30-day average dollar volume
(volume × close) that are also present, non-test and non-ETF in the
Nasdaq Trader master. The screen uses *current* liquidity: it selects names
for forward Paper/Shadow research and makes no point-in-time or survivorship
claim for history.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

VERSION = "US-UNIVERSE-v1"
SCANNER_URL = "https://scanner.tradingview.com/america/scan"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
OTHER_EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX",
                   "F": "Texas Stock Exchange", "M": "NYSE Texas"}
SCANNER_BODY = {
    "filter": [{"left": "type", "operation": "equal", "right": "stock"},
               {"left": "subtype", "operation": "equal", "right": "common"},
               {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
               {"left": "is_primary", "operation": "equal", "right": True}],
    "columns": ["name", "description", "exchange", "type", "subtype", "average_volume_30d_calc", "close",
                "market_cap_basic", "sector", "isin", "industry", "earnings_release_next_date",
                "earnings_release_date", "eps_surprise_percent_fq", "revenue_surprise_percent_fq",
                "total_revenue_yoy_growth_ttm", "net_margin", "return_on_equity", "price_earnings_ttm"],
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 6000]}


class UniverseError(ValueError):
    pass


def normalize(symbol: str) -> str:
    return symbol.strip().upper().replace("/", ".").replace("-", ".")


def _rows(text):
    lines = [line for line in text.strip().splitlines() if line and not line.startswith("File Creation Time")]
    if not lines:
        raise UniverseError("empty directory file")
    header = lines[0].split("|")
    return [dict(zip(header, line.split("|"))) for line in lines[1:]]


def parse_master(nasdaq_text: str, other_text: str) -> dict:
    master = {}
    nasdaq = _rows(nasdaq_text)
    if not nasdaq or "Symbol" not in nasdaq[0]:
        raise UniverseError("nasdaqlisted header changed")
    for row in nasdaq:
        if row.get("Test Issue") == "Y":
            continue
        master[normalize(row["Symbol"])] = {"name": row["Security Name"].strip(), "exchange": "Nasdaq",
                                            "etf": row.get("ETF") == "Y",
                                            "financial_status": row.get("Financial Status") or None}
    other = _rows(other_text)
    if not other or "ACT Symbol" not in other[0]:
        raise UniverseError("otherlisted header changed")
    for row in other:
        if row.get("Test Issue") == "Y":
            continue
        master[normalize(row["ACT Symbol"])] = {"name": row["Security Name"].strip(),
                                                "exchange": OTHER_EXCHANGES.get(row["Exchange"], row["Exchange"]),
                                                "etf": row.get("ETF") == "Y", "financial_status": None}
    if len(master) < 3000:
        raise UniverseError(f"master implausibly small ({len(master)})")
    return master


def parse_scanner(payload: bytes) -> list[dict]:
    document = json.loads(payload)
    columns = SCANNER_BODY["columns"]
    out = []
    for item in document.get("data") or []:
        values = dict(zip(columns, item.get("d") or []))
        try:
            volume, close = Decimal(str(values["average_volume_30d_calc"])), Decimal(str(values["close"]))
        except (InvalidOperation, KeyError, TypeError):
            continue
        if not (volume.is_finite() and close.is_finite()) or volume <= 0 or close <= 0:
            continue
        out.append({"tv_symbol": item["s"], "ticker": normalize(values["name"]), "exchange": values["exchange"],
                    "name": values.get("description"), "isin": values.get("isin"), "sector": values.get("sector"),
                    "avg_dollar_volume_30d": volume * close, "close": close,
                    # Snapshot attributes (TradingView scanner, retrieval-time evidence; never historical truth).
                    "industry": values.get("industry"), "market_cap": values.get("market_cap_basic"),
                    "earnings_next": values.get("earnings_release_next_date"),
                    "earnings_last": values.get("earnings_release_date"),
                    "eps_surprise_pct": values.get("eps_surprise_percent_fq"),
                    "revenue_surprise_pct": values.get("revenue_surprise_percent_fq"),
                    "revenue_growth_ttm": values.get("total_revenue_yoy_growth_ttm"),
                    "net_margin": values.get("net_margin"), "roe": values.get("return_on_equity"),
                    "pe_ttm": values.get("price_earnings_ttm")})
    if len(out) < 1000:
        raise UniverseError(f"scanner returned too few rows ({len(out)})")
    return out


def build_universe(master: dict, scanner_rows: list[dict], *, size: int = 500) -> dict:
    eligible, excluded = [], {"NOT_IN_MASTER": 0, "ETF_IN_MASTER": 0}
    for row in sorted(scanner_rows, key=lambda r: (-r["avg_dollar_volume_30d"], r["ticker"])):
        listing = master.get(row["ticker"])
        if listing is None:
            excluded["NOT_IN_MASTER"] += 1
            continue
        if listing["etf"]:
            excluded["ETF_IN_MASTER"] += 1
            continue
        eligible.append({**row, "avg_dollar_volume_30d": str(row["avg_dollar_volume_30d"].quantize(Decimal(1))),
                         "close": str(row["close"]), "listing_exchange": listing["exchange"],
                         "listing_name": listing["name"], "financial_status": listing["financial_status"]})
    members = eligible[:size]
    return {"version": VERSION, "size": size, "members": members, "eligible": len(eligible),
            "scanner_rows": len(scanner_rows), "master_symbols": len(master), "excluded": excluded,
            "floor_avg_dollar_volume_30d": members[-1]["avg_dollar_volume_30d"] if members else None,
            "rule": (f"top {size} primary-listed common stocks by 30-day average dollar volume (TradingView scanner), "
                     "present, non-test and non-ETF in the Nasdaq Trader directories; current liquidity, "
                     "forward research only")}
