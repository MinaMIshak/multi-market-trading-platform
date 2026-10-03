# Source decision matrix (2026-10-03)

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. Personal, non-commercial research
platform. No paid subscription was added.

**Method:** every candidate below was probed live from the platform host on
2026-10-03, anonymously. Results show the HTTP outcome, the format and the
latest observation seen. Rights are taken from the publisher's public status.
No contractual licence is claimed for any third-party market-data source.

**Labels:**
- `OFFICIAL_PUBLIC`: a government, central-bank, exchange or regulator
  publication.
- `OPEN_DATA`: published for reuse with attribution.
- `NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`: an unofficial or anonymous
  client of a commercial site, used for personal research under the operator's
  standing decision (2026-10-01, extended by the 2026-10-03 mission to the
  domains listed here).

**Roles:** PRIMARY is the best source for the observation. VERIFICATION is an
independent source used for reconciliation. FALLBACK is used only where it is
evidentially equivalent.

## 1. US market

| Need | Candidate | Probe result | Official | Auth | Depth / freshness | Decision |
|---|---|---|---|---|---|---|
| Listing master | Nasdaq Trader symbol directory (`nasdaqlisted.txt`, `otherlisted.txt`) | 200, pipe text, updated daily | yes (Nasdaq, all US listings incl. NYSE/AMEX) | none | current only | **PRIMARY** security master (listing, ETF and test flags, exchange) |
| Listing master | SEC `company_tickers_exchange.json` | 200 with a contact User-Agent; **403 without an e-mail contact** | yes (SEC) | contact UA | current only | **VERIFICATION** (ticker↔CIK); BLOCKED until the operator sets a contact |
| Liquidity screen | TradingView scanner (`scanner.tradingview.com/america/scan`) | 200, 5,008 primary common stocks, 30-day volume, close, ISIN, sector | no | none | current snapshot | **PRIMARY** for selecting the research universe (top N by 30-day $ volume), cross-checked against Nasdaq Trader membership |
| Daily OHLCV | TradingView (tvdatafeed protocol; ICE vendor) | 200 for NASDAQ/NYSE/AMEX, America/New_York, ISIN | no | none | years of daily bars; last completed session | **PRIMARY** (already in production for EGX, with the same raw-evidence pipeline) |
| Daily OHLCV | Yahoo chart API (`query1.finance.yahoo.com/v8/finance/chart`) | 200 JSON | no | none | years | **VERIFICATION** (sampled close cross-check, report-only) |
| Daily OHLCV | Stooq CSV | JavaScript bot challenge | no | n/a | n/a | rejected: not machine-accessible |
| Daily OHLCV | api.nasdaq.com historical | timeout without browser emulation | no | n/a | n/a | rejected |
| Daily OHLCV | Alpha Vantage, Tiingo, Polygon, EODHD, Twelve Data | API key or paid tier for this volume | no | key | n/a | rejected: key creation or payment is a user-only action, and a free alternative exists |
| Session calendar | NYSE holiday rules (published annually by NYSE) plus observed SPY/SPX bars | the rules are deterministic; `nyse.com` has no machine API (404) | rules yes | none | rules forward; observation backward | **PRIMARY** = NYSE rule set (`app/us/nyse_calendar.py`); **VERIFICATION** = a session counts as verified only if index bars exist; unscheduled closures come from observation |
| Corporate actions | TradingView split-adjusted series | adjustment `splits` recorded | no | none | n/a | **PARTIAL**: splits are embedded in prices, dividends are not modelled; a raw corporate-action ledger is BLOCKED (no free official feed) |

## 2. Gold

| Candidate | Probe | Status | Decision |
|---|---|---|---|
| OANDA XAUUSD via TradingView | 200, NY 17:00 roll, latest 2026-10-02 session | spot OTC | **PRIMARY** (market spot) |
| TVC:GOLD via TradingView | 200, independent composite | spot composite | **VERIFICATION** (close tolerance 0.5 %) |
| World Bank Pink Sheet (CMO monthly) | 200 XLSX, CC BY 4.0 | official-ish monthly average | **REFERENCE** (monthly, lagged; shown separately) |
| LBMA auction price | licensed | – | rejected: licensed |
| FRED gold | withdrawn (series discontinued) | – | rejected |
| Stooq XAUUSD | bot challenge | – | rejected |

## 3. Brent / oil

| Candidate | Probe | Decision |
|---|---|---|
| EIA Brent spot (FRED `DCOILBRENTEU`) | 200, official, ~4-day publication lag | **OFFICIAL REFERENCE** (dated Brent spot), already live |
| TVC:UKOIL via TradingView | 200, front-month futures continuous, Europe/London | **PRIMARY market quote** (latest completed session). It is a futures reference, so it differs from dated spot. The two are reported side by side, and the gap is reported but never reconciled away |

## 4. FX

