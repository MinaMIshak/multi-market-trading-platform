"""Build the research context report (rates, FX, gold, Brent, indices, events, geopolitics).

python -m app.context.run --data-root <abs> --report <abs>/context-report.json \
    --macro-report <abs>/macro-context.json --tv-python <abs> [--tv-script tools/tradingview_fetch.py]

Market truth (EGX/US candidate data) is never read or written here. The
report is context evidence: each section carries its evidence class, source,
provenance and freshness. A failed source is UNAVAILABLE with its code. The
report is written atomically; exit 0 when at least one section is available.
LIVE_MONEY=DISABLED.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.context import official, regime
from app.context.fetch import Fetcher, store_raw
from app.context.series import reconcile, summarize
from app.context.tradingview_series import (CONTEXT_MARKETS, PROVIDER, RIGHTS, SeriesError, closes,
                                            completed_bars, current_trade_date, fetch_stream)
from app.data.providers.tradingview import parse_stream
from app.research import macro
from app.research.macro_fetch import fetch_all, write_report

SCHEMA = "context-report-v1"
SPACING = {"api.gdeltproject.org": 6.0, "data.sec.gov": 0.2, "www.sec.gov": 0.2}


def collect_markets(data_root, *, now, tv_python, tv_script, fetch=fetch_stream):
    sections, history = {}, {}
    for spec in CONTEXT_MARKETS:
        base = {"key": spec.key, "symbol": spec.symbol, "title": spec.title, "group": spec.group,
                "unit": spec.unit, "nature": spec.nature, "provider": PROVIDER, "rights": RIGHTS,
                "calendar": spec.calendar}
        try:
            document = fetch(spec.symbol, python_path=tv_python, fetch_script=tv_script)
            digest, _ = store_raw(data_root, "tradingview", json.dumps(document, sort_keys=True).encode(), "json")
            parsed = parse_stream(document["raw"])
            rows = completed_bars(parsed, spec, now=now)
        except (SeriesError, KeyError, ValueError) as exc:
            sections[spec.key] = {**base, "status": "UNAVAILABLE", "reason": getattr(exc, "code", str(exc)[:120])}
            continue
        observations = closes(rows)
        history[spec.key] = observations
        trade_date = current_trade_date(now, spec)
        summary = summarize(observations, as_of=trade_date, calendar=spec.calendar, unit=spec.unit,
                            current_trade_date=trade_date)
        sections[spec.key] = {**base, **summary, "raw_sha256": digest, "retrieved_at": now.isoformat(),
                              "data_vendor": (parsed["resolved"] or {}).get("provider_id"),
                              "latest_ohlc": rows[-1] if rows else None, "stream_errors": parsed["errors"]}
    return sections, history


def reconciliations(markets, history, macro_report, macro_obs, er_api, bis):
    out = []
    gold, gold_tvc = history.get("XAUUSD"), history.get("GOLD_TVC")
    out.append({"check": "Gold spot: OANDA vs TVC composite (same date close)", "tolerance": "0.5%",
                **reconcile(gold[-1] if gold else None, gold_tvc[-1] if gold_tvc else None,
                            tolerance_pct=Decimal("0.5"))})
    usdegp = history.get("USDEGP")
    reference = ((date.fromisoformat(er_api["date"]), Decimal(er_api["usd_egp"]))
                 if er_api.get("status") == "AVAILABLE" else None)
    out.append({"check": "USD/EGP: ICE market close vs ExchangeRate-API reference", "tolerance": "1.0%",
                "note": "reference is an aggregated mid published at its own time; dates may differ by a day",
                **reconcile(usdegp[-1] if usdegp else None, reference, tolerance_pct=Decimal("1.0"),
                            same_date_required=False)})
    upper, lower = macro_obs.get("DFEDTARU") or [], macro_obs.get("DFEDTARL") or []
    midpoint = None
    if upper and lower and upper[-1][0] == lower[-1][0]:
        midpoint = (upper[-1][0], (upper[-1][1] + lower[-1][1]) / 2)
    bis_latest = ((date.fromisoformat(bis["latest"][0]), Decimal(bis["latest"][1]))
                  if bis.get("status") == "AVAILABLE" else None)
    out.append({"check": "Fed target midpoint: FRED (Fed Board) vs BIS policy-rate series", "tolerance": "0%",
                **reconcile(midpoint, bis_latest, tolerance_pct=Decimal("0"), same_date_required=False)})
    brent = history.get("UKOIL")
    spot = next((row for row in macro_report["series"] if row["series_id"] == "DCOILBRENTEU"), {})
    gap = None
    if brent and spot.get("status") == "AVAILABLE":
        same = dict(brent).get(date.fromisoformat(spot["latest_date"]))
        if same is not None:
            gap = str(((same / Decimal(spot["latest_value"]) - 1) * 100).quantize(Decimal("0.01")))
    out.append({"check": "Brent: front-month futures (UKOIL) vs EIA dated spot", "status": "REPORTED_NOT_RECONCILED",
                "note": "different instruments (futures vs physical spot); the gap is reported, never forced to agree",
                "eia_spot_date": spot.get("latest_date"), "futures_minus_spot_pct_same_date": gap})
    return out


def run(*, data_root, report_path, macro_report_path, tv_python, tv_script, now=None, fetcher=None,
        market_fetch=fetch_stream, egx_collect=official.collect_egx, fred_get=None):
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fetcher = fetcher or Fetcher(spacing=SPACING)
    fred = fetch_all(data_root, today=today, now=lambda: now, **({"get": fred_get} if fred_get else {}))
    macro_report = macro.build_report(fred, as_of=today, generated_at=now)
    write_report(macro_report_path, macro_report)
    macro_obs = {}
    for key, item in fred.items():
        if "text" in item:
            try:
                macro_obs[key] = macro.parse_fred_csv(item["text"], key)[0]
            except macro.MacroParseError:
                pass
    markets, history = collect_markets(data_root, now=now, tv_python=tv_python, tv_script=tv_script,
                                       fetch=market_fetch)
    er_api = official.collect_er_api(fetcher, data_root)
    bis = official.collect_bis_us(fetcher, data_root, today=today)
    imf = official.collect_imf_egypt(fetcher, data_root, today=today)
    fed = official.collect_fed_press(fetcher, data_root)
    disclosures, financials = egx_collect(data_root)
    portwatch = official.collect_portwatch(fetcher, data_root, today=today)
    ofac = official.collect_ofac(data_root, fetcher=fetcher)
    gdelt = official.collect_gdelt(fetcher, data_root)
    cross_input = {**history, **{k: macro_obs[k] for k in ("DTWEXBGS", "DGS10", "VIXCLS") if k in macro_obs}}
    report = {
        "schema": SCHEMA, "generated_at": now.isoformat(), "as_of": today.isoformat(), "live_money": False,
        "use": "CONTEXT_ONLY_NOT_A_SIGNAL", "regime_version": regime.VERSION,
        "markets": markets, "macro": {"as_of": macro_report["as_of"], "series": macro_report["series"],
                                      "derived": macro_report["derived"]},
        "reconciliation": reconciliations(markets, history, macro_report, macro_obs, er_api, bis),
        "rates": {"egypt": imf, "fed_policy_verification": bis, "fed_releases": fed},
        "fx_reference": er_api,
        "events": {"egx_disclosures": disclosures, "fed_releases": fed},
        "fundamentals": {"egx_financial_statements": financials,
                         "us_sec": official.sec_status(official.sec_contact())},
        "geopolitics": {"shipping": portwatch, "sanctions": ofac, "narrative": gdelt},
        "cross_market": regime.cross_market(cross_input),
        "regimes": regime.build_regimes(history, macro_obs, portwatch),
        "fetch_log": fetcher.log[-200:],
    }
    write_report(report_path, report)
    return report


def section_status(report):
    flat = {**{f"market:{k}": v for k, v in report["markets"].items()},
            "fx_reference": report["fx_reference"], "rates:egypt": report["rates"]["egypt"],
            "rates:bis": report["rates"]["fed_policy_verification"], "fed_releases": report["rates"]["fed_releases"],
            "egx_disclosures": report["events"]["egx_disclosures"],
            "egx_financials": report["fundamentals"]["egx_financial_statements"],
            "shipping": report["geopolitics"]["shipping"], "sanctions": report["geopolitics"]["sanctions"],
            "narrative": report["geopolitics"]["narrative"], "us_sec": report["fundamentals"]["us_sec"]}
    return {key: value.get("status") for key, value in flat.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--macro-report", required=True)
    parser.add_argument("--tv-python", required=True)
    parser.add_argument("--tv-script", default="tools/tradingview_fetch.py")
    args = parser.parse_args(argv)
    for value in (args.data_root, args.report, args.macro_report, args.tv_python):
        if not Path(value).is_absolute():
            parser.error("paths must be absolute")
    report = run(data_root=args.data_root, report_path=args.report, macro_report_path=args.macro_report,
                 tv_python=args.tv_python, tv_script=str(Path(args.tv_script).resolve()))
    statuses = section_status(report)
    print(json.dumps({"context_sections": statuses, "live_money": False}, sort_keys=True))
    return 0 if any(value == "AVAILABLE" for value in statuses.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
