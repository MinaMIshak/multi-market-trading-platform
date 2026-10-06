"""US daily pipeline: universe → sessions → acquisition → validation → ranking → Paper/Shadow.

    python -m app.us_run --data-root /abs/us-data --state /abs/state/us --tv-python /abs/python \
        [--tv-script tools/tradingview_fetch.py] [--size 500]

Market separation: US data lives under its own data root and state directory
and uses the NYSE calendar (America/New_York, Monday–Friday). Nothing here
reads or writes EGX data.

1. Universe (app/us/universe.py): Nasdaq Trader master plus the liquidity
   screen; raw files are stored content-addressed.
2. Sessions: rule calendar (app/us/nyse_calendar.py) verified against
   completed S&P 500 bars. The expected latest session must be both a rule
   session and observed, or freshness is UNKNOWN.
3. Acquisition: TradingView daily bars per member (raw stream stored first).
   The resolved identity must be a US stock in America/New_York, USD, on the
   member's exchange, with a matching ISIN when both sides provide one;
   otherwise the symbol is quarantined. Rows on rule non-sessions are
   quarantined. Canonical artifacts are immutable per (session, ticker); a
   differing re-acquisition is a recorded CONFLICT and never overwrites.
4. Cross-check: a deterministic sample is compared with Yahoo's chart API
   (same-date close, tolerance 0.5 %); report-only discrepancy evidence.
5. Ranking: US-RANK-v1 (the EGX-RANK-v1 rules with the US profile).
   STRONG_CANDIDATE / CANDIDATE are appended once to the US ledger, and
   lifecycles are simulated from later bars with US cost assumptions.
Paper/Shadow only; LIVE_MONEY=DISABLED. A candidate is not a fill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, time as dtime, timezone
from decimal import Decimal
from pathlib import Path

from app.context.fetch import FetchError, Fetcher, provenance, store_raw
from app.context.tradingview_series import (MarketSpec, SeriesError, completed_bars, current_trade_date,
                                            fetch_stream)
from app.data.providers.tradingview import parse_stream
from app.paper.system_candidates import CANDIDATE_CLASSES, performance, simulate
from app.strategies.egx_ranking import CLASSES, US_PROFILE, Bar, apply_relative_strength, classify
from app.us import nyse_calendar
from app.us.sources import US_DAILY_SOURCE
from app.us.universe import (NASDAQ_LISTED_URL, OTHER_LISTED_URL, SCANNER_BODY, SCANNER_URL, build_universe,
                             parse_master, parse_scanner)

SCHEMA = "us-ranking-report-v1"
SNAPSHOT_FIELDS = ("industry", "market_cap", "earnings_next", "earnings_last", "eps_surprise_pct",
                   "revenue_surprise_pct", "revenue_growth_ttm", "net_margin", "roe", "pe_ttm")
ORDER = {name: index for index, name in enumerate(CLASSES)}
US_SLIPPAGE, US_COMMISSION = Decimal("0.0005"), Decimal("0.0005")
SPX = MarketSpec("SPX", "SP:SPX", "S&P 500 index", "Equity indices", "points", "index", "America/New_York", "NYSE",
                 close=dtime(16, 0))
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
SCANNER_HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
                   "Origin": "https://www.tradingview.com"}
EXCHANGES = {"NASDAQ": "NASDAQ", "NYSE": "NYSE", "AMEX": "AMEX"}


def stock_spec(member) -> MarketSpec:
    return MarketSpec(member["ticker"], member["tv_symbol"], member.get("name") or member["ticker"], "US equity",
                      "USD", "stock", "America/New_York", "NYSE", "USD", close=dtime(16, 0))


def collect_universe(fetcher, data_root, *, size):
    provs = []
    texts = []
    for url in (NASDAQ_LISTED_URL, OTHER_LISTED_URL):
        payload, headers = fetcher.get(url)
        digest, _ = store_raw(data_root, "nasdaqtrader", payload, "txt")
        provs.append(provenance("nasdaqtrader", url, digest, headers=headers))
        texts.append(payload.decode("utf-8", "replace"))
    master = parse_master(*texts)
    payload, headers = fetcher.get(SCANNER_URL, data=json.dumps(SCANNER_BODY).encode(), headers=SCANNER_HEADERS,
                                   method="POST")
    digest, _ = store_raw(data_root, "tradingview_scanner", payload, "json")
    provs.append(provenance("tradingview_scanner", SCANNER_URL, digest, headers=headers))
    universe = build_universe(master, parse_scanner(payload), size=size)
    universe["provenance"] = provs
    return universe


def verify_sessions(spx_rows, *, now):
    observed = [date.fromisoformat(row["date"]) for row in spx_rows]
    current = current_trade_date(now, SPX)
    expected = nyse_calendar.previous_session(current)
    if not observed:
        return {"status": "UNKNOWN", "expected_session": expected.isoformat(), "verified": False}
    start = observed[max(0, len(observed) - 60)]
    checks = nyse_calendar.verified_sessions(observed, start, observed[-1])
    conflicts = {d.isoformat(): v for d, v in checks.items() if v.startswith("CONFLICT")}
    return {"status": "VERIFIED" if expected in set(observed) else "UNVERIFIED",
            "expected_session": expected.isoformat(), "verified": expected in set(observed),
            "latest_observed": observed[-1].isoformat(), "current_trade_date": current.isoformat(),
            "conflicts": conflicts, "window_start": start.isoformat()}


def identity_problems(member, resolved):
    problems = []
    if resolved.get("type") != "stock":
        problems.append("TYPE_NOT_STOCK")
    if resolved.get("timezone") != "America/New_York" or resolved.get("currency_code") != "USD":
        problems.append("NOT_NY_USD")
    if resolved.get("listed_exchange") != EXCHANGES.get(member["exchange"], member["exchange"]):
        problems.append("EXCHANGE_MISMATCH")
    if member.get("isin") and resolved.get("isin") and member["isin"] != resolved["isin"]:
        problems.append("ISIN_MISMATCH")
    return problems


def write_artifact(data_root, ticker, session, rows, meta):
    """Immutable per (session, ticker). Returns (path, status)."""
    folder = Path(data_root) / "us" / "canonical" / session
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ticker.replace('.', '_')}.json"
    payload = json.dumps({"ticker": ticker, "session": session, "rows": rows, **meta}, sort_keys=True).encode()
    try:
        with path.open("xb") as stream:
            stream.write(payload)
        return str(path), "WRITTEN"
    except FileExistsError:
        existing = json.loads(path.read_bytes())
        return str(path), "IDEMPOTENT" if existing["rows"] == rows else "CONFLICT_KEPT_EXISTING"


def acquire(member, *, data_root, now, tv_python, tv_script, expected, fetch=fetch_stream):
    spec = stock_spec(member)
    try:
        document = fetch(member["tv_symbol"], python_path=tv_python, fetch_script=tv_script, n_bars=300, delay=0.3)
        digest, _ = store_raw(data_root, "tradingview_us", json.dumps(document, sort_keys=True).encode(), "json")
        parsed = parse_stream(document["raw"])
        resolved = parsed.get("resolved") or {}
        problems = identity_problems(member, resolved)
        if problems:
            return {"status": "QUARANTINED", "reasons": problems, "raw_sha256": digest}
        rows = completed_bars(parsed, spec, now=now)
    except (SeriesError, KeyError, ValueError) as exc:
        return {"status": "FAILED", "reasons": [getattr(exc, "code", str(exc)[:80])]}
    valid = [r for r in rows if nyse_calendar.is_session(date.fromisoformat(r["date"])) and r["volume"] is not None]
    quarantined = len(rows) - len(valid)
    if not valid:
        return {"status": "FAILED", "reasons": ["NO_VALID_ROWS"], "raw_sha256": digest}
    session = valid[-1]["date"]
    path, artifact_status = write_artifact(data_root, member["ticker"], session, valid, {
        "provider": US_DAILY_SOURCE["provider"], "raw_sha256": digest, "isin": resolved.get("isin"),
        "price_adjustment": "splits", "quarantined_rows": quarantined, "retrieved_at": now.isoformat()})
    freshness = ("UNKNOWN" if not expected["verified"] else
                 "CURRENT" if session == expected["expected_session"] else
                 "STALE" if session < expected["expected_session"] else "UNKNOWN")
    return {"status": "ACQUIRED", "rows": valid, "quarantined_rows": quarantined, "raw_sha256": digest,
            "artifact": path, "artifact_status": artifact_status, "newest": session, "freshness": freshness,
            "isin": resolved.get("isin")}


def parse_yahoo(payload):
    document = json.loads(payload)
    result = (document.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("no chart result")
    stamps = result[0].get("timestamp") or []
    quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    offset = int((result[0].get("meta") or {}).get("gmtoffset") or 0)
    out = {}
    for stamp, close in zip(stamps, quote.get("close") or []):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp + offset, timezone.utc).date()
        out[day.isoformat()] = Decimal(str(close))
    return out


def cross_check(fetcher, data_root, acquired, *, sample=25, tolerance=Decimal("0.5")):
    tickers = sorted((t for t, item in acquired.items() if item["status"] == "ACQUIRED"),
                     key=lambda t: hashlib.sha256(t.encode()).hexdigest())[:sample]
    checks = []
    for ticker in tickers:
        primary = acquired[ticker]["rows"][-1]
        url = YAHOO_URL.format(ticker=ticker.replace(".", "-"))
        try:
            payload, headers = fetcher.get(url)
            digest, _ = store_raw(data_root, "yahoo_chart", payload, "json")
            closes = parse_yahoo(payload)
        except (FetchError, ValueError, KeyError) as exc:
            checks.append({"ticker": ticker, "status": "UNVERIFIED", "reason": getattr(exc, "code", str(exc)[:60])})
            continue
        other = closes.get(primary["date"])
        if other is None:
            checks.append({"ticker": ticker, "status": "UNVERIFIED", "reason": f"no Yahoo close on {primary['date']}"})
            continue
        diff = (other - Decimal(primary["close"])) / Decimal(primary["close"]) * 100
        checks.append({"ticker": ticker, "date": primary["date"], "primary": primary["close"],
                       "verification": str(other.quantize(Decimal("0.0001"))), "raw_sha256": digest,
                       "difference_pct": str(diff.quantize(Decimal("0.001"))),
                       "status": "AGREE" if abs(diff) <= tolerance else "DISCREPANT"})
    counts = Counter(item["status"] for item in checks)
    return {"source": "Yahoo Finance chart API (unofficial; verification only)", "tolerance_pct": str(tolerance),
            "sample": len(checks), "counts": dict(counts), "checks": checks}


def _read_ledger(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rank(acquired, universe, sessions, *, now, ledger_path):
    session = sessions["expected_session"]
    records, bars_by_symbol = [], {}
    members = {m["ticker"]: m for m in universe["members"]}
    for ticker, item in sorted(acquired.items()):
        member = members[ticker]
        if item["status"] != "ACQUIRED":
            records.append({"ticker": ticker, "company": member.get("name"), "isin": member.get("isin"),
                            "classification": "NO_TRADE", "rank_version": US_PROFILE.version, "score": None,
                            "source": US_DAILY_SOURCE["provider"], "freshness": "UNKNOWN",
                            "rejection_reasons": [f"{item['status']}:{','.join(item['reasons'])}"[:120]],
                            "data_warnings": [], "sector": member.get("sector")})
            continue
        bars = [Bar(r["date"], Decimal(r["open"]), Decimal(r["high"]), Decimal(r["low"]), Decimal(r["close"]),
                    Decimal(r["volume"])) for r in item["rows"]]
        bars_by_symbol[ticker] = bars
        record = classify(ticker=ticker, company=member.get("name"), isin=item.get("isin") or member.get("isin"),
                          source=US_DAILY_SOURCE["provider"], licensing=US_DAILY_SOURCE["licensing"],
                          admitted=US_DAILY_SOURCE["status"] == "ADMITTED", freshness=item["freshness"],
                          session=session, bars=[b for b in bars if b.date <= session],
                          quarantined_rows=item["quarantined_rows"], warnings=("SPLIT_ADJUSTED_SERIES",),
                          profile=US_PROFILE)
        record.update(evidence_snapshot_date=item["newest"], sector=member.get("sector"),
                      artifact_status=item["artifact_status"],
                      snapshot={k: member.get(k) for k in SNAPSHOT_FIELDS})
        records.append(record)
    median = apply_relative_strength(records)
    records.sort(key=lambda r: (ORDER.get(r["classification"], 9), -(r.get("score") or 0), r["ticker"]))
    next_session = nyse_calendar.next_session(date.fromisoformat(session)).isoformat()
    ledger = _read_ledger(ledger_path)
    known = {item["candidate_id"] for item in ledger}
    new = []
    for record in records:
        if record["classification"] not in CANDIDATE_CLASSES:
            continue
        candidate_id = hashlib.sha256(f"US|{record['ticker']}|{session}|{US_PROFILE.version}".encode()).hexdigest()[:24]
        if candidate_id in known:
            continue
        new.append({"candidate_id": candidate_id, "market": "US", "generated_at": now.isoformat(), "session": session,
                    "origin": "SYSTEM_GENERATED", "human_review": "OPTIONAL_NOT_REVIEWED", "live_money": False,
                    "mode": "PAPER_SHADOW", "next_expected_session": next_session,
                    **{key: record.get(key) for key in (
                        "ticker", "company", "isin", "source", "licensing", "classification", "score", "entry_zone",
                        "entry_reference", "stop", "target_1", "target_2", "risk_reward_t1", "risk_reward_t2",
                        "rank_version")}})
    if new:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a") as stream:
            for item in new:
                stream.write(json.dumps(item, sort_keys=True) + "\n")
    lifecycles = []
    for candidate in ledger + new:
        later = [b for b in bars_by_symbol.get(candidate["ticker"], []) if b.date > candidate["session"]]
        lifecycles.append({"candidate_id": candidate["candidate_id"], "ticker": candidate["ticker"],
                           "session": candidate["session"], "classification": candidate["classification"],
                           **simulate(candidate, later, slippage=US_SLIPPAGE, commission=US_COMMISSION)})
    return records, median, next_session, new, lifecycles


def run(*, data_root, state_dir, tv_python, tv_script, size=500, now=None, fetcher=None, fetch=fetch_stream):
    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    fetcher = fetcher or Fetcher(spacing={"query1.finance.yahoo.com": 1.0})
    universe = collect_universe(fetcher, data_root, size=size)
    from app.us import benchmarks as us_benchmarks
    benchmark_status = us_benchmarks.fetch_and_store(data_root, now=now, tv_python=tv_python, tv_script=tv_script,
                                                     fetch=fetch)
    spx_doc = fetch(SPX.symbol, python_path=tv_python, fetch_script=tv_script, n_bars=120)
    store_raw(data_root, "tradingview_us", json.dumps(spx_doc, sort_keys=True).encode(), "json")
    sessions = verify_sessions(completed_bars(parse_stream(spx_doc["raw"]), SPX, now=now), now=now)
    acquired = {member["ticker"]: acquire(member, data_root=data_root, now=now, tv_python=tv_python,
                                          tv_script=tv_script, expected=sessions, fetch=fetch)
                for member in universe["members"]}
    checks = cross_check(fetcher, data_root, acquired)
    records, median, next_session, new, lifecycles = rank(acquired, universe, sessions, now=now,
                                                          ledger_path=state_dir / "system-candidates.jsonl")
    status = Counter(item["status"] for item in acquired.values())
    fresh = Counter(r.get("freshness") or "UNKNOWN" for r in records)
    session = sessions["expected_session"]
    session_day = date.fromisoformat(session)
    report = {
        "schema": SCHEMA, "market": "US", "rank_version": US_PROFILE.version, "generated_at": now.isoformat(),
        "session": session, "based_on_session": session, "next_expected_session": next_session,
        "next_session_basis": "EXPECTED: NYSE rule calendar (Monday-Friday, published holidays)",
        "prepared_note": (f"Based on the {session_day.strftime('%A')} {session} NYSE close; for evaluation ahead of "
                          f"the next expected NYSE session, {date.fromisoformat(next_session).strftime('%A')} "
                          f"{next_session}."),
        "provider": US_DAILY_SOURCE["provider"], "admission": US_DAILY_SOURCE["status"],
        "licensing": US_DAILY_SOURCE["licensing"], "live_money": False,
        "counts": dict(Counter(r["classification"] for r in records)), "symbols_ranked": len(records),
        "universe_momentum_median_20_pct": median, "new_candidates": len(new),
        "universe": {k: v for k, v in universe.items() if k != "members"},
        "acquisition": dict(status), "freshness": dict(fresh), "sessions": sessions, "cross_check": checks,
        "benchmarks": benchmark_status, "snapshot_source": "TradingView scanner at retrieval time (no licence)",
        "costs": {"slippage": str(US_SLIPPAGE), "commission": str(US_COMMISSION)},
        "symbols": records, "lifecycles": lifecycles, "performance": performance(lifecycles)}
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "us-ranking.json"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--tv-python", required=True)
    parser.add_argument("--tv-script", default="tools/tradingview_fetch.py")
    parser.add_argument("--size", type=int, default=500)
    args = parser.parse_args(argv)
    for value in (args.data_root, args.state, args.tv_python):
        if not Path(value).is_absolute():
            parser.error("paths must be absolute")
    try:
        report = run(data_root=args.data_root, state_dir=args.state, tv_python=args.tv_python,
                     tv_script=str(Path(args.tv_script).resolve()), size=args.size)
    except (FetchError, SeriesError, ValueError, OSError) as exc:
        print(json.dumps({"status": "FAILED", "error": getattr(exc, "code", str(exc))[:200], "live_money": False}))
        return 1
    print(json.dumps({"status": "RANKED", "session": report["session"], "counts": report["counts"],
                      "acquisition": report["acquisition"], "freshness": report["freshness"],
                      "sessions": report["sessions"]["status"], "cross_check": report["cross_check"]["counts"],
                      "new_candidates": report["new_candidates"], "live_money": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