| Need | Candidate | Probe | Decision |
|---|---|---|---|
| USD/EGP market | FX_IDC:USDEGP via TradingView (ICE) | 200, UTC 22:00 roll | **PRIMARY market rate** |
| USD/EGP reference | open.er-api.com (ExchangeRate-API open access, attribution required) | 200, daily, includes EGP | **VERIFICATION** (aggregated mid reference; not official) |
| USD/EGP official | Central Bank of Egypt (`cbe.org.eg`) | WAF "Request Rejected" for automated access | **BLOCKED**: not circumvented |
| USD/EGP | Frankfurter / ECB | no EGP | rejected for EGP |
| Dollar context | Fed H.10 broad index (FRED `DTWEXBGS`), EUR/USD (`DEXUSEU`) | 200, official | **PRIMARY**, already live |
| Dollar context | TVC:DXY | resolves, returns 0 bars | rejected |

## 5. Rates and monetary policy

| Need | Candidate | Probe | Decision |
|---|---|---|---|
| Fed target range | FRED `DFEDTARU` / `DFEDTARL` (Fed Board) | 200 | **PRIMARY** |
| Fed effective rate, Treasury curve | FRED `DFF`, `DGS2`, `DGS10` | 200 | **PRIMARY**, already live |
| Fed policy cross-check | BIS central bank policy rates (`WS_CBPOL`, D.US) | 200 | **VERIFICATION** |
| FOMC decisions / meetings | Fed press-release JSON (`federalreserve.gov/json/ne-press.json`) | 200, official | **PRIMARY** decision evidence (monetary-policy press releases with timestamps) |
| CBE policy rate | `cbe.org.eg` | WAF block | **BLOCKED** (current) |
| CBE rate (lagged) | IMF IFS `MFS_IR` EGY discount rate (`DISR_RT_PT_A_PT`, monthly) | 200, latest 2025-M08 | **OFFICIAL REFERENCE, STALE by design** (shown with its age) |
| CBE rate | BIS `WS_CBPOL` D.EG | 404 (Egypt not covered) | rejected |

## 6. News, disclosures and catalysts

| Need | Candidate | Probe | Decision |
|---|---|---|---|
| EGX company disclosures | EGX official BFF `news-search` (`beta.egx.com.eg`, the same public API family as the admitted market-watch capture) | 200 JSON, 1,788 items, ISIN and Reuters code, timestamps, PDF links | **PRIMARY** (official exchange) |
| EGX regulator decrees | the same feed (FRA decrees, "General") | 200 | **PRIMARY** |
| US company filings | SEC EDGAR submissions / 8-K Atom feed | 200 only with an e-mail contact UA | **PRIMARY when configured; BLOCKED until the operator sets the SEC contact** |
| Central bank releases | Fed press JSON / RSS | 200 | **PRIMARY** |
| Headlines / attention | GDELT DOC 2.0 API (open, citation) | 429 at burst; needs ≥ 5 s spacing | **CONTEXT (narrative)**, bounded queries with backoff; never a signal |
| egx.com.eg legacy news | connection dropped | – | rejected (the beta API is used) |

## 7. Fundamentals

| Need | Candidate | Probe | Decision |
|---|---|---|---|
| EGX | EGX official `financial-statements-filter` (period, net profit, comparative, audit status, currency, units) | 200 JSON | **PRIMARY** (structured exchange disclosures; parsed fields only, no derived valuation without price-consistent share counts) |
| US | SEC XBRL `companyfacts` / `frames` | 200 with an e-mail contact UA, 403 otherwise | **PRIMARY when configured; BLOCKED until the operator sets the SEC contact** |

## 8. Geopolitical and macro-event evidence

| Need | Candidate | Probe | Decision |
|---|---|---|---|
| Suez / Red Sea shipping | IMF PortWatch daily chokepoints (ArcGIS FeatureServer) | 200 JSON, daily transit calls for Suez Canal and Bab el-Mandeb | **PRIMARY structured evidence** |
| Sanctions | OFAC SDN CSV (Treasury, official) | 200 (~5 MB) | **PRIMARY** (publication counts and program tags; no entity screening claims) |
| Event narrative | GDELT (above) | rate-limited | **CONTEXT (narrative)**, clearly separated from structured facts |
| Egypt macro | World Bank API (CPI etc.) | 200, annual | **REFERENCE** (annual, lagged) |

## 9. Cross-market

Derived only from stored series above (no new source): rolling 60-session
return correlations and 20-session co-movement between EGX30 (TradingView
EGX:EGX30), SPX, gold, Brent (UKOIL), USD/EGP, the broad dollar index and US
10-year yields. Pairs are aligned on common dates only, with no
forward-filling. Each relationship states its sample size and window and is
labelled descriptive (not causal, not predictive).

## User-only actions

Exactly one, optional: an e-mail contact for the SEC fair-access User-Agent
(`EGX_SEC_CONTACT`, see `docs/PROVIDER_ARCHITECTURE.md`). Without it, the SEC
listing verification, US filings and US fundamentals stay BLOCKED and say so.
Everything else above needs no account, key, payment or click-through.
